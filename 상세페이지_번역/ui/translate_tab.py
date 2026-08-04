# ==========================================================
# 메인 작업 화면 (ui/translate_tab.py)
# ----------------------------------------------------------
# 이 프로그램은 번역 자체는 대신 해주지 않습니다 (OpenAI API 비용을 쓰지 않기로
# 했기 때문). 대신 아래 순서로 작업을 도와줍니다.
#
#   1단계) 쿠팡/네이버 스마트스토어 같은 국내 오픈마켓 상품 URL에서 원본 이미지를
#          자동으로 모아오거나, 이미 가진 이미지를 업로드합니다. 이미지는
#          "상세페이지 이미지"와 "옵션(색상/사이즈 등) 이미지"로 나눠서 보여주고,
#          각각 원하는 것만 골라 다운로드할 수 있습니다.
#   (사용자가 직접) 다운로드한 상세페이지 이미지를 챗지피티에 올려서
#          "한국어로 번역해줘"라고 요청하고, 결과 이미지를 저장해둡니다.
#   2단계) 번역된 이미지들을 다시 이 프로그램에 업로드하면, 쿠팡 상세페이지
#          이미지 규격(가로 780px, 세로 3000px 이하, JPG, 5MB 이하)에 맞게
#          자동으로 크기를 조절/분할해서 최종 파일로 만들어줍니다.
# ==========================================================

import io
import os
import tempfile
import zipfile
from datetime import datetime

import streamlit as st

import config
import crawler
import image_resize

# 이미지 묶음 두 종류를 같은 방식으로 다루기 위한 이름입니다.
# session_state에는 "{group}_images", "{group}_include_flags" 형태로 저장됩니다.
DETAIL_GROUP = "detail"
OPTION_GROUP = "option"
GROUP_LABELS = {DETAIL_GROUP: "상세페이지 이미지", OPTION_GROUP: "옵션 이미지"}


def _work_dir() -> str:
    """이번 브라우저 세션 동안 이미지를 임시로 담아둘 폴더입니다."""
    if "work_dir" not in st.session_state:
        st.session_state["work_dir"] = tempfile.mkdtemp(prefix="detail_translate_")
    return st.session_state["work_dir"]


def _save_uploaded_files(uploaded_files, prefix: str) -> list[str]:
    work_dir = _work_dir()
    paths = []
    for i, uploaded in enumerate(uploaded_files, start=1):
        ext = os.path.splitext(uploaded.name)[1] or ".png"
        path = os.path.join(work_dir, f"{prefix}_{i:02d}{ext}")
        with open(path, "wb") as f:
            f.write(uploaded.getbuffer())
        paths.append(path)
    return paths


def _mime_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
        ext, "application/octet-stream"
    )


def _build_zip(paths: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            zf.write(path, os.path.basename(path))
    return buffer.getvalue()


def _set_group_images(group: str, paths: list[str]):
    st.session_state[f"{group}_images"] = paths
    st.session_state[f"{group}_include_flags"] = {p: True for p in paths}


def _parse_tagged_lines(text: str) -> tuple[list[str], list[str]]:
    """"D:"(상세페이지)/"O:"(옵션) 태그가 붙은 줄들을 두 목록으로 나눕니다.

    태그가 없는 그냥 http(s) 주소는(예: 예전 버전 즐겨찾기, 예전 콘솔 스크립트로 복사한 경우)
    호환을 위해 상세페이지 이미지로 취급합니다.
    """
    detail_urls, option_urls = [], []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("D:"):
            detail_urls.append(line[2:])
        elif line.startswith("O:"):
            option_urls.append(line[2:])
        elif line.startswith("http://") or line.startswith("https://"):
            detail_urls.append(line)
    return detail_urls, option_urls


def _download_and_store(detail_urls, option_urls, referer, status_label="이미지"):
    """상세/옵션 주소 목록을 내려받아 session_state에 저장합니다. 하나가 실패해도 나머지는 진행합니다."""
    total = len(detail_urls) + len(option_urls)
    if not total:
        st.error("찾은 이미지 주소가 없습니다.")
        return

    with st.spinner(f"{status_label} {total}개를 내려받는 중입니다..."):
        detail_paths, option_paths = [], []
        detail_error = option_error = None

        if detail_urls:
            try:
                detail_paths = crawler.download_images(
                    detail_urls, referer=referer, out_dir=_work_dir(), prefix="detail"
                )
            except crawler.CrawlError as exc:
                detail_error = exc
        if option_urls:
            try:
                option_paths = crawler.download_images(
                    option_urls, referer=referer, out_dir=_work_dir(), prefix="option"
                )
            except crawler.CrawlError as exc:
                option_error = exc

        if detail_paths:
            _set_group_images(DETAIL_GROUP, detail_paths)
        if option_paths:
            _set_group_images(OPTION_GROUP, option_paths)

        if detail_paths or option_paths:
            st.success(f"상세페이지 이미지 {len(detail_paths)}장, 옵션 이미지 {len(option_paths)}장을 가져왔습니다.")
        if detail_error and not detail_paths:
            st.error(f"상세페이지 이미지: {detail_error}")
        if option_error and not option_paths:
            st.error(f"옵션 이미지: {option_error}")


def _consume_paste_query_param():
    """즐겨찾기(북마클릿)나 콘솔 스크립트가 이미지 주소를 담아 이 앱을 새 탭으로 열어주면,
    그 주소들을 쿼리 파라미터(?paste=...)에서 읽어와 자동으로 다운로드까지 끝냅니다.
    사람이 복사 → 프로그램으로 전환 → 붙여넣기 하는 수고를 없애기 위함입니다.
    """
    pasted = st.query_params.get("paste")
    if not pasted:
        return
    # 쿼리 파라미터를 지워서, 새로고침하거나 다른 조작을 해도 같은 요청이 반복되지 않게 합니다.
    del st.query_params["paste"]

    detail_urls, option_urls = _parse_tagged_lines(pasted)
    _download_and_store(detail_urls, option_urls, referer="", status_label="이미지")


def _render_source_tabs():
    tab_url, tab_console, tab_upload = st.tabs(["URL로 가져오기", "브라우저에서 주소 복사", "직접 업로드"])

    with tab_url:
        product_url = st.text_input(
            "상품 URL",
            placeholder="쿠팡 / 네이버 스마트스토어 상품 페이지 주소를 붙여넣으세요",
            key="product_url_input",
        )

        fetch_clicked = st.button("이미지 가져오기", type="primary", width="stretch")

        if fetch_clicked:
            if not product_url.strip():
                st.error("URL을 입력해주세요.")
            else:
                with st.spinner("상세페이지에 접속해서 이미지를 찾는 중입니다... (최대 1분 정도 걸릴 수 있어요)"):
                    try:
                        found = crawler.fetch_detail_images(product_url)
                    except crawler.CrawlError as exc:
                        st.error(str(exc))
                        if exc.page_title:
                            st.caption(f"크롤러가 실제로 본 페이지 제목: \"{exc.page_title}\"")
                        if exc.screenshot_bytes:
                            st.image(
                                exc.screenshot_bytes,
                                caption="크롤러가 실제로 본 화면 (접속이 막혔는지 확인해보세요)",
                                width="stretch",
                            )
                    else:
                        _download_and_store(found["detail"], found["option"], referer=product_url)
        st.caption("쿠팡처럼 접속을 막는 사이트는 실패할 수 있습니다. 그럴 땐 '브라우저에서 주소 복사' 탭을 이용해주세요.")

    with tab_console:
        st.markdown(
            "쿠팡처럼 자동 접속을 막는 사이트는 이 방법을 쓰세요. **사용자님이 직접 여는 화면**이라 차단되지 않습니다.\n\n"
            "**이제는 복사/붙여넣기도 필요 없습니다.** 즐겨찾기를 클릭하면 이 프로그램이 새 탭으로 자동으로 열리면서 "
            "이미지도 바로 다운로드됩니다."
        )

        st.markdown("#### 방법 A. 즐겨찾기로 등록 (한 번만 설정하면 그다음부턴 클릭 한 번, 추천)")
        st.markdown(
            "**이전에 이미 즐겨찾기를 만드셨다면**, 그 즐겨찾기를 다시 수정해서 아래 새 코드로 URL만 교체해주세요 "
            "(상세페이지/옵션 이미지를 나눠서 가져오는 새 버전입니다).\n\n"
            "1. 즐겨찾기 아무거나 하나 추가합니다 (아무 페이지에서 **Ctrl+D** → 저장).\n"
            "2. 즐겨찾기 모음(북마크 바)에서 방금 추가한 즐겨찾기를 **마우스 오른쪽 클릭 → 수정(편집)**을 누릅니다. "
            "(작은 창에 URL 칸이 없으면 **Ctrl+Shift+O**로 즐겨찾기 관리자를 열어서 거기서 수정하세요.)\n"
            "3. 이름은 \"이미지 주소 가져오기\" 등 아무거나로 바꾸고, **URL(주소) 칸의 내용을 전부 지운 뒤** "
            "아래 코드를 복사해서 통째로 붙여넣고 저장합니다.\n"
            "4. 앞으로는 쿠팡 상품 페이지를 열고 이 즐겨찾기를 **클릭 한 번만** 하면, 이미지가 자동으로 이 프로그램에 채워집니다."
        )
        st.code(crawler.build_bookmarklet_href(), language=None)

        with st.expander("방법 B. 개발자도구 콘솔에 직접 붙여넣기 (조금 더 어려움)"):
            st.markdown(
                "1. 평소 쓰시는 브라우저(크롬 등)로 상품 페이지를 엽니다.\n"
                "2. 키보드 **F12**를 누르고 **Console(콘솔)** 탭을 클릭합니다.\n"
                "3. 콘솔 맨 아래, **아무 글자도 없고 커서만 깜빡이는 빈 줄**을 클릭합니다 "
                "(로그 목록과는 다른 줄입니다. 안 보이면 콘솔 창을 아래로 스크롤하거나, 뜬 안내창을 닫아주세요).\n"
                "4. 아래 코드를 복사해서 그 빈 줄에 붙여넣고 **Enter**를 누릅니다.\n"
                "5. **팝업창**이 뜨고 안에 이미지 주소들이 이미 선택되어 있습니다. **Ctrl+C**(맥은 Cmd+C)로 복사한 뒤 "
                "**확인**을 누르세요."
            )
            st.code(crawler.build_browser_console_script(), language="javascript")

        st.markdown("팝업에서 복사한 내용을 아래에 붙여넣어주세요.")

        console_referer = st.text_input(
            "상품 URL (선택 사항, 이미지 다운로드에 참고용으로만 사용)",
            key="console_referer_input",
        )
        pasted_urls = st.text_area(
            "복사한 이미지 주소들을 여기에 붙여넣으세요 (한 줄에 하나씩)",
            height=150,
            key="pasted_urls_input",
        )

        if st.button("이 주소들로 이미지 가져오기", type="primary"):
            if not pasted_urls.strip():
                st.error("붙여넣은 내용이 없습니다.")
            else:
                detail_urls, option_urls = _parse_tagged_lines(pasted_urls)
                if not detail_urls and not option_urls:
                    st.error(
                        "붙여넣은 내용이 이미지 주소처럼 보이지 않습니다. "
                        "콘솔에서 코드를 실행하면 뜨는 **팝업창 안의 내용**을 복사했는지 확인해주세요. "
                        "콘솔 화면 자체를 복사하신 거라면 다른 로그가 섞여 들어옵니다."
                    )
                else:
                    _download_and_store(detail_urls, option_urls, referer=console_referer.strip())

    with tab_upload:
        uploaded_files = st.file_uploader(
            "원본 이미지 파일들을 선택하세요",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key="source_uploader",
        )
        if uploaded_files and st.button("업로드한 이미지 사용", type="primary"):
            paths = _save_uploaded_files(uploaded_files, prefix="detail")
            _set_group_images(DETAIL_GROUP, paths)


def _render_image_group(group: str, label: str | None = None, caption: str | None = None) -> list[str]:
    """이미지 묶음 하나(상세페이지/옵션/변환결과 등)를 그리고, 선택된 경로 목록을 반환합니다.

    group은 session_state 키의 접두어로 쓰입니다 (예: "detail" → "detail_images").
    label/caption을 안 넘기면 DETAIL_GROUP/OPTION_GROUP 기본값을 씁니다.
    """
    paths = st.session_state.get(f"{group}_images", [])
    if not paths:
        return []

    if label is None:
        label = GROUP_LABELS.get(group, group)
    if caption is None:
        if group == DETAIL_GROUP:
            caption = "챗지피티 번역에 사용할 이미지입니다. 상세페이지가 아닌 이미지가 섞였다면 체크를 해제해주세요."
        elif group == OPTION_GROUP:
            caption = "색상/사이즈 등 옵션 선택용 이미지입니다. 필요할 때만 다운로드해서 쓰세요."

    st.markdown(f"#### {label} ({len(paths)}장)")
    if caption:
        st.caption(caption)

    include_flags = st.session_state.setdefault(f"{group}_include_flags", {p: True for p in paths})

    col_all, col_none, col_zip = st.columns([1, 1, 2])
    with col_all:
        if st.button("전체 선택", key=f"{group}_select_all", width="stretch"):
            for p in paths:
                include_flags[p] = True
                # 체크박스는 key가 있는 위젯이라 한 번 그려지고 나면 session_state에 있는
                # 값이 우선시됩니다. include_flags만 바꾸면 화면에 반영되지 않아서,
                # 체크박스의 session_state 값도 함께 직접 갱신해줘야 합니다.
                st.session_state[f"{group}_include_{p}"] = True
            st.rerun()
    with col_none:
        if st.button("전체 해제", key=f"{group}_select_none", width="stretch"):
            for p in paths:
                include_flags[p] = False
                st.session_state[f"{group}_include_{p}"] = False
            st.rerun()

    selected = [p for p in paths if include_flags.get(p, True)]
    with col_zip:
        if selected:
            st.download_button(
                f"선택한 {label} {len(selected)}장 전체 다운로드 (zip)",
                data=_build_zip(selected),
                file_name=f"{label}.zip",
                mime="application/zip",
                key=f"{group}_zip_download",
                width="stretch",
            )

    columns = st.columns(4)
    for i, path in enumerate(paths):
        col = columns[i % 4]
        with col:
            st.image(path, width=180, caption=f"{i + 1}번째")
            include_flags[path] = st.checkbox(
                "포함", value=include_flags.get(path, True), key=f"{group}_include_{path}"
            )
            with open(path, "rb") as f:
                st.download_button(
                    "다운로드",
                    data=f.read(),
                    file_name=f"{i + 1:02d}_{os.path.basename(path)}",
                    mime=_mime_for(path),
                    key=f"{group}_download_{path}",
                    width="stretch",
                )

    return selected


def _render_step1_collect():
    st.subheader("1단계. 원본 이미지 모으기")

    _consume_paste_query_param()

    _render_source_tabs()

    has_any = st.session_state.get(f"{DETAIL_GROUP}_images") or st.session_state.get(f"{OPTION_GROUP}_images")
    if not has_any:
        return

    st.divider()
    _render_image_group(DETAIL_GROUP)
    st.divider()
    _render_image_group(OPTION_GROUP)


def _render_step2_convert():
    st.divider()
    st.subheader("2단계. 번역된 이미지 업로드 → 쿠팡 규격으로 변환")
    st.caption(
        "챗지피티에서 받은 한국어 번역 이미지들을 아래에 올려주세요. "
        "쿠팡 상세페이지 규격(가로 780px, 세로 3000px 이하, JPG, 장당 5MB 이하)에 맞게 자동으로 조절/분할됩니다. "
        "업로드한 순서대로 번호가 매겨집니다."
    )

    with st.expander("챗지피티가 여러 장을 한 장으로 합쳐서 줬나요? 여기서 낱장으로 나누세요"):
        st.caption(
            "예를 들어 6장을 번역해달라고 했는데 결과가 2×3 격자로 합쳐진 이미지 한 장으로 왔다면, "
            "여기에 그 이미지를 올리고 가로/세로 칸 수를 입력하면 원래대로 낱장으로 나눠줍니다."
        )
        grid_file = st.file_uploader(
            "합쳐진 이미지 업로드",
            type=["png", "jpg", "jpeg", "webp"],
            key="grid_uploader",
        )
        col_cols, col_rows = st.columns(2)
        with col_cols:
            grid_cols = st.number_input("가로 칸 수(열)", min_value=1, max_value=10, value=2, key="grid_cols")
        with col_rows:
            grid_rows = st.number_input("세로 칸 수(행)", min_value=1, max_value=10, value=3, key="grid_rows")

        if grid_file and st.button("낱장으로 나누기", key="grid_split_btn"):
            pieces = image_resize.split_grid_image(grid_file.getvalue(), rows=int(grid_rows), cols=int(grid_cols))
            st.session_state["step2_extra_pieces"] = pieces
            st.success(f"{len(pieces)}장으로 나눴습니다. 미리보기를 확인하고, 아래 '쿠팡 규격으로 변환' 버튼을 누르면 함께 변환됩니다.")

        extra_pieces = st.session_state.get("step2_extra_pieces")
        if extra_pieces:
            piece_columns = st.columns(4)
            for i, piece in enumerate(extra_pieces):
                with piece_columns[i % 4]:
                    st.image(piece, width=140, caption=f"{i + 1}번째")
            if st.button("나눈 조각 지우기", key="grid_clear_btn"):
                st.session_state.pop("step2_extra_pieces", None)
                st.rerun()

    target_width = st.number_input(
        "결과 이미지 가로 폭(px)",
        min_value=500,
        max_value=1000,
        value=image_resize.RECOMMENDED_WIDTH,
        step=10,
        help="쿠팡 권장 가로 폭은 780px입니다(최대 1000px).",
    )

    translated_files = st.file_uploader(
        "번역된 이미지 파일들을 선택하세요 (낱장으로 이미 나뉜 이미지가 있다면 여기에)",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="translated_uploader",
    )

    extra_pieces = st.session_state.get("step2_extra_pieces", [])
    all_inputs = [f.getvalue() for f in translated_files] + extra_pieces

    if all_inputs and st.button("쿠팡 규격으로 변환", type="primary"):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(config.OUTPUT_DIR, timestamp)
        os.makedirs(out_dir, exist_ok=True)

        result_paths = []
        counter = 1
        for image_bytes in all_inputs:
            chunks = image_resize.resize_for_coupang(image_bytes, target_width=target_width)
            for chunk_bytes in chunks:
                path = os.path.join(out_dir, f"{counter:02d}.jpg")
                with open(path, "wb") as f:
                    f.write(chunk_bytes)
                result_paths.append(path)
                counter += 1

        _set_group_images("converted", result_paths)
        st.session_state["converted_dir"] = out_dir

    if st.session_state.get("converted_images"):
        st.success(f"변환 완료 (저장 위치: {st.session_state.get('converted_dir', '')})")
        st.divider()
        _render_image_group(
            "converted",
            label="변환된 이미지",
            caption="쿠팡 규격 변환이 끝난 최종 이미지입니다. 필요한 것만 선택해서 다운로드하세요.",
        )


def render():
    _render_step1_collect()
    _render_step2_convert()
