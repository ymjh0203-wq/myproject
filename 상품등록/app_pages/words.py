# ============================================================
# app_pages/words.py  —  키워드 / 단어 설정 (퍼센티 모티브)
# ------------------------------------------------------------
# 탭: 키워드 프리셋 · 경고 단어 · 단어 치환
#   - 키워드 프리셋: 상품명 만들 때 쓸 묶음 키워드
#   - 경고 단어: 상품명/옵션에서 걸러낼 금지어
#   - 단어 치환: 수집 시 상품명·옵션의 단어를 자동 치환
# 모두 data/word_rules.json 에 저장되고, 이후 가공 단계에서 사용됩니다.
# ============================================================

import pandas as pd
import streamlit as st

import config_store as cs

st.title("키워드 / 단어 설정")
st.caption("상품명 생성·가공에 쓰이는 키워드와 단어 규칙을 관리합니다.")

words = cs.load_words()
tab_preset, tab_warn, tab_replace = st.tabs(["키워드 프리셋", "경고 단어", "단어 치환"])

# ---------------- 키워드 프리셋 ----------------
with tab_preset:
    st.subheader("키워드 프리셋 등록")
    st.caption("묶음 키워드를 등록해 상품명 생성 시 뒤에 섞을 후보로 씁니다.")
    with st.form("preset_form", clear_on_submit=True):
        c1, c2 = st.columns([1, 2])
        name = c1.text_input("프리셋 명 (최대 10자)", max_chars=10)
        kws = c2.text_input("키워드 (쉼표로 구분, 최대 20개)")
        if st.form_submit_button("등록하기", type="primary", icon=":material/add:"):
            kw_list = [k.strip() for k in kws.split(",") if k.strip()][:20]
            if name and kw_list:
                words["presets"].append({"name": name, "keywords": kw_list})
                cs.save_words(words)
                st.success(f"'{name}' 프리셋 등록됨", icon=":material/check_circle:")
                st.rerun()
            else:
                st.warning("프리셋 명과 키워드를 입력해주세요.")

    st.markdown(f"**키워드 프리셋 목록 — {len(words['presets'])}개**")
    if words["presets"]:
        df = pd.DataFrame(
            [{"프리셋 명": p["name"], "키워드": ", ".join(p["keywords"]),
              "키워드 수": len(p["keywords"])} for p in words["presets"]]
        )
        st.dataframe(df, hide_index=True, width="stretch")
        idx = st.number_input("삭제할 행 번호(0부터)", min_value=0,
                              max_value=len(words["presets"]) - 1, value=0, step=1)
        if st.button("선택 프리셋 삭제", icon=":material/delete:"):
            words["presets"].pop(int(idx))
            cs.save_words(words)
            st.rerun()
    else:
        st.caption("등록된 프리셋이 없습니다.")

# ---------------- 경고 단어 ----------------
with tab_warn:
    st.subheader("경고 단어 등록")
    st.caption("상품명/옵션에 있으면 걸러낼 금지어입니다.")
    with st.form("warn_form", clear_on_submit=True):
        c1, c2 = st.columns([1, 2])
        word = c1.text_input("경고 단어 (쉼표로 여러 개)")
        note = c2.text_input("사유 메모")
        if st.form_submit_button("등록하기", type="primary", icon=":material/add:"):
            new = [w.strip() for w in word.split(",") if w.strip()]
            existing = {w["word"] for w in words["warn_words"]}
            added = 0
            for w in new:
                if w not in existing:
                    words["warn_words"].append({"word": w, "note": note})
                    added += 1
            cs.save_words(words)
            st.success(f"경고 단어 {added}개 등록됨", icon=":material/check_circle:")
            st.rerun()

    st.markdown(f"**경고 단어 목록 — {len(words['warn_words'])}개**")
    if words["warn_words"]:
        st.dataframe(
            pd.DataFrame([{"경고 단어": w["word"], "사유 메모": w.get("note", "")} for w in words["warn_words"]]),
            hide_index=True, width="stretch",
        )
    else:
        st.caption("등록된 경고 단어가 없습니다.")

# ---------------- 단어 치환 ----------------
with tab_replace:
    st.subheader("단어 치환 등록")
    st.caption("수집 시 상품명·옵션명의 단어를 설정한 단어로 자동 치환합니다.")
    with st.form("replace_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        w_from = c1.text_input("치환할 단어")
        w_to = c2.text_input("치환될 단어")
        if st.form_submit_button("등록하기", type="primary", icon=":material/add:"):
            if w_from:
                words["replaces"].append({"from": w_from.strip(), "to": w_to.strip()})
                cs.save_words(words)
                st.success("치환 규칙 등록됨", icon=":material/check_circle:")
                st.rerun()
            else:
                st.warning("치환할 단어를 입력해주세요.")

    st.markdown(f"**단어 치환 목록 — {len(words['replaces'])}개**")
    if words["replaces"]:
        st.dataframe(
            pd.DataFrame([{"치환할 단어": r["from"], "치환될 단어": r["to"]} for r in words["replaces"]]),
            hide_index=True, width="stretch",
        )
    else:
        st.caption("등록된 치환 규칙이 없습니다.")
