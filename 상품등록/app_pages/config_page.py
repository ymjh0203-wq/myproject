# ============================================================
# app_pages/config_page.py  —  필수 설정 (퍼센티 '필수 설정' 모티브)
# ------------------------------------------------------------
# 탭: 기본 설정(가격/마진/수수료/배송비) · 마켓 연동 · 시스템(연결 상태)
# 기본 설정은 data/config.json 에 저장되고, 이후 판매가 계산에 사용됩니다.
# ============================================================

import os

import streamlit as st

import config_store as cs

st.title("필수 설정")
st.caption("판매가 계산에 쓰이는 마진·수수료·배송비를 설정합니다.")

tab_basic, tab_market, tab_system = st.tabs(["기본 설정", "마켓 연동", "시스템"])

# ---------------- 기본 설정 (저장됨) ----------------
with tab_basic:
    cfg = cs.load_config()

    with st.form("basic_settings"):
        st.subheader("판매 마진")
        c1, c2 = st.columns(2)
        margin_percent = c1.number_input("퍼센트 마진 (%)", value=float(cfg["margin_percent"]), step=1.0)
        margin_add = c2.number_input("더하기 마진 (원)", value=int(cfg["margin_add"]), step=100)

        st.subheader("배송대행지 / 배송비")
        c3, c4 = st.columns(2)
        overseas = c3.number_input("해외 배송비 (원)", value=int(cfg["overseas_shipping"]), step=100)
        shipping_type = c4.selectbox(
            "배송비 종류", ["무료 배송", "유료 배송"],
            index=0 if cfg["shipping_type"] == "무료 배송" else 1,
        )
        c5, c6 = st.columns(2)
        return_fee = c5.number_input("반품비 (원)", value=int(cfg["return_fee"]), step=100)
        exchange_fee = c6.number_input("교환비 (원)", value=int(cfg["exchange_fee"]), step=100)

        st.subheader("수수료")
        card_fee = st.number_input("카드 수수료 (%)", value=float(cfg["card_fee"]), step=0.1)
        st.markdown("**판매 마켓 수수료 (%)**")
        c7, c8 = st.columns(2)
        fee_ss = c7.number_input("스마트스토어", value=float(cfg["market_fees"].get("smartstore", 0)), step=0.1)
        fee_esm = c8.number_input("옥션/G마켓(ESM)", value=float(cfg["market_fees"].get("esm", 0)), step=0.1)

        st.subheader("기타")
        c9, c10 = st.columns(2)
        market_discount = c9.number_input("마켓 표기 할인율 (%)", value=int(cfg["market_discount"]), step=1)
        round_unit = c10.selectbox(
            "단위 올림 (원)", [1, 10, 100, 1000],
            index=[1, 10, 100, 1000].index(cfg["round_unit"]) if cfg["round_unit"] in [1, 10, 100, 1000] else 2,
        )
        c11, c12 = st.columns(2)
        customs = c11.selectbox(
            "관부가세 설정", ["부과 대상 아님", "부과 대상"],
            index=0 if cfg["customs"] == "부과 대상 아님" else 1,
        )
        option_basis = c12.selectbox(
            "옵션 대표 가격 기준", ["최저 옵션가 기준", "최고 옵션가 기준", "평균 옵션가 기준"],
            index=["최저 옵션가 기준", "최고 옵션가 기준", "평균 옵션가 기준"].index(cfg["option_price_basis"])
            if cfg["option_price_basis"] in ["최저 옵션가 기준", "최고 옵션가 기준", "평균 옵션가 기준"] else 0,
        )

        if st.form_submit_button("설정 저장", type="primary", icon=":material/save:"):
            cs.save_config({
                "margin_percent": margin_percent,
                "margin_add": margin_add,
                "overseas_shipping": overseas,
                "card_fee": card_fee,
                "market_fees": {"smartstore": fee_ss, "esm": fee_esm},
                "market_discount": market_discount,
                "round_unit": round_unit,
                "customs": customs,
                "option_price_basis": option_basis,
                "shipping_type": shipping_type,
                "return_fee": return_fee,
                "exchange_fee": exchange_fee,
            })
            st.success("저장했습니다. 이 값으로 판매가를 계산합니다.", icon=":material/check_circle:")

# ---------------- 마켓 연동 ----------------
with tab_market:
    st.subheader("마켓 연동")
    st.dataframe(
        {"마켓": list(cs.MARKET_KEYS.values()), "상태": ["미연동"] * len(cs.MARKET_KEYS)},
        hide_index=True, width="stretch",
    )
    st.caption("네이버 커머스API·ESM PLUS 키 연동은 이후 단계에서 추가합니다.")

# ---------------- 시스템 (연결 상태) ----------------
with tab_system:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    profile_dir = os.path.join(BASE_DIR, ".browser_profile")

    st.subheader("Supabase(DB) 연결")
    try:
        from db import get_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name IN "
                    "('products_raw','products_processed','keywords',"
                    "'category_mapping','listings','benchmark_seeds')"
                )
                n = cur.fetchone()[0]
        finally:
            conn.close()
        st.success(f"연결 성공 · 필요한 표 {n}/6개", icon=":material/check_circle:")
    except Exception as e:
        st.warning("DB 미연결 — .env 의 DATABASE_URL 확인 후 python apply_migration.py", icon=":material/warning:")
        with st.expander("자세한 오류"):
            st.code(str(e))

    st.subheader("타오바오 로그인")
    if os.path.isdir(profile_dir) and os.listdir(profile_dir):
        st.success("로그인 프로필 저장됨 (.browser_profile)", icon=":material/check_circle:")
    else:
        st.warning("타오바오_로그인_설정.bat 으로 1회 로그인해주세요.", icon=":material/warning:")
