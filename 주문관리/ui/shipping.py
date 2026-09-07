# ==========================================================
# 배송중 화면 (ui/shipping.py)
# ----------------------------------------------------------
# 발송대기에서 송장을 등록하면 여기로 이동합니다. "수집하기"를 누르면
# 쿠팡에서 배송지시(DEPARTURE)/배송중(DELIVERING) 상태인 주문을 직접
# 가져올 수도 있습니다 (쿠팡 Wing에서 직접 처리한 주문도 반영됨).
#
# 목록 표(엑셀 양식 전체 컬럼 + 너비/높이 조절 슬라이더 + 클릭 시 상세정보)는
# ui/common.py의 render_full_table/render_full_detail을 공용으로 씁니다.
#
# TODO: 배송완료(FINAL_DELIVERY)로의 전환은 택배사 배송완료 스캔에 따라
# 쿠팡이 자동으로 처리하는 것으로 보입니다. 판매자가 직접 호출하는 "배송완료
# 처리" API를 아직 확인하지 못해서, 수동으로 배송완료로 넘기는 버튼은 만들지
# 않았습니다 (확인 후 추가 예정).
# ==========================================================

import streamlit as st

import models
from integrations import quickstar_automation
from repositories import order_repository, settings_repository
from services import customs_service, gr_backfill_service, quickstar_invoice_service, sync_service
from ui import common, settings


def _collect_invoice_pending(key: str = "shipping") -> list:
    """
    배송중 표에서 사용자가 고친 택배사·송장번호(session_state[f'{key}_edited_records'])를
    현재 등록값과 비교해, '실제로 바뀐' 건만 골라 돌려줍니다.
    반환: [(order, 택배사코드, 송장번호, 표시용_택배사명), ...]
    """
    _name_to_code = {name: code for code, name in models.COURIER_CODES.items()}

    def _cur_courier_name(o):
        code = o.get("delivery_company_code")
        if code:
            return models.COURIER_CODES.get(code, code)
        return (o.get("market_delivery_company_name") or "").strip()

    def _cur_invoice(o):
        return str(o.get("invoice_number") or o.get("market_invoice_number") or "").strip()

    all_shipping = {
        str(o["market_order_id"]): o
        for o in order_repository.list_orders_by_work_status(models.WORK_STATUS_SHIPPING)
    }
    records = st.session_state.get(f"{key}_edited_records", []) or []
    pending = []
    for r in records:
        o = all_shipping.get(str(r.get("주문번호")))
        if not o:
            continue
        new_name = str(r.get("택배사") or "").strip()
        new_inv = str(r.get("송장번호") or "").strip()
        if not new_inv or new_inv.lower() in ("-", "nan"):
            continue
        # 현재 등록값과 같으면(=안 고친 것) 대상 아님.
        if new_inv == _cur_invoice(o) and new_name == _cur_courier_name(o):
            continue
        code = _name_to_code.get(new_name) or o.get("delivery_company_code")
        if not code:
            continue
        pending.append((o, code, new_inv, new_name or models.COURIER_CODES.get(code, code)))
    return pending


@st.dialog("🚚 운송장(송장) 변경")
def _invoice_change_dialog(key: str = "shipping") -> None:
    """
    표에서 고친 택배사·송장번호를 모아 보여주고, 버튼 한 번으로 여러 건을 쿠팡에 실제
    반영합니다. (배송중 화면 상단 '🚚 운송장 변경' 버튼으로 열립니다.)
    """
    pending = _collect_invoice_pending(key)
    if not pending:
        st.info(
            "변경할 송장이 없습니다.\n\n"
            "먼저 표에서 **택배사·송장번호 칸을 직접 고친 뒤** 이 버튼을 눌러주세요.",
            icon="✏️",
        )
        if st.button("닫기", width="stretch"):
            st.rerun()
        return

    st.markdown(f"**아래 {len(pending)}건을 쿠팡에 실제 반영합니다.**")
    for o, _code, _inv, _cname in pending[:30]:
        _rcv = (o.get("shipping") or {}).get("receiver_name") or o.get("orderer_name") or "-"
        st.write(f"- {o['market_order_id']} · {_rcv} → **{_cname} / {_inv}**")
    if len(pending) > 30:
        st.caption(f"…외 {len(pending) - 30}건")

    c_apply, c_clear = st.columns([2, 1])
    with c_apply:
        if st.button(f"✏️ {len(pending)}건 일괄 송장 변경 (쿠팡 실제 반영)",
                     type="primary", width="stretch", key=f"{key}_bulk_invoice_edit"):
            done_ids, fails = [], []
            bar = st.progress(0.0)
            for i, (o, code, inv, _cn) in enumerate(pending):
                try:
                    res = sync_service.update_invoice_for_shipping(o, code, inv)
                    if res.get("succeeded"):
                        done_ids.append(str(o["market_order_id"]))
                    else:
                        fails.append(f"{o['market_order_id']}: {res.get('message')}")
                except Exception as e:  # noqa: BLE001
                    fails.append(f"{o['market_order_id']}: {e}")
                bar.progress((i + 1) / len(pending))
            bar.empty()
            if done_ids:
                st.success(f"{len(done_ids)}건 송장 변경 완료")
            if fails:
                st.error("일부 실패:\n" + "\n".join(f"- {x}" for x in fails[:12]))
            _records = st.session_state.get(f"{key}_edited_records", []) or []
            st.session_state[f"{key}_edited_records"] = [
                r for r in _records if str(r.get("주문번호")) not in set(done_ids)
            ]
            if done_ids and not fails:
                st.rerun()
    with c_clear:
        if st.button("대기 비우기", width="stretch", key=f"{key}_clear_edits"):
            st.session_state[f"{key}_edited_records"] = []
            st.rerun()


def _migrate_shipping_view_settings() -> None:
    """
    기존 '배송중'(key='shipping') 표 설정(표시항목·정렬·너비)을 새 하위탭
    (배송지시=shipping_dep / 배송중=shipping_del) 키로 1회 복사합니다. 사용자가
    맞춰둔 표 레이아웃을 하위탭 분리 후에도 그대로 잇기 위함입니다.
    """
    for suffix in ("shipping_dep", "shipping_del"):
        for prefix in ("column_order", "sort_col", "sort_dir", "table_width"):
            src = settings_repository.get_setting(f"{prefix}:shipping")
            if src is not None and settings_repository.get_setting(f"{prefix}:{suffix}") is None:
                settings_repository.set_setting(f"{prefix}:{suffix}", src)


def _render_shipping_list(sub_orders: list, sub_rows: list, key: str, reveal: bool,
                          empty_hint: str) -> None:
    """
    배송지시/배송중 하위탭 각각의 목록(버튼바 + 표 + 송장변경 + 합계)을 그립니다.
    같은 코드를 두 탭이 key만 달리해서 재사용합니다. (선택·편집·표시설정 상태가 탭별로 분리됨)
    """
    if not sub_orders:
        st.info(empty_hint)
        return

    # 버튼바(맨 오른쪽 '🚚 운송장 변경' 포함)
    common.render_shopmine_action_bar(key, sub_orders, invoice_button=True)

    def _detail(order):
        common.render_full_detail(order, reveal)

    # 송장 변경 안내 + 팝업 열기
    _pending_cnt = len(_collect_invoice_pending(key))
    if _pending_cnt:
        st.info(
            f"✏️ 송장 변경 **수정 대기 {_pending_cnt}건** — 위 **‘🚚 운송장 변경’** 버튼을 누르면 "
            "한 번에 반영합니다.",
            icon="🚚",
        )
    else:
        st.caption(
            "✏️ **송장(운송장) 변경**: 표에서 **택배사·송장번호 칸을 직접 고친 뒤**, 위 "
            "**‘🚚 운송장 변경’** 버튼을 누르면 여러 건을 한 번에 반영합니다."
        )
    if st.session_state.pop(f"{key}_invoice_dialog", False):
        _invoice_change_dialog(key)

    _courier_names = list(dict.fromkeys(models.COURIER_CODES.values()))
    _ship_editable = {
        "택배사": {"cellEditor": "agSelectCellEditor", "cellEditorParams": {"values": _courier_names}},
        "송장번호": {"cellEditor": "agTextCellEditor"},
    }
    common.render_full_table(
        sub_rows, sub_orders, key=key,
        detail_renderer=_detail,
        multi_select=True,
        editable=_ship_editable,
    )
    common.render_order_summary_bar(sub_orders)


@st.dialog("관부가세 결재통보 조회 & 자동문자")
def _scan_customs_tax(gr_orders: list) -> None:
    """
    퀵스타 통관조회로 각 주문의 '수입(사용소비) 결재통보'(관부가세 부과)를 확인하고,
    부과된 주문에 8번 관부가세 통보 문구를 자동발송합니다. (GR신청번호 있는 주문만 대상)
    """
    st.write(
        f"GR신청번호가 있는 **{len(gr_orders)}건**의 통관 상태를 조회해서, 관부가세가 부과된"
        "(결재통보) 주문에 **8번 관부가세 통보 문구**를 자동발송합니다."
    )
    st.caption("이미 같은 결재통보로 보낸 주문은 건너뜁니다. 퀵스타 자동화 크롬이 로그인돼 있어야 합니다. 건당 몇 초 걸립니다.")

    if quickstar_automation.get_status() != "READY":
        st.warning("퀵스타 자동화가 준비되지 않았습니다. 발송대기의 '퀵스타 연동'에서 자동화를 켜고 로그인해주세요.")
        if st.button("닫기"):
            st.rerun()
        return

    col_go, col_close = st.columns(2)
    with col_go:
        if st.button("조회 & 자동문자 발송", type="primary", width="stretch"):
            counts = {"sent": 0, "no_notice": 0, "already_sent": 0, "no_phone": 0, "fail": 0}
            fails = []
            bar = st.progress(0.0, text="통관조회 중...")
            for i, order in enumerate(gr_orders):
                result = customs_service.maybe_send_customs_tax_notice(order)
                reason = result.get("reason")
                if result.get("sent"):
                    counts["sent"] += 1
                elif reason in ("no_notice", "already_sent", "no_phone"):
                    counts[reason] += 1
                elif reason in ("query_fail", "need_login", "send_failed"):
                    counts["fail"] += 1
                    fails.append(f"{order['market_order_id']}: {result.get('msg') or result.get('error') or reason}")
                bar.progress((i + 1) / len(gr_orders), text=f"통관조회 {i + 1}/{len(gr_orders)}")
            bar.empty()
            st.session_state["shipping_tax_scan_result"] = {"counts": counts, "fails": fails}
            st.rerun()
    with col_close:
        if st.button("닫기", width="stretch"):
            st.rerun()


def _render_gr_backfill() -> None:
    """퀵스타 '신청내역'을 긁어 기존 주문에 GR신청번호를 채워주는(백필) 도구.
    이게 채워져야 위 '관부가세 결재통보 조회'의 대상이 확 늘어납니다."""
    with st.expander("🔧 GR신청번호 백필 (관부가세 자동문자 대상 늘리기)", expanded=False):
        st.caption(
            "퀵스타 '신청내역'을 읽어, **운송장(CJ대한통운)·수취인명**으로 기존 주문에 "
            "GR신청번호를 채웁니다. 채워지면 관세청 통관조회(관부가세 결재통보 감지 → 8번 "
            "자동문자)의 대상이 크게 늘어납니다. **퀵스타 자동화 크롬이 로그인돼 있어야** 합니다."
        )
        missing = order_repository.list_orders_missing_gr()
        st.write(f"현재 GR번호 없이 운송장만 있는(백필 대상 후보) 주문: **{len(missing)}건**")

        max_pages = st.number_input(
            "최대 조회 페이지 수", min_value=5, max_value=1000, value=200, step=10,
            help="신청내역을 몇 페이지까지 넘겨볼지. 새 GR이 더 없으면 자동으로 멈춥니다. "
                 "건수가 많으면 몇 분 걸릴 수 있습니다.",
        )

        if quickstar_automation.get_status() != "READY":
            st.warning("퀵스타 자동화가 준비되지 않았습니다. 발송대기의 '퀵스타 연동'에서 자동화를 켜고 로그인한 뒤 실행해주세요.")
            return

        if st.button("퀵스타 신청내역으로 GR번호 백필 실행", type="primary", key="gr_backfill_run"):
            ph = st.empty()

            def _prog(page, total):
                ph.info(f"신청내역 조회 중... {page}페이지 · 누적 신청서 {total}건")

            with st.spinner("퀵스타 신청내역을 읽어 GR번호를 채우는 중입니다..."):
                report = gr_backfill_service.backfill_gr_numbers(max_pages=int(max_pages), progress=_prog)
            ph.empty()

            if not report.get("ok"):
                if report.get("need_login"):
                    st.error("퀵스타 로그인이 풀렸습니다. 자동화 크롬에서 다시 로그인한 뒤 실행해주세요.")
                else:
                    st.error(f"백필 실패: {report.get('msg')}")
                return

            st.success(
                f"백필 완료 — 신청내역 {report['apps']}건 읽음 · 대상 주문 {report['orders_scanned']}건 중 "
                f"**{report['updated']}건에 GR번호 채움** "
                f"(운송장 매칭 {report['matched_invoice']} · 이름 매칭 {report['matched_name']})"
            )
            if report["updated"] == 0:
                st.info(
                    "채워진 게 없습니다. 신청내역이 제대로 읽혔는지(페이지 수)와, 운송장이 서로 맞는지 확인이 필요합니다. "
                    f"(참고: 조회된 페이지 {report.get('pages')}, 신청내역 링크샘플 {report.get('page_links')})"
                )
            else:
                st.rerun()


def _render_invoice_change_scan(gr_orders: list, all_orders: list) -> None:
    """GR로 배대지의 '현재 송장'을 조회해, 쿠팡에 등록된 송장과 다른 주문을 찾아 일괄 변경.
    (배대지에서 재송장/분리배송 등으로 송장이 바뀌었는데 쿠팡엔 옛 송장이 남은 경우)"""
    import pandas as pd

    with st.container(border=True):
        st.markdown(
            "**🔄 배대지 송장 변경분 자동 반영** — GR신청번호로 배대지의 **현재 송장**을 조회해, "
            "쿠팡에 등록된 송장과 다른 주문을 찾아 한 번에 수정합니다."
        )
        if st.button(
            f"🔎 변경분 찾기 (대상 {len(gr_orders)}건)",
            disabled=not gr_orders, key="ship_scan_inv_changes",
            help="배송중 + GR신청번호가 있는 주문만 조회합니다.",
        ):
            prog = st.progress(0.0)

            def _p(done, total):
                try:
                    prog.progress(min(done / max(total, 1), 1.0))
                except Exception:
                    pass

            with st.spinner("배대지 송장 조회 중..."):
                st.session_state["ship_inv_changes"] = quickstar_invoice_service.scan_invoice_changes(
                    gr_orders, progress=_p
                )
            prog.empty()

        res = st.session_state.get("ship_inv_changes")
        if not res:
            return

        changes = res.get("changes") or []
        st.caption(
            f"조회 {res['scanned']}건 · GR있음 {res['with_gr']} · 가송장없음 {res['no_invoice']} · "
            f"업체직송제외 {res.get('skipped_direct', 0)} · 오류 {res['errors']} → **변경 대상 {len(changes)}건**"
        )
        if res.get("error_samples"):
            with st.expander(f"오류 {res['errors']}건 보기"):
                for s in res["error_samples"]:
                    st.text(s)

        if not changes:
            st.success("배대지와 쿠팡 송장이 모두 일치합니다. 변경할 주문이 없습니다.")
            return

        df = pd.DataFrame([{
            "주문번호": c["market_order_id"],
            "수령자": c["receiver"],
            "쿠팡 현재송장": c["old"] or "(없음)",
            "배대지 새송장": c["new"],
            "택배사": models.COURIER_CODES.get(c["courier_code"], c["courier_code"]),
        } for c in changes])
        st.dataframe(df, use_container_width=True, hide_index=True)

        confirm = st.checkbox(
            f"위 {len(changes)}건을 쿠팡에서 **실제로 송장 변경**합니다 (되돌리려면 다시 수정)",
            key="ship_inv_apply_confirm",
        )
        if st.button(
            f"🔄 {len(changes)}건 쿠팡에 송장 변경", type="primary",
            disabled=not confirm, key="ship_inv_apply_btn",
        ):
            by_id = {o["id"]: o for o in all_orders}
            ok = fail = 0
            fails = []
            prog2 = st.progress(0.0)
            for i, c in enumerate(changes):
                order = by_id.get(c["order_id"])
                if not order:
                    fail += 1
                    fails.append(f"{c['market_order_id']}: 주문을 찾지 못함")
                else:
                    try:
                        r = sync_service.update_invoice_for_shipping(order, c["courier_code"], c["new"])
                        if r.get("succeeded"):
                            ok += 1
                        else:
                            fail += 1
                            fails.append(f"{c['market_order_id']}: {r.get('message')}")
                    except Exception as e:  # noqa: BLE001
                        fail += 1
                        fails.append(f"{c['market_order_id']}: {e}")
                prog2.progress((i + 1) / len(changes))
            prog2.empty()
            st.session_state.pop("ship_inv_changes", None)
            if fail == 0:
                st.success(f"{ok}건 송장 변경 완료!")
            else:
                st.warning(f"{ok}건 성공 · {fail}건 실패")
                for f in fails[:10]:
                    st.text(f)
            st.rerun(scope="app")


def render() -> None:
    st.header("배송중")

    if st.session_state.get("shipping_tax_scan_result"):
        _r = st.session_state.pop("shipping_tax_scan_result")
        _c = _r["counts"]
        st.success(
            f"관부가세 통보 자동문자 — 발송 {_c['sent']}건 · 결재통보 없음 {_c['no_notice']}건 · "
            f"이미 보냄 {_c['already_sent']}건 · 전화없음 {_c['no_phone']}건 · 실패 {_c['fail']}건"
        )
        if _r["fails"]:
            st.error("일부 조회/발송 실패:\n" + "\n".join(f"- {x}" for x in _r["fails"][:8]))

    period_from, period_to = common.render_period_picker("shipping", "결제일시(시작)", "결제일시(종료)")
    st.caption(
        "※ 위 기간은 '수집하기'로 쿠팡에서 **가져올 범위**에만 적용됩니다. 아래 목록은 "
        "현재 배송중 상태인 주문을 **기간과 무관하게 전부** 보여줍니다."
    )
    # 수집은 백그라운드로 돌아, 도중에 다른 메뉴로 옮겨도 취소되지 않고 끝까지 진행됩니다.
    # advance_delivered=True: 배송완료된 배송중 주문을 자동으로 배송완료로 넘겨(전진),
    # 배송중 화면에 이미 배송완료된 옛 주문이 계속 쌓이는 문제를 막습니다.
    common.render_background_collect(
        period_from, period_to, key="shipping", stages=[models.WORK_STATUS_SHIPPING],
        advance_delivered=True,
    )

    common.render_live_count_banner(models.WORK_STATUS_SHIPPING)

    st.divider()

    orders = order_repository.list_orders_by_work_status(models.WORK_STATUS_SHIPPING)

    if not orders:
        st.info("배송중 주문이 없습니다. 위 '수집하기'를 누르거나, 발송대기 화면에서 송장을 등록해주세요.")
        return

    # 관부가세 결재통보 조회 → 8번 자동문자 (퀵스타 GR신청번호가 있는 주문만 대상).
    gr_orders = [o for o in orders if (o.get("quickstar_order_no") or "").strip()]
    if st.button(
        f"🔎 관부가세 결재통보 조회 & 8번 자동문자  (대상 {len(gr_orders)}건)",
        disabled=not gr_orders,
        help="퀵스타 통관조회로 관부가세 부과(결재통보)를 감지해, 해당 고객에게 8번 문구를 자동발송합니다. "
             "GR신청번호가 있는 주문만 조회됩니다.",
    ):
        _scan_customs_tax(gr_orders)
    if not gr_orders:
        st.caption("※ 지금 배송중에 퀵스타 GR신청번호가 있는 주문이 없어 조회 대상이 없습니다. (접수 시 GR번호가 저장돼야 조회됩니다)")

    _render_gr_backfill()

    # 배대지가 송장을 바꾼 주문을 GR로 감지해 쿠팡에 일괄 반영.
    _render_invoice_change_scan(gr_orders, orders)

    keyword = st.text_input("배송중 목록 검색", placeholder="주문번호, 수령자, 송장번호 등으로 검색")

    # 전화번호·개인통관고유부호는 실제 업무에 매번 필요해서 항상 그대로 보여줍니다.
    reveal = True

    all_rows = [common.build_full_row(order, idx + 1, reveal=reveal) for idx, order in enumerate(orders)]

    filtered_pairs = [(order, row) for order, row in zip(orders, all_rows) if common.matches_search(row, keyword)]

    if not filtered_pairs:
        st.info("검색 결과가 없습니다.")
        return

    # ---- 쿠팡 '주문상태'로 배송지시 / 배송중 하위탭 분리 ----
    #   work_status는 그대로 '배송중' 하나(안전, DB 이동 없음). 화면만 쿠팡 원본상태
    #   (market_status)로 가릅니다. 쿠팡에서 배송지시(DEPARTURE)→배송중(DELIVERING)으로
    #   바뀌면 '수집하기' 때 market_status가 갱신되어 자동으로 탭이 옮겨집니다.
    #   배송지시 = DEPARTURE, 배송중 = 그 외(DELIVERING 등) — 어느 탭에서도 안 빠지게 처리.
    _migrate_shipping_view_settings()

    def _is_departure(o):
        return (o.get("market_status") or "").upper() == "DEPARTURE"

    dep_pairs = [(o, r) for o, r in filtered_pairs if _is_departure(o)]
    del_pairs = [(o, r) for o, r in filtered_pairs if not _is_departure(o)]

    tab_dep, tab_del = st.tabs([
        f"🚚 배송지시 ({len(dep_pairs)})",
        f"📦 배송중 ({len(del_pairs)})",
    ])
    with tab_dep:
        st.caption("쿠팡 주문상태가 **배송지시**(발송 직후, 아직 집화 전)인 주문입니다.")
        _render_shipping_list(
            [p[0] for p in dep_pairs], [p[1] for p in dep_pairs],
            key="shipping_dep", reveal=reveal,
            empty_hint="배송지시 상태인 주문이 없습니다.",
        )
    with tab_del:
        st.caption("쿠팡 주문상태가 **배송중**(집화·이동 중)인 주문입니다.")
        _render_shipping_list(
            [p[0] for p in del_pairs], [p[1] for p in del_pairs],
            key="shipping_del", reveal=reveal,
            empty_hint="배송중 상태인 주문이 없습니다.",
        )
