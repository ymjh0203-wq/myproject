# ============================================================
# app_pages/sourcing.py  —  벤치마킹 소싱 페이지
# ------------------------------------------------------------
# 기준 상품(URL/이미지) → 대표이미지 → 타오바오 이미지검색 →
# 유사 후보 N개 → 각 추출 → 카드로 보기 → 선택 저장(products_raw)
#
# 검색 시 "타오바오 크롬 창"이 따로 뜹니다. 로그인/캡차가 보이면 그 창에서
# 직접 처리하세요. 결과가 뜨면 이 화면이 자동으로 이어받습니다.
# ============================================================

import os
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

import seed as seed_mod
from collector.taobao import CaptchaDetected, collect_taobao
from collector.taobao_image_search import image_search

SEEDS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seeds")


def run_in_thread(fn, *args, **kwargs):
    """Playwright(sync)를 Streamlit 스레드가 아닌 '새 스레드'에서 실행."""
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(fn, *args, **kwargs).result()


def save_uploaded_image(uploaded) -> str:
    """업로드한 이미지를 seeds/ 폴더에 저장하고 경로를 돌려줍니다."""
    os.makedirs(SEEDS_DIR, exist_ok=True)
    path = os.path.join(SEEDS_DIR, "upload_" + uploaded.name)
    with open(path, "wb") as f:
        f.write(uploaded.getbuffer())
    return path


# ---- 세션 상태 초기화 (한 곳에서) ----
if "seed" not in st.session_state:
    st.session_state.seed = None
if "results" not in st.session_state:
    st.session_state.results = None
if "saved_msg" not in st.session_state:
    st.session_state.saved_msg = None


st.title("벤치마킹 소싱")
st.caption("잘 팔리는 기준 상품의 이미지로 타오바오에서 유사 상품을 찾아옵니다.")

with st.form("search_form"):
    st.markdown("**기준 상품 입력** — URL 또는 이미지 중 하나")
    url = st.text_input(
        "한국 마켓 상품 URL",
        placeholder="https://smartstore.naver.com/... (가격비교 catalog 주소는 봇차단됨)",
    )
    uploaded = st.file_uploader("또는 상품 이미지 업로드", type=["jpg", "jpeg", "png", "webp"])
    num = st.slider("찾을 후보 개수", min_value=1, max_value=10, value=3)
    wait = st.slider(
        "결과 대기 시간(초)", min_value=30, max_value=180, value=90,
        help="캡차를 직접 푸는 시간이 필요하면 넉넉히 잡으세요.",
    )
    submitted = st.form_submit_button("유사 상품 찾기", type="primary", icon=":material/search:")

st.info(
    "검색을 누르면 타오바오 크롬 창이 따로 뜹니다. 로그인/캡차가 보이면 그 창에서 "
    "직접 처리하세요 — 결과가 뜨면 이 화면이 자동으로 이어받습니다.",
    icon=":material/info:",
)

if submitted:
    if not url and uploaded is None:
        st.warning("상품 URL을 입력하거나 이미지를 업로드해주세요.")
        st.stop()

    st.session_state.results = None
    st.session_state.saved_msg = None

    with st.status("소싱 진행 중...", expanded=True) as status:
        try:
            if uploaded is not None:
                st.write("업로드한 이미지를 기준으로 사용합니다.")
                seed = seed_mod.image_from_file(save_uploaded_image(uploaded))
            else:
                st.write("상품 URL에서 대표이미지를 추출하는 중...")
                seed = run_in_thread(seed_mod.image_from_url, url)
            st.session_state.seed = seed

            st.write("타오바오 이미지검색 중... (뜬 창에서 로그인/캡차 필요하면 처리)")
            candidates = run_in_thread(
                image_search, seed["seed_image_path"], num, None, False, wait
            )
            if not candidates:
                status.update(label="후보를 찾지 못했습니다.", state="error")
                st.stop()
            st.write(f"후보 {len(candidates)}개 발견 — 상세 정보 추출 중...")

            results = []
            for rank, cand in enumerate(candidates, start=1):
                st.write(f"  {rank}번 후보 추출 중...")
                try:
                    data = run_in_thread(collect_taobao, cand)
                    data["_match_rank"] = rank
                    results.append(data)
                except Exception as e:
                    st.write(f"  {rank}번 후보 추출 실패: {e}")
            st.session_state.results = results
            status.update(label=f"완료 — {len(results)}개 후보 추출", state="complete")
        except CaptchaDetected as e:
            status.update(label="캡차/차단으로 중단", state="error")
            st.error(f"{e}", icon=":material/report:")
            st.stop()
        except Exception as e:
            status.update(label="오류로 중단", state="error")
            st.error(f"진행 중 오류: {e}", icon=":material/report:")
            st.stop()


# ---- 결과 표시 ----
seed = st.session_state.seed
results = st.session_state.results

if seed and results is not None:
    st.divider()
    left, right = st.columns([1, 3])
    with left:
        st.markdown("**기준 상품(씨앗)**")
        if seed.get("seed_image_path") and os.path.exists(seed["seed_image_path"]):
            st.image(seed["seed_image_path"], width="stretch")
        st.caption(seed.get("seed_ref", ""))
    with right:
        st.markdown(f"**찾은 유사 상품 {len(results)}개**")
        if not results:
            st.warning("추출된 후보가 없습니다. 대기 시간을 늘리거나 다른 이미지를 써보세요.")

        selected = []
        cols = st.columns(min(len(results), 3)) if results else []
        for i, data in enumerate(results):
            with cols[i % len(cols)]:
                with st.container(border=True):
                    imgs = data.get("image_urls") or []
                    if imgs:
                        st.image(imgs[0], width="stretch")
                    st.markdown(f"**{data.get('title_original') or '(제목 없음)'}**")
                    price = data.get("price_original")
                    st.caption(f"가격(CNY): {price if price is not None else '—'}")
                    if data.get("shop_name"):
                        st.caption(f"판매점: {data['shop_name']}")
                    st.link_button("타오바오에서 열기", data["source_url"], icon=":material/open_in_new:")
                    pick = st.checkbox("채택", key=f"pick_{i}", value=True)
                    if pick:
                        selected.append(data)

        if results:
            st.divider()
            c1, c2 = st.columns([1, 3])
            with c1:
                do_save = st.button(
                    "선택 항목 저장", type="primary", icon=":material/save:",
                    disabled=(len(selected) == 0),
                )
            with c2:
                st.caption(
                    f"{len(selected)}개 선택됨 · 저장하려면 .env의 DATABASE_URL과 "
                    "적용된 표(001·002)가 필요합니다."
                )

            if do_save:
                try:
                    from db import (get_conn, insert_benchmark_seed, link_to_seed,
                                    upsert_product_raw)
                    conn = get_conn()
                    try:
                        seed_id = insert_benchmark_seed(conn, seed)
                        for data in selected:
                            rank = data.get("_match_rank")
                            clean = {k: v for k, v in data.items() if not k.startswith("_")}
                            pid = upsert_product_raw(conn, clean)
                            link_to_seed(conn, pid, seed_id, rank)
                        st.session_state.saved_msg = (
                            f"씨앗 #{seed_id} 기준으로 {len(selected)}개를 products_raw에 저장했습니다."
                        )
                    finally:
                        conn.close()
                except Exception as e:
                    st.error(f"저장 실패: {e}", icon=":material/report:")

if st.session_state.saved_msg:
    st.success(st.session_state.saved_msg, icon=":material/check_circle:")
