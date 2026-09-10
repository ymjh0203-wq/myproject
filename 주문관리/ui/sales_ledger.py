# ==========================================================
# 매출정리 화면 (ui/sales_ledger.py)
# ----------------------------------------------------------
# 사장님 '매출' 시트를 그대로 앱 안에서: 쿠팡 자동 컬럼(주문·고객·제품·판매금액·정산금)
# + 배대지에서 가져오는 두 값(원가=단가 위안, 배송비 원)만 입력하면
# 매입가(한화)·구매비용·지출·순매출·부가세·마진율을 자동 계산하고, 엑셀로 내려받습니다.
#
# 계산식(8월 시트 역산으로 확인):
#   매입가(한화, 단위) = 단가(위안) × 1.03(카드3%) × 1.01 × 1.01 × 1.01(비자·해외·전신환 각 1%) × 환율
#   구매비용 = 매입가(한화) × 수량
#   지출합계 = 구매비용 + 배송비
#   순매출   = 정산금 − 지출합계
#   부가세   = 순매출 / 11 ,  부가세제외 = 순매출 − 부가세
#   마진율   = 부가세제외 / 총판매금액
# 수수료율(3%·1%·1%·1%)은 고정, 환율만 매번 입력합니다.
# ==========================================================

import io

import pandas as pd
import streamlit as st

from repositories import cost_repository, market_repository, order_repository, settings_repository
from services import ledger_autofill_service
from ui import common

_CARD_MULT = 1.03 * 1.01 * 1.01 * 1.01  # ≈ 1.06121003 (카드3% + 비자·해외·전신환 각 1%)
_RATE_KEY = "ledger_exchange_rate"


def _won(n) -> str:
    try:
        return f"{int(round(n)):,}"
    except (TypeError, ValueError):
        return "-"


def _compute(row: dict, rate: float) -> dict:
    """한 주문의 원가·배송비 + 환율로 파생값을 계산합니다."""
    cny = row.get("원가(단가 위안)") or 0
    ship = row.get("배송비(원)") or 0
    qty = row.get("수량") or 0
    sales = row.get("총판매금액") or 0
    settle = row.get("정산금") or 0
    unit_krw = round((cny or 0) * _CARD_MULT * (rate or 0))          # 매입가(한화, 단위)
    purchase = unit_krw * (qty or 0)                                  # 구매비용
    spend = purchase + (ship or 0)                                    # 지출합계
    net = (settle or 0) - spend                                       # 순매출
    vat = net / 11 if net else 0                                      # 부가세
    net_ex_vat = net - vat                                            # 부가세제외
    margin = (net_ex_vat / sales) if sales else 0                     # 마진율
    return {
        "매입가(한화)": unit_krw, "구매비용": purchase, "지출합계": spend,
        "순매출": net, "부가세": vat, "부가세제외": net_ex_vat, "마진율": margin,
    }


def _render_upload_fill() -> None:
    """
    사장님이 쓰는 매출 엑셀을 그대로 올리면, 각 행의 '마켓 주문번호'로 배대지를 조회해
    '단순매입가(단가 CNY)'·'배송비용(원)'을 채워 다시 내려받게 합니다.
    (앱 데이터로 새로 만드는 아래 방식과 별개 — 사장님 파일 양식 그대로 채워줌)
    """
    from services import ledger_upload_service

    with st.expander("📎 내 매출 엑셀 올려서 원가·배송비 자동 채우기 (내 양식 그대로)", expanded=False):
        st.caption(
            "내가 쓰는 매출 엑셀(‘마켓 주문번호’·‘단순매입가’·‘배송비용’ 칸이 있는 시트)을 올리면, "
            "각 행의 주문번호로 배대지 GR을 조회해 **단순매입가(단가 CNY)·배송비용(원)**을 채워 드립니다. "
            "매입가·구매비용·순매출·마진율은 시트 수식이 자동 계산합니다. 못 채운/의심 건은 맨 끝 "
            "**‘비고(자동확인)’** 칸에 사유를 적습니다. (값은 그대로 두고 두 칸만 채워 안전)"
        )
        up = st.file_uploader("매출 엑셀 파일(.xlsx)", type=["xlsx"], key="ledger_upload_file")
        if up is not None and st.button("🔎 이 파일에 원가·배송비 채우기", type="primary", key="ledger_upload_run"):
            bar = st.progress(0.0, text="배대지 조회 준비 중...")
            res = ledger_upload_service.fill_uploaded_ledger(
                up.getvalue(),
                progress=lambda i, n: bar.progress(i / n, text=f"배대지 조회 {i}/{n}"),
            )
            bar.empty()
            if not res.get("ok"):
                st.error(res.get("error") or "채우기에 실패했습니다.")
            else:
                s = res["stats"]
                st.success(
                    f"[{res['sheet']}] 시트 {s['rows']}행 처리 — 원가 {s['filled_price']}건 · "
                    f"배송비 {s['filled_ship']}건 채움. (GR없음 {s['no_gr']} · 앱에없음 {s['not_in_db']} · "
                    f"의심 {s['suspect']} · 오류 {s['err']})"
                )
                # 채운 파일 세션에 보관 → 아래 다운로드 버튼(새로고침돼도 유지)
                st.session_state["ledger_upload_result"] = res
        _res = st.session_state.get("ledger_upload_result")
        if _res and _res.get("ok"):
            st.download_button(
                "📥 채워진 엑셀 내려받기", data=_res["out_bytes"],
                file_name="매출_원가배송비_채움.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch", key="ledger_upload_dl",
            )
            if _res.get("notes"):
                with st.expander(f"⚠️ 못 채운/의심 건 {len(_res['notes'])}개 보기", expanded=False):
                    for moid, note in _res["notes"][:200]:
                        st.write(f"- {moid} → {note}")
            st.caption("※ 반드시 **Excel로 열어야** 매입가·순매출·마진율 수식이 자동 재계산됩니다.")


def render() -> None:
    st.header("매출정리")
    st.caption(
        "쿠팡 데이터는 자동, **원가(단가 위안)·배송비**만 입력하면 매입가·구매비용·순매출·마진율을 "
        "자동 계산하고 엑셀로 내려받습니다. (배대지 신청서의 '단가'와 '결제정보' 두 값)"
    )

    # 사장님 매출 엑셀을 그대로 올려서 원가·배송비만 채우는 방식(양식 유지).
    _render_upload_fill()

    accounts = market_repository.list_market_accounts(platform=market_repository.PLATFORM_COUPANG)
    if not accounts:
        st.info("등록된 쿠팡 상점이 없습니다.")
        return

    c1, c2 = st.columns([2, 1])
    with c1:
        names = [a["market_name"] for a in accounts]
        # 맨 앞에 '전체 마켓' 추가 → 모든 상점을 한꺼번에 봄.
        _opts = ["📊 전체 마켓"] + names
        _sel = st.selectbox("상점(사업자)", range(len(_opts)), format_func=lambda i: _opts[i], key="ledger_acct")
        _is_all = (_sel == 0)
        account = None if _is_all else accounts[_sel - 1]
        account_id = "all" if _is_all else account["id"]
        account_name = "전체" if _is_all else account["market_name"]
    with c2:
        _saved_rate = float(settings_repository.get_setting(_RATE_KEY, "0") or 0)
        rate = st.number_input("환율 (위안→원)", min_value=0.0, value=_saved_rate, step=1.0, format="%.2f",
                               help="배대지 신청 시점 환율을 매번 확인해서 입력하세요. (매입가 계산에 사용)")
        if rate != _saved_rate:
            settings_repository.set_setting(_RATE_KEY, str(rate))

    period_from, period_to = common.render_period_picker("ledger", "결제일시(시작)", "결제일시(종료)")
    include_closed = st.checkbox("취소·반품(주문종료)도 포함", value=False)

    # 전체 마켓이면 모든 상점의 주문을 합치고, 각 주문에 자기 마켓명을 태그(엑셀 '판매처'에 씀).
    if _is_all:
        orders = []
        for a in accounts:
            _os = order_repository.list_orders_for_ledger(
                a["id"], period_from.isoformat(), period_to.isoformat(), exclude_closed=not include_closed
            )
            for _o in _os:
                _o["_market"] = a["market_name"]
            orders += _os
    else:
        orders = order_repository.list_orders_for_ledger(
            account["id"], period_from.isoformat(), period_to.isoformat(), exclude_closed=not include_closed
        )
        for _o in orders:
            _o["_market"] = account_name
    if not orders:
        st.info("이 상점·기간에 결제된 주문이 없습니다.")
        return

    # ---- 배대지 자동조회: GR신청번호로 원가(단가 CNY)·배송비 자동 채우기 ----
    gr_cnt = sum(1 for o in orders if (o.get("gr") or "").strip())
    ac1, ac2 = st.columns([2, 3])
    with ac1:
        if st.button(f"🔎 배대지에서 원가·배송비 자동 채우기 (GR 있는 {gr_cnt}건)",
                     type="primary", width="stretch", disabled=not gr_cnt, key="ledger_autofill"):
            bar = st.progress(0.0, text="배대지 조회 중...")
            rep = ledger_autofill_service.autofill_costs(
                orders, progress=lambda i, n: bar.progress(i / n, text=f"배대지 조회 {i}/{n}")
            )
            bar.empty()
            st.success(
                f"자동 채움 완료 — GR 있는 {rep['with_gr']}건 중 "
                f"원가 {rep['filled_price']}건 · 배송비 {rep['filled_ship']}건 채움. "
                f"(GR 없음 {rep['no_gr']}건, 오류 {rep['errors']}건)"
            )
            if rep["error_samples"]:
                st.caption("일부 오류: " + " / ".join(rep["error_samples"]))
            st.rerun()
    with ac2:
        st.caption(
            "각 주문의 퀵스타 GR신청번호로 배대지를 조회해 **원가(단가 위안)·배송비(원)**를 자동 입력합니다. "
            "배송비는 출고(무게측정)된 건만 나옵니다. 채운 뒤 값이 이상하면 표에서 직접 고칠 수 있습니다."
        )

    saved = cost_repository.get_map([o["order_id"] for o in orders])

    # ---- 입력 표(원가·배송비만 편집) ----
    rows = []
    for o in orders:
        sc = saved.get(o["order_id"], {})
        _r = {}
        if _is_all:
            _r["마켓"] = o.get("_market") or ""
        _r.update({
            "주문번호": o["market_order_id"],
            "결제일": (o.get("paid_at") or "")[:10],
            "고객": o.get("customer") or "",
            "제품명": (o.get("product") or "")[:40],
            "수량": int(o.get("qty") or 0),
            "총판매금액": int(o.get("sales") or 0),
            "정산금": int(o.get("settlement") or 0),
            "원가(단가 위안)": sc.get("unit_price_cny"),
            "배송비(원)": sc.get("shipping_cost"),
        })
        rows.append(_r)
    df = pd.DataFrame(rows)

    st.markdown("**원가·배송비 입력** (배대지에서 가져온 값). 입력하면 아래 계산·합계가 바로 갱신됩니다.")
    edited = st.data_editor(
        df, width="stretch", num_rows="fixed", height=460, key=f"ledger_editor_{account_id}",
        disabled=["마켓", "주문번호", "결제일", "고객", "제품명", "수량", "총판매금액", "정산금"],
        column_config={
            "총판매금액": st.column_config.NumberColumn("총판매금액", format="%d"),
            "정산금": st.column_config.NumberColumn("정산금", format="%d"),
            "원가(단가 위안)": st.column_config.NumberColumn("원가(단가 위안)", help="배대지 신청서의 '단가(위안)'", format="%.2f"),
            "배송비(원)": st.column_config.NumberColumn("배송비(원)", help="배대지 결제정보 배송비(원)", format="%d"),
        },
    )

    # ---- 파생 계산 + 합계 ----
    calc_rows = []
    for _, r in edited.iterrows():
        base = r.to_dict()
        base["원가(단가 위안)"] = None if pd.isna(base.get("원가(단가 위안)")) else base.get("원가(단가 위안)")
        base["배송비(원)"] = None if pd.isna(base.get("배송비(원)")) else base.get("배송비(원)")
        d = _compute(base, rate)
        calc_rows.append({**base, **d})
    cdf = pd.DataFrame(calc_rows)

    tot_sales = int(cdf["총판매금액"].sum())
    tot_settle = int(cdf["정산금"].sum())
    tot_spend = int(cdf["지출합계"].sum())
    tot_net = int(cdf["순매출"].sum())
    avg_margin = (cdf["부가세제외"].sum() / tot_sales) if tot_sales else 0
    m = st.columns(5)
    m[0].metric("총 판매금액", _won(tot_sales))
    m[1].metric("총 정산금", _won(tot_settle))
    m[2].metric("총 지출(원가+배송)", _won(tot_spend))
    m[3].metric("순매출", _won(tot_net))
    m[4].metric("평균 마진율", f"{avg_margin*100:.1f}%")

    col_save, col_dl = st.columns([1, 1])
    with col_save:
        if st.button("💾 원가·배송비 저장", type="primary", width="stretch", key="ledger_save"):
            for o, (_, r) in zip(orders, edited.iterrows()):
                cost_repository.upsert(o["order_id"], r.get("원가(단가 위안)"), r.get("배송비(원)"))
            st.success("저장했습니다.")
            st.rerun()

    # ---- 엑셀 다운로드: 사장님 '매출' 시트 양식 그대로 ----
    xlsx_bytes = _build_maechul_xlsx(orders, edited, rate, account_name)
    with col_dl:
        st.download_button(
            "📥 매출정리 엑셀 다운로드 (사장님 매출 양식)", data=xlsx_bytes,
            file_name=f"매출_{account_name}_{period_from}~{period_to}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch", key="ledger_dl",
        )


# 사장님 '매출' 시트 컬럼 순서(39개) — 실제 8월.xlsx 매출 탭과 동일.
_MAECHUL_HEADERS = [
    "판매처", "CS메모", "주문시간", "국내 운송장", "중국 트래킹", "마켓", "타오바오 주문번호",
    "마켓 주문번호", "고객성함", "연락처", "개인고유통관부호", "우편번호", "주소", "제품명", "옵션",
    "주문수량", "총 판매금액", "배송비", "정산금", "실제정산금 합계", "단순매입가",
    "실매입가(카드결제수수료포함)", "비자바스터카드", "해외이용수수료", "전신환매도율", "매입가(한화)",
    "구매비용", "배송비용", "지출비용합계", "순매출", "부가세", "부가세 제외", "마진율", "배송비", "",
    "결제 일시", "결제시간", "결제금액", "카드",
]


def _split_dt(text):
    """'2026-08-01 01:44:49' 또는 ISO 'T' 형식을 (날짜, 시간)으로 나눕니다."""
    s = str(text or "").replace("T", " ").strip()
    if not s:
        return "", ""
    parts = s.split(" ", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _maechul_row(o: dict, cny, ship, rate: float) -> list:
    """주문 하나 → '매출' 시트 한 줄(39칸). 원가가 있으면 계산열까지 채웁니다."""
    qty = int(o.get("qty") or 0)
    sales = int(o.get("sales") or 0)
    settle = int(o.get("settlement") or 0)
    addr = " ".join(x for x in [o.get("addr1") or "", o.get("addr2") or ""] if x).strip()
    pdate, ptime = _split_dt(o.get("paid_at"))

    # 계산열(원가가 있을 때만)
    v1 = v2 = v3 = v4 = krw = buy = spend = net = vat = net_ex = margin = ""
    ship_cost = "" if ship is None else int(round(ship))
    if cny is not None:
        c = float(cny)
        v1 = c * 1.03                    # 실매입가(카드3%)
        v2 = v1 * 1.01                   # 비자/마스터
        v3 = v2 * 1.01                   # 해외이용수수료
        v4 = v3 * 1.01                   # 전신환매도율
        krw = round(v4 * (rate or 0), 2)  # 매입가(한화)
        buy = round(krw * qty, 2)         # 구매비용
        s_val = ship if ship is not None else 0
        spend = round(buy + s_val, 2)     # 지출비용합계
        net = round(settle - spend, 2)    # 순매출
        vat = round(net / 11, 2)          # 부가세
        net_ex = round(net - vat, 2)      # 부가세 제외
        margin = round(net_ex / sales, 4) if sales else 0  # 마진율
        v1, v2, v3, v4 = round(v1, 4), round(v2, 4), round(v3, 4), round(v4, 4)

    return [
        o.get("판매처_name", ""),          # 판매처 (아래에서 채움)
        o.get("cs_memo") or "",            # CS메모
        o.get("ordered_at") or "",         # 주문시간
        o.get("invoice") or "",            # 국내 운송장
        "",                                 # 중국 트래킹
        "06.쿠팡",                          # 마켓
        "",                                 # 타오바오 주문번호
        o.get("market_order_id") or "",    # 마켓 주문번호
        o.get("customer") or "",           # 고객성함
        o.get("phone") or "",              # 연락처
        o.get("pccc") or "",               # 개인고유통관부호
        o.get("zip_code") or "",           # 우편번호
        addr,                               # 주소
        o.get("product") or "",            # 제품명
        o.get("option") or "",             # 옵션
        qty,                                # 주문수량
        sales,                              # 총 판매금액
        0,                                  # 배송비(고객부담, 보통 0)
        settle,                             # 정산금
        settle,                             # 실제정산금 합계
        ("" if cny is None else float(cny)),  # 단순매입가(원가 단가 CNY)
        v1, v2, v3, v4,                     # 실매입가/비자/해외/전신환
        krw,                                # 매입가(한화)
        buy,                                # 구매비용
        ship_cost,                          # 배송비용
        spend,                              # 지출비용합계
        net,                                # 순매출
        vat,                                # 부가세
        net_ex,                             # 부가세 제외
        margin,                             # 마진율
        "",                                 # 배송비(빈칸)
        "",                                 # (빈칸)
        pdate,                              # 결제 일시
        ptime,                              # 결제시간
        "",                                 # 결제금액
        "",                                 # 카드
    ]


def _build_maechul_xlsx(orders: list, edited, rate: float, account_name: str) -> bytes:
    """'매출' 시트 양식으로 엑셀을 만들어 bytes로 돌려줍니다."""
    import openpyxl

    # edited(표)에서 주문별 원가·배송비 읽기 (orders와 같은 순서)
    cny_list, ship_list = [], []
    for _, r in edited.iterrows():
        c = r.get("원가(단가 위안)")
        s = r.get("배송비(원)")
        cny_list.append(None if (c is None or pd.isna(c)) else float(c))
        ship_list.append(None if (s is None or pd.isna(s)) else float(s))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "매출"
    ws.append(_MAECHUL_HEADERS)
    for o, cny, ship in zip(orders, cny_list, ship_list):
        o = dict(o)
        # 전체 마켓이면 각 주문의 실제 마켓명(_market), 아니면 선택한 상점명.
        o["판매처_name"] = o.get("_market") or account_name
        ws.append(_maechul_row(o, cny, ship, rate))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
