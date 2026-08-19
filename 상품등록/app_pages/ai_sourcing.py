# ============================================================
# app_pages/ai_sourcing.py  —  AI 소싱 (퍼센티 'AI 소싱' 모티브)
# ------------------------------------------------------------
#   - 셀러 캘린더: 이번 달/다음 달 시즌 키워드(큐레이션)
#   - 키워드 검색: 씨앗 키워드 → 네이버 자동완성 연관 키워드
#     + (검색광고 API 키가 있으면) 월간검색수·경쟁도까지
# 여기서 고른 한글 키워드 → (중국어 번역) → 타오바오 검색/소싱으로 이어집니다.
# ============================================================

import datetime as dt

import pandas as pd
import streamlit as st

import keywords_source as ks

MONTH_LABEL = {m: f"{m}월" for m in range(1, 13)}

st.title("AI 소싱")
st.caption("시즌 키워드 + 연관 키워드로 팔릴 만한 상품 키워드를 찾습니다.")

# ---------------- 키워드 검색 (자동완성 + 검색광고) ----------------
with st.container(border=True):
    st.markdown("**스마트 키워드 검색**")
    c1, c2 = st.columns([4, 1])
    seed = c1.text_input("키워드", placeholder="상품 키워드를 입력하세요",
                         label_visibility="collapsed")
    go = c2.button("추천받기", type="primary", icon=":material/search:")

    if go and seed.strip():
        with st.spinner("연관 키워드 조회 중..."):
            # ③ 검색광고 API (키 있으면 검색수·경쟁도)
            ad = None
            try:
                ad = ks.ad_keywords(seed)
            except Exception as e:
                st.caption(f"(검색광고 API 조회 실패: {e})")

            if ad:
                st.markdown("**연관 키워드 · 월간검색수 (검색광고)**")
                df = pd.DataFrame(ad)
                df = df.rename(columns={"keyword": "키워드", "pc": "PC", "mobile": "모바일",
                                        "total": "합계", "comp": "경쟁도"})
                st.dataframe(df, hide_index=True, width="stretch")
            else:
                # ② 자동완성 (무료)
                try:
                    sug = ks.autocomplete(seed)
                    st.markdown("**연관 키워드 (자동완성)**")
                    st.markdown(" ".join(f":blue-badge[{k}]" for k in sug) or "(결과 없음)")
                except Exception as e:
                    st.warning(f"자동완성 조회 실패: {e}", icon=":material/warning:")
                if not ks.has_ad_api():
                    st.caption(
                        "월간검색수·경쟁도까지 보려면 설정에 네이버 검색광고 API 키를 넣으세요"
                        "(.env: NAVER_AD_API_KEY / NAVER_AD_SECRET / NAVER_AD_CUSTOMER_ID)."
                    )

# ---------------- 셀러 캘린더 (시즌 키워드) ----------------
with st.container(border=True):
    st.markdown("**셀러 캘린더 키워드 추천**")
    this_month = dt.date.today().month
    months = [((this_month - 1 + i) % 12) + 1 for i in range(3)]  # 이번 달 + 다음 2달
    cols = st.columns(3)
    for col, m in zip(cols, months):
        with col:
            st.markdown(f"**{MONTH_LABEL[m]} 키워드**")
            kws = ks.season_keywords(m)
            st.markdown(" ".join(f":gray-badge[{k}]" for k in kws))
    st.caption("관심 키워드를 위 검색창에 넣어 연관 키워드로 넓혀보세요.")
