"""벤치마킹 소싱 페이지 — 1단계: 국내 기준상품 분석과 확인."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from benchmark_analyzer import BenchmarkAnalysisError, analyze_benchmark_product
from collector.taobao_candidate_search import search_candidates, suggest_chinese_query


def run_in_worker(function, *args, **kwargs):
    """Playwright 동기 API를 Streamlit 실행 스레드와 분리한다."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(function, *args, **kwargs).result()


def keyword_candidates(title: str) -> list[str]:
    """검수용 키워드 후보를 보수적으로 추출한다. 최종 분류는 사용자가 한다."""
    stopwords = {
        "무료배송", "당일배송", "오늘출발", "국내배송", "해외배송", "정품", "특가",
        "추천", "인기", "신상", "할인", "증정", "세일", "공식", "스토어",
    }
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", title or "")
    result = []
    for token in tokens:
        if len(token) < 2 or token in stopwords or token.isdigit() or token in result:
            continue
        result.append(token)
    return result[:20]


st.session_state.setdefault("benchmark_product", None)
st.session_state.setdefault("benchmark_confirmed", False)
st.session_state.setdefault("taobao_candidates", [])
st.session_state.setdefault("taobao_search_report", None)

st.title("벤치마킹 소싱")
st.caption("국내에서 잘 팔리는 상품 URL을 분석한 뒤 타오바오 공급상품 후보를 찾습니다.")

with st.container(border=True):
    st.subheader("1. 기준상품 분석")
    st.write("스마트스토어·네이버쇼핑·쿠팡 상품 주소를 넣어 주세요.")

    with st.form("benchmark_url_form", border=False):
        product_url = st.text_input(
            "국내 상품 URL",
            placeholder="https://smartstore.naver.com/.../products/...",
            key="benchmark_url",
        )
        wait_seconds = st.slider(
            "보안확인 대기시간",
            min_value=60,
            max_value=600,
            value=300,
            step=30,
            format="%d초",
            help="보안확인이 뜨면 열린 Chrome 창에서 직접 완료하세요. 창은 이 시간 동안 유지됩니다.",
        )
        analyze_clicked = st.form_submit_button(
            "기준상품 분석",
            type="primary",
            icon=":material/manage_search:",
        )

    st.info(
        "분석 중 Chrome 창이 열립니다. 보안확인이 나오면 그 창에서 처리해 주세요. "
        "인증 뒤 상품명과 대표이미지가 확인될 때까지 창을 닫지 않습니다.",
        icon=":material/info:",
    )

if analyze_clicked:
    st.session_state.benchmark_product = None
    st.session_state.benchmark_confirmed = False
    st.session_state.taobao_candidates = []
    st.session_state.taobao_search_report = None
    try:
        with st.status("기준상품을 분석하고 있습니다...", expanded=True) as status:
            st.write("국내 상품 페이지를 여는 중입니다.")
            st.write("보안확인이 표시되면 열린 Chrome 창에서 인증해 주세요.")
            product = run_in_worker(
                analyze_benchmark_product,
                product_url,
                wait_seconds=wait_seconds,
            )
            st.session_state.benchmark_product = product
            status.update(label="기준상품 분석 완료", state="complete", expanded=False)
    except (ValueError, BenchmarkAnalysisError) as error:
        st.error(str(error), icon=":material/report:")
    except Exception as error:
        st.error(
            f"분석 중 예상하지 못한 오류가 발생했습니다: {error}",
            icon=":material/report:",
        )

product = st.session_state.benchmark_product
if product:
    with st.container(border=True):
        st.subheader("2. 기준상품 확인")
        st.caption("잘못 추출된 값은 수정하고, 타오바오 검색에 사용할 키워드를 선택하세요.")

        image_col, info_col = st.columns([1, 2], vertical_alignment="top")
        with image_col:
            if product.get("image_url"):
                st.image(product["image_url"], caption="추출된 대표이미지", width="stretch")
            else:
                st.warning("대표이미지를 확인하지 못했습니다.")

        with info_col:
            candidates = keyword_candidates(product.get("title", ""))
            with st.form("benchmark_confirm_form", border=False):
                edited_title = st.text_area(
                    "경쟁상품명",
                    value=product.get("title", ""),
                    height=90,
                )
                edited_image = st.text_input(
                    "대표이미지 URL",
                    value=product.get("image_url", ""),
                )
                c1, c2 = st.columns(2)
                edited_price = c1.number_input(
                    "국내 판매가",
                    min_value=0,
                    value=int(product.get("price_krw") or 0),
                    step=100,
                    format="%d",
                )
                edited_shop = c2.text_input("판매점", value=product.get("shop_name", ""))
                main_keyword = st.selectbox(
                    "메인 키워드",
                    options=[""] + candidates,
                    help="타오바오 키워드검색과 상품명 가공의 기준입니다.",
                )
                sub_keywords = st.multiselect(
                    "서브 키워드",
                    options=[word for word in candidates if word != main_keyword],
                )
                confirm_clicked = st.form_submit_button(
                    "기준상품 확정",
                    type="primary",
                    icon=":material/check_circle:",
                )

            st.caption(
                f"출처: {product.get('marketplace', '-')} · "
                f"리뷰 신호: {product.get('review_count') or '확인 안 됨'}"
            )

        if confirm_clicked:
            product.update(
                {
                    "title": edited_title.strip(),
                    "image_url": edited_image.strip(),
                    "price_krw": int(edited_price),
                    "shop_name": edited_shop.strip(),
                    "main_keyword": main_keyword,
                    "sub_keywords": sub_keywords,
                }
            )
            if not product["title"] or not product["image_url"]:
                st.error("상품명과 대표이미지는 반드시 확인해 주세요.")
            elif not main_keyword:
                st.error("타오바오 검색에 사용할 메인 키워드를 선택해 주세요.")
            else:
                st.session_state.benchmark_product = product
                st.session_state.benchmark_confirmed = True
                st.success("기준상품을 확정했습니다.", icon=":material/check_circle:")

if st.session_state.benchmark_confirmed:
    with st.container(border=True):
        st.subheader("3. 타오바오 후보 찾기")
        st.write("같은 로그인 창에서 중국어 키워드검색과 이미지검색을 차례로 실행합니다.")
        default_query = suggest_chinese_query(
            product.get("main_keyword", ""), product.get("sub_keywords", [])
        )
        with st.form("taobao_candidate_form", border=False):
            chinese_query = st.text_input(
                "타오바오 중국어 검색어",
                value=default_query,
                placeholder="예: 微耕机 汽油",
                help="자동 초안이 비어 있거나 부정확하면 타오바오에서 쓰는 중국어 상품명으로 수정하세요.",
            )
            count_col, wait_col = st.columns(2)
            recommendation_count = count_col.number_input(
                "추천 상품 수", min_value=1, max_value=10, value=3, step=1
            )
            taobao_wait = wait_col.slider(
                "로그인·보안확인 대기시간", 60, 300, 120, 30, format="%d초"
            )
            search_clicked = st.form_submit_button(
                "타오바오 후보 찾기", type="primary", icon=":material/image_search:"
            )
        st.info(
            "Chrome에 로그인 화면이나 보안확인이 나오면 직접 완료해 주세요. "
            "이미지검색이 지원되지 않는 화면이어도 키워드검색 결과는 유지됩니다.",
            icon=":material/info:",
        )

    if search_clicked:
        st.session_state.taobao_candidates = []
        st.session_state.taobao_search_report = None
        try:
            with st.status("타오바오 공급상품을 찾고 있습니다...", expanded=True) as status:
                st.write("중국어 키워드로 상품 카드를 확인합니다.")
                st.write("대표이미지로 유사상품 검색을 시도합니다.")
                result = run_in_worker(
                    search_candidates,
                    product["image_url"],
                    chinese_query,
                    limit=int(recommendation_count),
                    wait_seconds=int(taobao_wait),
                )
                st.session_state.taobao_candidates = result["candidates"]
                st.session_state.taobao_search_report = result["report"]
                status.update(label="타오바오 후보 검색 완료", state="complete", expanded=False)
        except Exception as error:
            st.error(f"후보 검색을 시작하지 못했습니다: {error}", icon=":material/report:")

report = st.session_state.taobao_search_report
if report:
    with st.container(border=True):
        st.subheader("검색 경로 진단")
        left, right = st.columns(2)
        for column, label, route in (
            (left, "중국어 키워드검색", report["keyword"]),
            (right, "대표이미지 검색", report["image"]),
        ):
            with column:
                st.metric(label, f"{route['count']}개")
                if route["ok"]:
                    st.success(route["message"], icon=":material/check_circle:")
                else:
                    st.warning(route["message"], icon=":material/warning:")

candidates = st.session_state.taobao_candidates
if report and not candidates:
    st.error(
        "두 검색 경로에서 후보를 확보하지 못했습니다. 열린 Chrome의 로그인 상태와 "
        "중국어 검색어를 확인한 뒤 다시 실행해 주세요.",
        icon=":material/report:",
    )
elif candidates:
    st.subheader("추천 공급상품")
    st.caption("점수는 판매 신호, 정보 완성도, 두 검색 경로에서 함께 발견됐는지를 합산한 1차 점수입니다.")
    for candidate in candidates:
        with st.container(border=True):
            image_col, detail_col = st.columns([1, 3], vertical_alignment="top")
            with image_col:
                if candidate.get("image_url"):
                    st.image(candidate["image_url"], width="stretch")
            with detail_col:
                st.markdown(f"#### {candidate['rank']}위 · {candidate.get('title') or '상품명 확인 필요'}")
                metric_cols = st.columns(3)
                metric_cols[0].metric("추천점수", candidate["score"])
                metric_cols[1].metric(
                    "가격", f"¥{candidate['price_cny']:,.2f}" if candidate.get("price_cny") is not None else "확인 필요"
                )
                metric_cols[2].metric(
                    "판매 신호", f"{candidate['sales_count']:,}" if candidate.get("sales_count") else "확인 필요"
                )
                st.caption("발견 경로: " + " + ".join(candidate["routes"]))
                st.link_button(
                    "타오바오에서 확인", candidate["url"], icon=":material/open_in_new:"
                )
