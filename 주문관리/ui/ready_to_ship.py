# ==========================================================
# 발송대기 화면 (ui/ready_to_ship.py)
# ----------------------------------------------------------
# 신규주문에서 이동된 주문들이 여기 나타납니다.
#
# 쿠팡이 주문 단계에서 이미 통관정보 형식을 걸러주기 때문에, 이 화면에서는
# 형식검사(LocalFormatValidator) 없이 곧바로 관세청 공식검증만 합니다.
#
# "선택 주문 관세청 공식검증" 버튼 하나만 있습니다. 표 왼쪽 위 체크박스로
# 전체선택도 되기 때문에, 전체를 검증하고 싶으면 전체선택 후 이 버튼을 누르면 됩니다.
# 실제 관세청 유니패스 서버로 요청이 나가는 기능이라, 누르면 확인 창을 거칩니다.
# (참고: 형식이 심하게 틀린 값이 섞여 있어도 관세청 서버에 낭비성 요청을 보내지
#  않도록, UnipassCustomsValidator 내부에 최소한의 형식 확인은 남아있습니다)
#
# 통관검증을 통과한(발송 가능) 주문은 택배사+송장번호를 입력해서 실제로 쿠팡에
# 송장을 등록할 수 있습니다. 등록에 성공하면 우리 프로그램에서도 배송중으로
# 이동합니다. (쿠팡의 실제 주문 상태도 "배송지시"로 바뀝니다)
#
# 목록 표(엑셀 양식 전체 컬럼 + 너비/높이 조절 슬라이더 + 클릭 시 상세정보)는
# ui/common.py의 render_full_table/render_full_detail을 공용으로 씁니다.
#
# 아래 기능은 이번 단계 범위 밖이라 넣지 않았습니다.
#   - 쿠팡 최신정보 다시 조회 : fetch_order_detail 연결은 다음 단계에서
#   - 재검증 필요 주문만 보기 : 정보 변경 감지 로직은 다음 단계에서
# ==========================================================

from datetime import date

import pandas as pd
import streamlit as st

import models
from integrations import quickstar_automation, quickstar_client
from integrations.phone_link_sender import _copy_to_clipboard
from services.sms_sender import PhoneLinkError, send_message

# 퀵스타 페이지 주소.
# service_03_apply.php로 "직접" 들어가면 퀵스타가 "잘못된 요청"으로 막습니다
# (사이트 메뉴를 통해 정상 경로로 들어가야 함). 그래서 로그인된 메인 페이지를 열고,
# 거기서 판매자님이 '배송대행 신청' 메뉴로 들어가시게 합니다.
# 정확한 신청 페이지 주소를 확인하면 이 값을 그 주소로 바꾸면 됩니다.
QUICKSTAR_APPLY_URL = "https://quickstar.co.kr/"
from repositories import order_repository, settings_repository
from services import customs_service, gr_backfill_service, privacy, quickstar_invoice_service, sms_templates, sync_service
from ui import common, settings


@st.dialog("관세청 공식검증")
def _confirm_official_check(orders: list) -> None:
    """
    관세청 공식검증 버튼을 눌렀을 때 한 번 더 확인하는 창입니다.
    실제 관세청 서버로 요청이 나가는 기능이라, 누른 즉시 실행하지 않고 확인을 거칩니다.
    """
    st.write(f"선택한 **{len(orders)}건**을 관세청 유니패스 서버로 실제 조회하시겠습니까?")
    st.caption("이름·전화번호·통관고유부호·우편번호가 관세청 등록 정보와 일치하는지 실시간으로 확인합니다.")

    auto_error_sms = st.checkbox(
        "❗ 통관번호·우편번호가 틀리면 자동으로 오류문구 발송",
        value=True,
        key="rts_auto_error_sms",
        help=(
            "검증 결과 통관번호가 틀리면 1번(통관번호 오류문구), 우편번호가 틀리면 2번(우편번호 "
            "오류문구)을 해당 고객에게 자동으로 문자 발송합니다. 같은 정보로는 한 번만 보냅니다. "
            "(문자는 '휴대폰과 연결'로 나갑니다)"
        ),
    )

    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("검증 실행", type="primary", width="stretch"):
            match_count = 0
            mismatch_count = 0
            error_count = 0
            sms_sent = 0
            sms_failed = 0
            sms_already = 0
            sms_nophone = 0
            with st.spinner(f"관세청 서버에 {len(orders)}건 조회 중입니다..."):
                for order in orders:
                    result = customs_service.run_official_check(order)
                    if result.status_code == models.VALIDATION_STATUS_OFFICIAL_PASSED:
                        match_count += 1
                    elif result.status_code == models.VALIDATION_STATUS_OFFICIAL_MISMATCH:
                        mismatch_count += 1
                        # 통관번호/우편번호 불일치면 해당 오류문구를 자동 발송(중복은 알아서 건너뜀)
                        if auto_error_sms:
                            sms = customs_service.maybe_send_customs_error_sms(order, result)
                            reason = sms.get("reason")
                            if sms.get("sent"):
                                sms_sent += 1
                            elif reason == "send_failed":
                                sms_failed += 1
                            elif reason == "already_sent":
                                sms_already += 1
                            elif reason == "no_phone":
                                sms_nophone += 1
                    else:
                        error_count += 1
            summary = f"일치 {match_count}건 / 불일치 {mismatch_count}건 / 오류 {error_count}건"
            if auto_error_sms:
                # 이번에 실제로 나간 것 + 왜 안 나갔는지(이미발송/전화없음/실패)까지 흔적을 보여줍니다.
                summary += f" · 자동문자 발송 {sms_sent}건"
                extras = []
                if sms_already:
                    extras.append(f"이미발송 {sms_already}건")
                if sms_nophone:
                    extras.append(f"전화없음 {sms_nophone}건")
                if sms_failed:
                    extras.append(f"발송실패 {sms_failed}건")
                if extras:
                    summary += f" ({', '.join(extras)})"
            st.session_state["ready_to_ship_official_check_result"] = summary
            st.rerun()
    with col_no:
        if st.button("취소", width="stretch"):
            st.rerun()


@st.dialog("송장 등록")
def _confirm_register_invoice(order: dict, courier_code: str, invoice_number: str, shipping_date: str) -> None:
    """
    송장 등록 폼을 제출했을 때, 실제로 쿠팡에 등록하기 전에 한 번 더 확인하는 창입니다.
    """
    courier_name = models.COURIER_CODES.get(courier_code, courier_code)
    st.write(f"주문번호 **{order['market_order_id']}** 을(를) 아래 정보로 쿠팡에 송장 등록하시겠습니까?")
    st.write(f"- 택배사: {courier_name}")
    st.write(f"- 송장번호: {invoice_number}")
    st.write(f"- 출고예정일: {shipping_date}")
    st.caption("실제 쿠팡 주문 상태가 '배송지시'로 바뀝니다.")

    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("등록", type="primary", width="stretch"):
            with st.spinner("쿠팡에 송장을 등록하는 중입니다..."):
                result = sync_service.register_invoice_and_move_to_shipping(
                    order, courier_code, invoice_number, shipping_date
                )
            st.session_state["ready_to_ship_invoice_result"] = result
            st.rerun()
    with col_no:
        if st.button("취소", width="stretch"):
            st.rerun()


def _gr_is_on_or_after(gr: str, fill_date_iso: str) -> bool:
    """GR신청번호에 박혀 있는 접수날짜(GR + YYMMDD…)가 fill_date 이후인지 봅니다.
    옛 동명이인 접수를 잘못 저장하지 않기 위한 안전장치입니다.
    (형식을 못 읽으면 막지 않고 True — 이름 매칭이 대체로 맞으므로 과도한 차단 방지)"""
    try:
        digits = "".join(ch for ch in str(gr) if ch.isdigit())
        if len(digits) < 6 or not fill_date_iso:
            return True
        yy, mm, dd = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
        gr_date = date(2000 + yy, mm, dd)
        return gr_date >= date.fromisoformat(fill_date_iso)
    except (ValueError, TypeError):
        return True


@st.dialog("퀵스타 배대지 선택")
def _confirm_quickstar_submit(orders: list) -> None:
    """
    샵마인과 같은 방식입니다. 선택창(운송방식/수취인)에서 확인을 누르면, 상시 켜둔
    자동화 브라우저가 퀵스타 배송대행 신청 폼을 열고 수취인정보를 자동으로 채웁니다.
    (품목은 그 폼에서 직접 고르고 검토 후 제출하시면 됩니다)
    처음 한 번은 '퀵스타 자동화 시작'으로 로그인해야 합니다.
    """
    order = orders[0]  # 퀵스타 버튼은 한 주문씩 눌러서 열립니다.
    shipping = order.get("shipping") or {}
    key = order["id"]

    st.markdown(f"**주문번호 {order['market_order_id']}**")

    # ---- 자동화(상시 브라우저) 상태 ----
    status = quickstar_automation.get_status()
    if status != "READY":
        if status in ("STARTING", "LOGIN_WAIT"):
            st.info(
                "퀵스타 자동화 브라우저가 켜졌습니다. **그 크롬 창에서 로그인**해주세요. "
                "(자동로그인 체크 권장) 로그인되면 아래 '확인'이 동작합니다."
            )
            if st.button("로그인 됐는지 다시 확인", key=f"qs_recheck_{key}"):
                st.rerun()
        else:
            st.warning("퀵스타 자동화가 아직 시작되지 않았습니다. 아래 버튼으로 시작하고 로그인하세요.")
            if st.button("🚀 퀵스타 자동화 시작 (최초 1회 로그인)", type="primary", key=f"qs_start_{key}"):
                quickstar_automation.start_daemon()
                st.rerun()
        if st.button("닫기", key=f"qs_close_early_{key}"):
            st.rerun()
        return

    st.success("퀵스타 자동화 준비됨 (로그인 상태)")

    st.markdown("**운송방식 선택**")
    transport_codes = list(quickstar_client.TRANSPORT_METHODS.keys())
    transport = st.radio(
        "운송방식",
        options=transport_codes,
        index=transport_codes.index(quickstar_client.DEFAULT_TRANSPORT_NO),
        format_func=lambda no: quickstar_client.TRANSPORT_METHODS[no],
        label_visibility="collapsed",
        key=f"qs_tr_{key}",
    )

    st.markdown("**수취인 선택**")
    who = st.radio("수취인", ["수령자", "구매자"], horizontal=True, label_visibility="collapsed", key=f"qs_who_{key}")

    col_ok, col_close = st.columns(2)
    with col_ok:
        if st.button("확인 (자동입력)", type="primary", width="stretch"):
            if who == "수령자":
                name = shipping.get("receiver_name") or ""
                phone = shipping.get("customs_phone") or shipping.get("receiver_phone_raw") or ""
            else:
                name = order.get("orderer_name") or ""
                phone = order.get("orderer_phone") or ""
            receiver = {
                "name": name,
                "phone": phone,
                "zip": shipping.get("zip_code") or "",
                "addr1": shipping.get("address_basic") or "",
                "addr2": shipping.get("address_detail") or "",
                "pccc": shipping.get("pccc") or "",
            }
            with st.spinner("자동화 브라우저가 퀵스타 폼을 열고 수취인정보를 채우는 중..."):
                result = quickstar_automation.fill_receiver(receiver, transport)
            st.session_state["ready_to_ship_quickstar_result"] = {
                "auto": True,
                "ok": result.get("ok"),
                "msg": result.get("msg"),
                "market_order_id": order["market_order_id"],
            }
            st.rerun()
    with col_close:
        if st.button("닫기", width="stretch"):
            st.rerun()


@st.dialog("송장 일괄 등록")
def _confirm_bulk_register_invoice(rows: list) -> None:
    """
    표에 직접 입력한 택배사/송장번호를 여러 건 한꺼번에 쿠팡에 등록하기 전에
    한 번 더 확인하는 창입니다. 실제 쿠팡 주문 상태가 바뀌는 작업이라, 무엇이
    등록될지 목록으로 다 보여주고 확인을 받습니다.
    """
    st.write(f"아래 **{len(rows)}건**을 쿠팡에 송장 등록하시겠습니까?")
    preview = pd.DataFrame(
        [
            {
                "주문번호": row["order"]["market_order_id"],
                "택배사": models.COURIER_CODES.get(row["택배사코드"], row["택배사코드"]),
                "송장번호": row["송장번호"],
            }
            for row in rows
        ]
    )
    st.dataframe(preview, hide_index=True, width="stretch")
    st.caption("실제 쿠팡 주문 상태가 '배송지시'로 바뀌고, 우리 프로그램에서도 배송중으로 이동합니다.")

    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("등록", type="primary", width="stretch"):
            today = date.today().isoformat()
            succeeded = 0
            failures = []
            with st.spinner(f"쿠팡에 {len(rows)}건 송장을 등록하는 중입니다..."):
                for row in rows:
                    result = sync_service.register_invoice_and_move_to_shipping(
                        row["order"], row["택배사코드"], row["송장번호"], today
                    )
                    if result["succeeded"]:
                        succeeded += 1
                    else:
                        failures.append(f"{row['order']['market_order_id']}: {result['message']}")
            st.session_state["ready_to_ship_bulk_invoice_result"] = {
                "succeeded": succeeded,
                "failures": failures,
            }
            st.rerun()
    with col_no:
        if st.button("취소", width="stretch"):
            st.rerun()


def _render_invoice_form(order: dict) -> None:
    shipping = order["shipping"] or {}
    validation_status = shipping.get("validation_status")
    shippable = validation_status in models.SHIPPABLE_VALIDATION_STATUSES

    st.divider()
    st.subheader("송장 등록 (배송중으로 이동)")

    if not shippable:
        st.warning(
            f"이 주문은 아직 발송할 수 없습니다 (통관검증 상태: {validation_status or '미검증'}). "
            "관세청 공식검증을 통과해야 송장을 등록할 수 있습니다."
        )
        return

    if order["work_status"] != models.WORK_STATUS_READY_TO_SHIP:
        st.info(f"이미 처리된 주문입니다 (현재 상태: {order['work_status']}).")
        return

    with st.form(f"invoice_form_{order['id']}"):
        courier_options = list(models.COURIER_CODES.keys())
        default_courier = settings.get_default_courier_code()
        courier_code = st.selectbox(
            "택배사",
            options=courier_options,
            index=courier_options.index(default_courier),
            format_func=lambda code: f"{models.COURIER_CODES[code]} ({code})",
        )
        invoice_number = st.text_input("송장번호")
        shipping_date = st.date_input("출고예정일", value=date.today())
        submitted = st.form_submit_button("송장 등록하고 배송중으로 이동", type="primary")

        if submitted:
            if not invoice_number.strip():
                st.error("송장번호를 입력해주세요.")
            else:
                _confirm_register_invoice(order, courier_code, invoice_number.strip(), shipping_date.isoformat())


@st.dialog("문자 발송")
def _confirm_send_sms(order: dict, phone: str, message: str) -> None:
    """
    "문자 발송" 버튼을 눌렀을 때 한 번 더 확인하는 창입니다. Phone Link
    화면을 자동으로 조작해서 실제로 문자가 나가는 기능이라, 받는 번호와
    문구를 마지막으로 한 번 더 보여주고 확인을 거칩니다.
    """
    st.write(f"주문번호 **{order['market_order_id']}** 고객에게 아래 문자를 발송하시겠습니까?")
    st.write(f"**받는 번호**: {phone}")
    st.text_area("발송할 문구", value=message, height=200, disabled=True, key="rts_sms_confirm_preview")
    st.caption(
        "설정된 문자 발송 경로(폰 SMS 게이트웨이 또는 Phone Link)로 보냅니다. "
        "실패하면 아래에 사유가 표시됩니다."
    )

    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("발송", type="primary", width="stretch"):
            with st.spinner("문자 발송 중입니다..."):
                try:
                    send_message(phone, message)
                except PhoneLinkError as error:
                    order_repository.record_sms_send(
                        order["id"], order.get("market_order_id"), phone, "manual",
                        kind="manual", success=False, error_message=str(error),
                    )
                    st.session_state["ready_to_ship_sms_result"] = {
                        "succeeded": False,
                        "message": str(error),
                    }
                else:
                    order_repository.record_sms_send(
                        order["id"], order.get("market_order_id"), phone, "manual",
                        kind="manual", success=True,
                    )
                    st.session_state["ready_to_ship_sms_result"] = {"succeeded": True, "message": None}
            st.rerun()
    with col_no:
        if st.button("취소", width="stretch"):
            st.rerun()


def _order_phone(order: dict) -> str:
    """이 주문의 문자 받을 번호(통관 전화 우선, 없으면 수령자 전화)."""
    shipping = order.get("shipping") or {}
    return shipping.get("customs_phone") or shipping.get("receiver_phone_raw") or ""


@st.dialog("문자 일괄 발송")
def _confirm_bulk_sms(orders: list, template_key: str) -> None:
    """체크한 여러 주문에게 고른 문구를 한 번에 발송하기 전, 마지막으로 확인하는 창입니다.
    전화번호 없는 주문은 자동 제외하고, 주문마다 상품명 등은 각자 데이터로 채워 보냅니다."""
    label = sms_templates.TEMPLATES[template_key]["label"]
    targets = [(o, _order_phone(o)) for o in orders if _order_phone(o)]
    no_phone = [o for o in orders if not _order_phone(o)]

    st.write(f"체크한 **{len(orders)}건** 중 **{len(targets)}건**에게 **[{label}]** 문자를 발송합니다.")
    if no_phone:
        st.warning(f"전화번호가 없는 {len(no_phone)}건은 발송에서 제외됩니다.")
    if targets:
        st.text_area(
            "발송 문구 미리보기 (첫 주문 기준 — 주문마다 상품명·고객명 등은 각자 자동 반영)",
            value=sms_templates.render_template(template_key, targets[0][0]),
            height=180, disabled=True, key="rts_bulk_sms_preview",
        )
        with st.expander(f"받는 사람 {len(targets)}명 보기"):
            for o, ph in targets:
                st.caption(f"- {o.get('market_order_id')} · {privacy.mask_phone(ph)}")
    st.caption("설정된 문자 발송 경로(폰 SMS 게이트웨이 또는 Phone Link)로 보냅니다.")

    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button(f"✅ {len(targets)}건 발송", type="primary", width="stretch", disabled=not targets):
            ok = 0
            fails = []
            with st.spinner(f"문자 발송 중입니다... ({len(targets)}건)"):
                for o, phone in targets:
                    message = sms_templates.render_template(template_key, o)
                    try:
                        send_message(phone, message)
                    except Exception as error:  # noqa: BLE001 (PhoneLinkError 포함)
                        order_repository.record_sms_send(
                            o["id"], o.get("market_order_id"), phone, template_key,
                            kind="manual", success=False, error_message=str(error),
                        )
                        fails.append(f"{o.get('market_order_id')}: {error}")
                    else:
                        order_repository.record_sms_send(
                            o["id"], o.get("market_order_id"), phone, template_key,
                            kind="manual", success=True,
                        )
                        ok += 1
            st.session_state["ready_to_ship_bulk_sms_result"] = {
                "ok": ok, "fail": len(fails), "fails": fails[:10], "skipped": len(no_phone),
            }
            st.rerun()
    with col_no:
        if st.button("취소", width="stretch"):
            st.rerun()


_SMS_TEMPLATE_LABELS = {
    "customs_error": "통관오류문구(1번)",
    "zipcode_error": "우편번호오류문구(2번)",
    "customs_tax_notice": "관부가세통보(8번)",
    "manual": "수동발송",
}


def _render_sms_history(order: dict) -> None:
    """이 주문의 자동/수동 문자 발송 이력을 보여줍니다(발송 흔적 확인용)."""
    logs = order_repository.list_sms_sends(order["id"])
    with st.expander(f"📨 문자 발송 이력 ({len(logs)}건)", expanded=bool(logs)):
        if not logs:
            # 이번 업데이트 이전에 보낸 건은 로그가 없어서, 예전 dedup 기록으로 안내만 합니다.
            if order_repository.get_customs_error_sms_sent(order["id"]):
                st.caption("과거에 통관오류 문구를 보낸 기록이 있습니다. (상세 이력은 이번 업데이트 이후 발송분부터 남습니다)")
            else:
                st.caption("이 주문에 발송된 문자 이력이 없습니다.")
            return
        for lg in logs:
            when = (lg.get("sent_at") or "").replace("T", " ")
            tmpl = _SMS_TEMPLATE_LABELS.get(lg.get("template_key"), lg.get("template_key") or "-")
            kind = "자동" if lg.get("kind") == "auto" else "수동"
            if lg.get("success"):
                st.markdown(f"- ✅ **{when}** · {kind} · {tmpl} → {lg.get('phone')}")
            else:
                st.markdown(
                    f"- ❌ **{when}** · {kind} · {tmpl} → {lg.get('phone')}  \n"
                    f"　실패 사유: {lg.get('error_message')}"
                )


def _render_sms_form(order: dict, reveal: bool) -> None:
    shipping = order["shipping"] or {}
    phone = shipping.get("customs_phone") or shipping.get("receiver_phone_raw") or ""

    st.divider()
    st.subheader("문자 발송")

    if not phone:
        st.warning("이 주문은 전화번호 정보가 없어 문자를 보낼 수 없습니다.")
        return

    template_key = st.selectbox(
        "문구 선택",
        options=list(sms_templates.TEMPLATES.keys()),
        format_func=lambda key: sms_templates.TEMPLATES[key]["label"],
        key=f"sms_template_{order['id']}",
    )
    rendered = sms_templates.render_template(template_key, order)
    # 텍스트 상자 key에 '선택한 문구'를 포함시킵니다. 이게 없으면 문구를 바꿔도
    # 세션에 저장된 첫 내용이 그대로 남아(위젯 key 우선) 내용이 안 바뀝니다.
    message = st.text_area(
        "발송 내용 (필요하면 직접 수정할 수 있습니다)",
        value=rendered,
        height=220,
        key=f"sms_message_{order['id']}_{template_key}",
    )
    phone_display = phone if reveal else privacy.mask_phone(phone)
    st.caption(f"받는 번호: {phone_display}")

    if st.button("문자 발송", key=f"sms_send_{order['id']}"):
        if not message.strip():
            st.error("발송할 문구가 비어있습니다.")
        else:
            common.open_dialog_deferred(_confirm_send_sms, order, phone, message)


def _run_gr_fill_ready_to_ship(selected_orders=None) -> None:
    """'🧩 GR번호 채우기' 버튼 클릭 시 실행: GR이 없는 발송대기 주문을 퀵스타 신청내역과
    이름(수령자/구매자)+전화(실전화 포함)+통관번호로 매칭해 GR을 채웁니다.
    ★표에서 체크한 주문이 있으면 그 주문만, 없으면 발송대기 전체를 대상으로 합니다."""
    missing = order_repository.list_ready_to_ship_missing_gr()
    if not missing:
        st.info("GR을 채울 발송대기 주문이 없습니다. (모두 이미 GR이 있습니다)")
        return
    # 체크한 주문이 있으면 그 중 'GR 없는' 것만 대상으로 좁힘
    only_ids = None
    if selected_orders:
        _sel = {o["id"] for o in selected_orders}
        target = [m for m in missing if m["id"] in _sel]
        if not target:
            st.info("체크한 주문은 이미 모두 GR이 있습니다. (GR 없는 주문을 체크하거나, 체크 없이 누르면 발송대기 전체가 대상입니다)")
            return
        only_ids = [m["id"] for m in target]
    n_target = len(only_ids) if only_ids is not None else len(missing)
    _scope_txt = f"체크한 {n_target}건" if only_ids is not None else f"발송대기 전체 {n_target}건"

    if quickstar_automation.get_status() != "READY":
        st.warning(
            f"GR을 채울 대상 {n_target}건이 있지만, **퀵스타 자동화가 준비되지 않았습니다.** "
            "표의 '퀵스타 연동'에서 자동화를 켜고 로그인한 뒤 다시 눌러주세요. (자동화 크롬 창은 닫지 마세요)"
        )
        return

    ph = st.empty()

    def _prog(page, total):
        ph.info(f"신청내역 조회 중... {page}페이지 · 누적 신청서 {total}건")

    with st.spinner(f"퀵스타 신청내역(최근 1주일)을 읽어 {_scope_txt}에 GR번호를 채우는 중입니다..."):
        # 조회 도중 배경 자동수집 완료 새로고침이 이 긴 작업을 끊지 않게 억제.
        with common.suppress_bg_rerun():
            report = gr_backfill_service.backfill_gr_for_ready_to_ship(days_back=7, progress=_prog, only_order_ids=only_ids)
    ph.empty()

    if not report.get("ok"):
        if report.get("need_login"):
            st.error("퀵스타 로그인이 풀렸습니다. 자동화 크롬에서 다시 로그인한 뒤 눌러주세요.")
        elif "closed" in (report.get("msg") or "").lower():
            st.error("퀵스타 자동화 크롬 창이 닫혀 조회할 수 없습니다. '퀵스타 연동'에서 자동화를 다시 켜고 로그인한 뒤 눌러주세요.")
        else:
            st.error(f"GR 채우기 실패: {report.get('msg')}")
        return

    st.success(
        f"GR 채우기 완료 — 최근 1주일 신청내역 {report['apps']}건 읽음 · 대상 발송대기 {report['orders_scanned']}건 중 "
        f"**{report['updated']}건에 GR 채움** "
        f"(수령자명 {report['matched_receiver']} · 구매자명 {report['matched_buyer']} · 정밀조회 {report.get('matched_recinfo', 0)}). "
        f"· 동명이인 확정불가 {report['ambiguous']}건 · 신청내역에 이름 없음 {report['no_match']}건"
    )
    if report["updated"]:
        st.rerun()
    elif report["no_match"] and report["ambiguous"] == 0:
        st.info(
            "채워진 게 없습니다. 대부분 '최근 1주일 신청내역'에 그 이름이 없어서일 수 있습니다. "
            "배대지 신청서를 1주일보다 전에 만드셨다면 알려주세요(기간을 늘리겠습니다)."
        )


def _suppress_bg_on_click() -> None:
    """★버튼 on_click 콜백. Streamlit은 on_click 콜백을 '스크립트 본문 실행 전'에 돌립니다.
    여기서 배경수집 억제 수명(ttl)을 미리 켜두면, 이어지는 실행(클릭 처리)에서 수집-완료
    fragment가 st.rerun(scope='app')으로 이 버튼의 핸들러를 preempt(중단)하지 못합니다.
    (송장 자동입력/ GR 채우기 등 긴 작업이 '갑자기 안 되던' 경쟁조건의 근본 차단)"""
    common.arm_bg_rerun_suppression()


def render() -> None:
    st.header("발송대기")

    if st.session_state.get("ready_to_ship_official_check_result"):
        summary = st.session_state.pop("ready_to_ship_official_check_result")
        st.success(f"관세청 공식검증 완료 — {summary}")

    if st.session_state.get("ready_to_ship_invoice_result"):
        result = st.session_state.pop("ready_to_ship_invoice_result")
        if result["succeeded"]:
            st.success("송장을 등록하고 배송중으로 이동했습니다.")
        else:
            st.error(f"송장 등록에 실패했습니다: {result['message']}")

    if st.session_state.get("ready_to_ship_bulk_invoice_result"):
        result = st.session_state.pop("ready_to_ship_bulk_invoice_result")
        if result["succeeded"]:
            st.success(f"{result['succeeded']}건의 송장을 등록하고 배송중으로 이동했습니다.")
        if result["failures"]:
            failure_lines = "\n".join(f"- {line}" for line in result["failures"])
            st.error(f"{len(result['failures'])}건은 등록에 실패했습니다.\n{failure_lines}")

    if st.session_state.get("ready_to_ship_quickstar_result"):
        result = st.session_state.pop("ready_to_ship_quickstar_result")
        if result.get("auto"):
            if result.get("ok"):
                st.success(
                    f"주문 {result['market_order_id']}: 자동화 브라우저에 배송대행 신청 폼이 열리고 "
                    "**수취인정보가 자동으로 채워졌습니다.** 그 창에서 품목을 넣고 검토 후 제출하세요."
                )
            else:
                st.error(f"자동입력 실패: {result.get('msg')}")

    if st.session_state.get("ready_to_ship_sms_result"):
        result = st.session_state.pop("ready_to_ship_sms_result")
        if result["succeeded"]:
            st.success("Phone Link에서 전송 버튼까지 눌렀습니다. (실제 도착 여부는 폰에서 확인해주세요)")
        else:
            st.error(f"문자 발송 자동화 실패: {result['message']} (문구는 클립보드에 복사되어 있습니다)")

    if st.session_state.get("ready_to_ship_bulk_sms_result"):
        _r = st.session_state.pop("ready_to_ship_bulk_sms_result")
        _extra = (f" · 번호없어 제외 {_r['skipped']}건" if _r.get("skipped") else "")
        if _r["fail"] == 0:
            st.success(f"문자 일괄 발송 완료 — {_r['ok']}건 발송{_extra}")
        else:
            st.warning(f"문자 일괄 발송 — 성공 {_r['ok']}건 · 실패 {_r['fail']}건{_extra}")
            for _f in _r["fails"]:
                st.text(f"- {_f}")

    # ------------------------------------------------------
    # 상단: 결제일시 기간 + 수집하기
    # 쿠팡에서 이미 "상품준비중"(INSTRUCT) 상태인 주문을 직접 가져옵니다.
    # 신규주문에서 '발송대기로 이동'을 거치지 않고 쿠팡 Wing에서 직접 처리한
    # 주문도 여기서 다시 수집하면 나타납니다.
    # ------------------------------------------------------
    period_from, period_to = common.render_period_picker("rts", "결제일시(시작)", "결제일시(종료)")
    # 수집은 백그라운드로 돌아, 도중에 다른 메뉴로 옮겨도 취소되지 않고 끝까지 진행됩니다.
    # (발송대기만이 아니라 전 단계를 함께 최신화합니다.)
    common.render_background_collect(
        period_from, period_to, key="rts",
        stages=[models.WORK_STATUS_READY_TO_SHIP], reconcile=True,
    )

    common.render_live_count_banner(models.WORK_STATUS_READY_TO_SHIP)

    st.divider()

    orders = order_repository.list_orders_by_work_status(models.WORK_STATUS_READY_TO_SHIP)

    if not orders:
        st.info("발송대기 주문이 없습니다. 위 '수집하기'를 누르거나, 신규주문 화면에서 '선택 주문 발송대기로 이동'을 사용해주세요.")
        return

    col_search, col_f1, col_f2 = st.columns([3, 1, 1])
    with col_search:
        keyword = st.text_input("발송대기 목록 검색", placeholder="주문번호, 수령자 등으로 검색")
    with col_f1:
        st.write("")
        problem_only = st.checkbox("불일치·오류만 보기")
    with col_f2:
        st.write("")
        unchecked_only = st.checkbox("통관검사 안 한 것만 보기", help="아직 관세청 공식검증을 안 한(미검증) 주문만 보여줍니다.")

    # 전화번호·개인통관고유부호는 실제 발송 업무에 매번 필요해서 항상 그대로 보여줍니다.
    reveal = True
    all_rows = [common.build_full_row(order, idx + 1, reveal=reveal) for idx, order in enumerate(orders)]

    filtered_pairs = []
    for order, row in zip(orders, all_rows):
        if not common.matches_search(row, keyword):
            continue
        if problem_only and row["통관검증 상태"] in (models.VALIDATION_STATUS_OFFICIAL_PASSED, "미검증"):
            continue
        if unchecked_only and row["통관검증 상태"] != "미검증":
            continue
        filtered_pairs.append((order, row))

    if not filtered_pairs:
        st.info("조건에 맞는 주문이 없습니다.")
        return

    filtered_orders = [pair[0] for pair in filtered_pairs]
    filtered_rows = [pair[1] for pair in filtered_pairs]

    # 발송대기 전용 도메인 버튼(관세청 공식검증 / 퀵스타 접수) + 샵마인식 액션 바(주 버튼 = 발송처리).
    # ★표 안 '퀵스타 연동' 버튼은 클릭 인식이 불안정해서, 여기 '확실히 되는' 메인 버튼을 둡니다.
    _b1, _b2 = st.columns(2)
    with _b1:
        official_selected_clicked = st.button(
            "🔎 선택 주문 관세청 공식검증", width="stretch",
            help="표 왼쪽 체크박스로 선택한 주문들을 관세청에 검증합니다. (머리글 체크박스로 전체선택)",
        )
    with _b2:
        quickstar_selected_clicked = st.button(
            "🛒 선택 주문 퀵스타 배대지 접수", width="stretch",
            help="표 왼쪽 체크박스로 주문 1건을 선택하고 누르면 퀵스타 접수 창이 확실히 뜹니다. (표 안 '퀵스타 연동' 버튼 대체)",
        )
    _gb1, _gb2, _gb4, _gb3 = st.columns(4)
    with _gb4:
        gr_clear_clicked = st.button(
            "🗑 GR 지우기", width="stretch", on_click=_suppress_bg_on_click,
            help="잘못 넣은 GR신청번호를 지웁니다. 표 왼쪽 체크박스로 지울 주문을 고르고 누르면 "
                 "그 주문들의 GR과 (그 GR로 조회했던) 가송장까지 함께 비웁니다. 그 뒤 다시 넣을 수 있습니다.",
        )
    with _gb1:
        gr_fill_clicked = st.button(
            "🧩 GR 채우기", width="stretch", on_click=_suppress_bg_on_click,
            help="퀵스타 신청내역(최근 1주일)을 읽어, GR 없는 발송대기 주문을 이름(수령자/구매자)+전화+통관번호로 "
                 "매칭해 GR을 채웁니다. 표에서 체크한 주문이 있으면 그것만, 없으면 발송대기 전체가 대상. "
                 "채워지면 송장번호(가송장)가 자동으로 뜸. 퀵스타 자동화 크롬 로그인 필요.",
        )
    with _gb2:
        gr_save_clicked = st.button(
            "💾 GR 직접입력 저장", width="stretch", on_click=_suppress_bg_on_click,
            help="자동매칭이 안 되는 주문(같은 사람의 복수 주문 등)은 표의 '퀵스타 신청번호' 칸을 더블클릭해 GR을 "
                 "붙여넣고 이 버튼을 누르면 저장됩니다. (자동화/로그인 불필요) 저장되면 송장번호(가송장)가 뜸.",
        )
    with _gb3:
        autofill_invoice_clicked = st.button(
            "🔄 송장 자동입력", width="stretch", on_click=_suppress_bg_on_click,
            help="GR 있는 주문들의 배대지 가송장번호를 조회해 표의 '택배사·송장번호' 칸을 채웁니다(택배사=CJ대한통운). "
                 "배대지 접수하면 가송장이 바로 생성돼 출고 전이라도 채워짐. 확인 후 '발송처리'로 쿠팡 등록.",
        )
    do_ship = common.render_shopmine_action_bar(
        "rts", filtered_orders,
        primary_label="🚚 발송처리",
        primary_help="표에 입력한 택배사·송장번호를 쿠팡에 등록하고 배송중으로 넘깁니다.",
    )

    # 📨 선택 주문 문자 일괄 발송: 표에서 행을 체크하고, 보낼 문구를 골라 한 번에 보냅니다.
    _sms_col1, _sms_col2 = st.columns([3, 2])
    with _sms_col1:
        bulk_sms_template = st.selectbox(
            "문자 문구 선택",
            options=list(sms_templates.TEMPLATES.keys()),
            format_func=lambda k: sms_templates.TEMPLATES[k]["label"],
            key="rts_bulk_sms_template",
        )
    with _sms_col2:
        st.write("")
        bulk_sms_clicked = st.button(
            "📨 선택 주문 문자 발송", width="stretch", on_click=_suppress_bg_on_click,
            help="표 왼쪽 체크박스로 주문을 고르고 누르면, 위에서 고른 문구를 그 주문들에게 한 번에 발송합니다. "
                 "(전화번호 없는 주문은 자동 제외, 주문마다 상품명 등은 각자 자동 반영)",
        )

    # 정렬(주문일 오름차순 등) — 고른 정렬을 DB에 저장해, 다른 단계 갔다 와도/앱 껐다 켜도 유지됩니다.
    _sort_options = [c for c in ["주문일(약식)", "수령자", "주문번호", "통관검증 상태", "발송 가능 여부", "상품명"]
                     if filtered_rows and c in filtered_rows[0]]
    if _sort_options:
        _saved_sc = settings_repository.get_setting("sort_col:rts", "주문일(약식)")
        _saved_sd = settings_repository.get_setting("sort_dir:rts", "내림차순")
        # ★퀵스타 연동 등 'fragment 재실행'으로 이 정렬 선택 위젯값이 초기화되면,
        #   미러(비-위젯 세션키, fragment 재실행에도 안 사라짐) > 저장값에서 되살립니다.
        _sc_mirror, _sd_mirror = "rts__sort_col_mirror", "rts__sort_dir_mirror"
        if "rts_sort_col" not in st.session_state:
            _v = st.session_state.get(_sc_mirror) or _saved_sc
            st.session_state["rts_sort_col"] = _v if _v in _sort_options else _sort_options[0]
        if "rts_sort_dir" not in st.session_state:
            _v = st.session_state.get(_sd_mirror) or _saved_sd
            st.session_state["rts_sort_dir"] = _v if _v in ("오름차순", "내림차순") else "내림차순"
        _cs1, _cs2, _ = st.columns([1, 1, 3])
        with _cs1:
            sort_col = st.selectbox("정렬 기준", _sort_options, key="rts_sort_col")
        with _cs2:
            sort_dir = st.selectbox("정렬 방향", ["오름차순", "내림차순"], key="rts_sort_dir")
        # 미러에 현재값 복사(GC돼도 되살림) + 저장(껐다 켜도 유지)
        st.session_state[_sc_mirror] = sort_col
        st.session_state[_sd_mirror] = sort_dir
        # ★항상 저장: 표(aggrid)가 이 값을 읽어 머리글 정렬 화살표를 그리므로, 기본값이어도
        #   DB에 있어야 화살표가 처음부터 뜨고 새로고침 후에도 안 풀립니다.
        if settings_repository.get_setting("sort_col:rts") != sort_col:
            settings_repository.set_setting("sort_col:rts", sort_col)
        if settings_repository.get_setting("sort_dir:rts") != sort_dir:
            settings_repository.set_setting("sort_dir:rts", sort_dir)
        # ★드롭다운(정렬 기준/방향)을 '바꾸면' 머리글 클릭 정렬(aggrid_sort)을 해제 → 드롭다운이 우선.
        #   (머리글 정렬과 드롭다운 정렬이 안 충돌하게: 마지막에 쓴 게 이김. 첫 렌더는 해제 안 함.)
        _dd_sig = f"{sort_col}|{sort_dir}"
        if "rts_sort_dropdown_sig" not in st.session_state:
            st.session_state["rts_sort_dropdown_sig"] = _dd_sig
        elif st.session_state["rts_sort_dropdown_sig"] != _dd_sig:
            st.session_state["rts_sort_dropdown_sig"] = _dd_sig
            settings_repository.set_setting("aggrid_sort:rts", "")
        # 드롭다운 정렬(기본/폴백). 머리글을 클릭했으면 표(aggrid)가 aggrid_sort로 다시 정렬해 덮어씀.
        _paired = sorted(
            zip(filtered_rows, filtered_orders),
            key=lambda p: (p[0].get(sort_col) or ""), reverse=(sort_dir == "내림차순"),
        )
        filtered_rows = [p[0] for p in _paired]
        filtered_orders = [p[1] for p in _paired]

    # AG-Grid 표: 왼쪽 체크박스(+머리글 전체선택), 마우스로 컬럼/행(순번) 드래그 순서변경(자동저장).
    import pandas as pd

    from ui import aggrid_table

    grid_df = pd.DataFrame(filtered_rows)
    # '퀵스타 신청번호'(=GR, build_full_row가 넣음)를 앞쪽으로 빼고 편집 가능하게 함 → 여기 직접 입력.
    _front = ["No", "택배사", "송장번호", "퀵스타 신청번호", "통관검증 상태", "발송 가능 여부", "수령자", "주문번호", "상품명", "퀵스타"]
    # 퀵스타 컬럼 값 = 접수번호(있으면). 표에서 줄마다 '퀵스타 연동' 버튼으로 렌더됩니다.
    grid_df["퀵스타"] = [o.get("quickstar_order_no") or "" for o in filtered_orders]
    _cols = [c for c in _front if c in grid_df.columns] + [c for c in grid_df.columns if c not in _front]
    grid_df = grid_df[_cols]
    # 택배사 기본값: 주문에 지정된 택배사가 있으면 그걸, 없으면 기본 택배사(CJ대한통운)로 채웁니다.
    _default_courier_label = common.courier_label(settings.get_default_courier_code())
    grid_df["택배사"] = [
        common.courier_label(o["delivery_company_code"]) if o.get("delivery_company_code") else _default_courier_label
        for o in filtered_orders
    ]

    # ★표에 입력했던 택배사·송장번호를 다시 덮어써서, 다른 작업(퀵스타 연동/정렬/상세 등)으로
    #   화면이 새로고침돼도 값이 유지되게 합니다. (주문번호로 매칭 → 정렬이 바뀌어도 유지)
    #   앱을 완전히 끄면 세션이 비워져 사라집니다(요청대로).
    _prev_edits = st.session_state.get("rts_edited_records", []) or []
    _edit_by_moid = {}
    for _r in _prev_edits:
        _moid = str(_r.get("주문번호") or "").strip()
        if _moid:
            _edit_by_moid[_moid] = (_r.get("택배사"), _r.get("송장번호"), _r.get("퀵스타 신청번호"))
    if _edit_by_moid and "주문번호" in grid_df.columns:
        for _i in grid_df.index:
            _moid = str(grid_df.at[_i, "주문번호"]).strip()
            _e = _edit_by_moid.get(_moid)
            if not _e:
                continue
            _cr, _iv, _grv = _e
            # ★"-"·빈칸은 '편집 안 함'으로 취급해 덮어쓰지 않음 → build_full_row가 넣은 가송장
            #   (quickstar_invoice)이 미러의 옛 "-"에 지워지던 문제 방지.
            if _iv not in (None, "", "-") and str(_iv).lower() != "nan":
                grid_df.at[_i, "송장번호"] = _iv
            if _cr not in (None, "", common.COURIER_UNSET_LABEL) and str(_cr).lower() != "nan":
                grid_df.at[_i, "택배사"] = _cr
            if _grv not in (None, "", "-") and str(_grv).lower() != "nan":
                grid_df.at[_i, "퀵스타 신청번호"] = _grv

    # 표 안에서 택배사(드롭다운)·송장번호·퀵스타 신청번호(GR 직접입력) 편집 가능하게 설정.
    _courier_labels = [common.COURIER_UNSET_LABEL] + list(common.COURIER_LABEL_TO_CODE.keys())
    _editable = {
        "택배사": {"cellEditor": "agSelectCellEditor", "cellEditorParams": {"values": _courier_labels}},
        "송장번호": {"cellEditor": "agTextCellEditor"},
        "퀵스타 신청번호": {"cellEditor": "agTextCellEditor"},
    }
    # 상세(오른쪽 옆) 렌더 함수 — 체크한 주문의 상세 + 검증/퀵스타 버튼 + 문자 발송.
    def _rts_detail(order):
        shipping = order.get("shipping") or {}
        st.subheader(f"상세내역 — {shipping.get('receiver_name') or order['market_order_id']}")
        common.render_full_detail(order, reveal)
        # ★다이얼로그는 fragment 안에서 열면 안 떠서, 공통 헬퍼로 예약 → 메인에서 엽니다.
        if st.button("🔎 이 주문 관세청 공식검증", key=f"rts_official_{order['id']}", width="stretch"):
            common.open_dialog_deferred(_confirm_official_check, [order])
        if st.button("🛒 퀵스타 배대지 접수", key=f"rts_quickstar_{order['id']}", width="stretch"):
            common.open_dialog_deferred(_confirm_quickstar_submit, [order])
        if order.get("quickstar_order_no"):
            st.caption(f"✅ 퀵스타 접수됨(GR): {order['quickstar_order_no']}")
        # GR신청번호 직접 입력 — 이미 배대지 신청서를 만든 경우, 그 GR을 붙여넣고 바로 이 주문에 저장.
        #   (수령자명 매칭·자동화 크롬/로그인 불필요) 저장하면 '🔄 송장 자동입력'으로 가송장을 채울 수 있음.
        _gr_manual = st.text_input(
            "GR신청번호 직접 입력·수정", value=order.get("quickstar_order_no") or "",
            key=f"rts_grman_{order['id']}", placeholder="예: GR2608287582367",
            help="퀵스타에서 만든 배대지 신청서의 GR번호를 붙여넣고 아래 버튼으로 저장하면 이 주문에 바로 들어갑니다.",
        )
        if st.button("💾 이 GR번호를 주문에 저장", key=f"rts_grsave_{order['id']}", width="stretch"):
            _grc = (_gr_manual or "").strip()
            if not _grc:
                st.warning("GR번호를 입력해주세요.")
            elif not _grc.upper().startswith("GR"):
                st.warning("'GR'로 시작하는 신청번호를 넣어주세요. (예: GR2608287582367)")
            else:
                order_repository.backfill_quickstar_order_no(order["id"], _grc, None)
                st.success(f"GR번호 저장 완료: {_grc} — 이제 위 '🔄 퀵스타 송장 자동입력'으로 가송장을 채울 수 있습니다.")
                st.rerun(scope="app")
        # GR을 잘못 넣었을 때 지우기(이 주문의 GR·가송장 비움).
        if (order.get("quickstar_order_no") or "").strip():
            if st.button("🗑 이 주문의 GR 지우기 (잘못 넣었을 때)", key=f"rts_grclear_{order['id']}", width="stretch"):
                order_repository.clear_quickstar_gr(order["id"])
                _recs = st.session_state.get("rts_edited_records", []) or []
                _moid = str(order["market_order_id"])
                st.session_state["rts_edited_records"] = [r for r in _recs if str(r.get("주문번호")) != _moid]
                st.success("이 주문의 GR을 지웠습니다. (가송장도 함께 비움) 다시 넣으실 수 있습니다.")
                st.rerun(scope="app")
        _render_sms_history(order)
        _render_sms_form(order, reveal)

    # 표 안 '퀵스타 연동' 버튼 클릭 처리. ★다이얼로그는 fragment 안에서 열면 안 떠서,
    #   플래그만 세우고 전체 새로고침 → 메인 본문(fragment 밖)에서 다이얼로그를 엽니다.
    def _rts_on_result(selected, edited_df, qs_clicked_raw):
        if qs_clicked_raw and qs_clicked_raw != st.session_state.get("rts_last_qs_raw"):
            st.session_state["rts_last_qs_raw"] = qs_clicked_raw
            try:
                _qs_idx = int(qs_clicked_raw.split(":")[0])
            except Exception:
                _qs_idx = -1
            if 0 <= _qs_idx < len(filtered_orders):
                common.open_dialog_deferred(_confirm_quickstar_submit, [filtered_orders[_qs_idx]])

    # 표(왼쪽)+상세(오른쪽). 상세 없을 땐 표 전체폭. 체크는 fragment로 처리 → 스크롤 안 튐.
    common.render_grid_detail(
        grid_df, key="rts", orders=filtered_orders, detail_renderer=_rts_detail,
        empty_caption="표 왼쪽 체크박스를 체크하면 그 주문의 상세·퀵스타·문자가 여기에 나타납니다.",
        height=760, editable=_editable, button_col="퀵스타", on_result=_rts_on_result,
    )

    # 표 아래 합계 요약 바(총 건수·결제·수수료·정산) — 신규주문과 동일.
    common.render_order_summary_bar(filtered_orders)

    # 위 액션줄 버튼(관세청 공식검증 / 발송처리)은 표 바깥이라, fragment가 세션에 저장한
    # 선택·편집 결과를 읽어서 처리합니다. (버튼 클릭은 전체 새로고침 → fragment도 함께 최신화됨)
    selected_orders = st.session_state.get("rts_selected_orders", [])
    records = st.session_state.get("rts_edited_records", [])

    if official_selected_clicked:
        if not selected_orders:
            st.warning("검증할 주문을 표 왼쪽 체크박스로 선택해주세요.")
        else:
            _confirm_official_check(selected_orders)

    # 📨 선택 주문 문자 일괄 발송
    if bulk_sms_clicked:
        if not selected_orders:
            st.warning("문자 보낼 주문을 표 왼쪽 체크박스로 선택해주세요.")
        else:
            common.open_dialog_deferred(_confirm_bulk_sms, selected_orders, bulk_sms_template)

    # 퀵스타 배대지 접수(메인 버튼) — 표 안 '퀵스타 연동' 버튼이 불안정해서 여기서 확실히 엽니다.
    if quickstar_selected_clicked:
        if not selected_orders:
            st.warning("접수할 주문을 표 왼쪽 체크박스로 1건 선택해주세요.")
        else:
            # 창이 떠 있는 동안 배경 수집 새로고침이 창을 닫지 않게 억제.
            st.session_state["_suppress_bg_rerun"] = True
            _confirm_quickstar_submit(selected_orders)

    # GR 지우기: 체크한 주문의 잘못된 GR(과 그 GR로 조회한 가송장)을 비웁니다.
    if gr_clear_clicked:
        if not selected_orders:
            st.warning("지울 주문을 표 왼쪽 체크박스로 선택해주세요.")
        else:
            _cleared_moids = set()
            for o in selected_orders:
                if (o.get("quickstar_order_no") or "").strip():
                    order_repository.clear_quickstar_gr(o["id"])
                    _cleared_moids.add(str(o["market_order_id"]))
            if _cleared_moids:
                # 미러에 남은 해당 주문의 GR/송장도 제거(안 그러면 표에서 되살아남).
                _recs = st.session_state.get("rts_edited_records", []) or []
                st.session_state["rts_edited_records"] = [
                    _r for _r in _recs if str(_r.get("주문번호")) not in _cleared_moids
                ]
                st.success(f"🗑 {len(_cleared_moids)}건의 GR을 지웠습니다. (가송장도 함께 비움) 다시 넣으실 수 있습니다.")
                st.rerun(scope="app")
            else:
                st.info("선택한 주문에 지울 GR이 없습니다.")

    # GR번호 채우기: GR이 없는 발송대기 주문을 퀵스타 신청내역과 이름+전화+통관번호로 매칭해 채웁니다.
    #   체크한 주문이 있으면 그것만(요청), 없으면 발송대기 전체.
    if gr_fill_clicked:
        _run_gr_fill_ready_to_ship(selected_orders)

    # 표의 '퀵스타 신청번호' 칸에 붙여넣은 GR을 저장(수동 입력). 보통은 셀 편집 즉시 자동저장되지만,
    #   혹시 안 됐을 때를 위한 백업 버튼.
    if gr_save_clicked:
        _saved = 0
        _bad = 0
        for r in records:
            try:
                idx = int(r.get(aggrid_table.IDX_COL))
            except Exception:
                continue
            if not (0 <= idx < len(filtered_orders)):
                continue
            order = filtered_orders[idx]
            grv = str(r.get("퀵스타 신청번호") or "").strip()
            cur = (order.get("quickstar_order_no") or "").strip()
            if grv in ("", "-") or grv == cur:
                continue
            if not grv.upper().startswith("GR"):
                _bad += 1
                continue
            order_repository.backfill_quickstar_order_no(order["id"], grv, None)
            _saved += 1
        if _saved:
            st.success(f"✅ {_saved}건의 GR번호를 저장했습니다. 이제 송장번호(가송장)가 채워집니다.")
            st.rerun()
        elif _bad:
            st.warning(f"'GR'로 시작하지 않는 값 {_bad}건이 있어 저장 안 함. '퀵스타 신청번호' 칸에 GR번호(예: GR2608287582367)를 넣어주세요.")
        else:
            st.info("저장할 새 GR이 없습니다. 표의 **'퀵스타 신청번호'** 칸을 더블클릭해 GR번호를 입력한 뒤 눌러주세요.")

    # 퀵스타 송장 자동입력: GR신청번호로 배대지 운송장(택배사+송장번호)을 조회해 표 칸을 채웁니다.
    #   체크한 주문이 있으면 그 주문만, 없으면 화면의 발송대기 전체를 대상으로 합니다.
    #   ★실제 쿠팡 등록은 채운 걸 확인한 뒤 '발송처리'로 — 되돌리기 어려운 작업이라 자동실행하지 않습니다.
    if autofill_invoice_clicked:
        scope_orders = selected_orders if selected_orders else filtered_orders
        targets = [
            {"order_id": o["id"], "market_order_id": o["market_order_id"], "gr": o.get("quickstar_order_no")}
            for o in scope_orders
        ]
        _prog = st.progress(0.0, text="퀵스타에서 배대지 운송장번호를 조회하는 중...")

        def _cb(done, total):
            _prog.progress(done / total if total else 1.0, text=f"퀵스타 배대지 조회 {done}/{total}")

        # ★조회 도중 배경 자동수집이 끝나며 화면을 새로고침(scope=app)해서 이 핸들러를
        #   중간에 끊는 것을 막습니다(끊기면 가송장이 저장되기 전이라 송장번호가 '-'로 남음).
        with common.suppress_bg_rerun():
            res = quickstar_invoice_service.fetch_tracking_for_orders(targets, progress=_cb)
        _prog.empty()
        matched = res["matched"]
        if matched:
            # 편집-미러(rts_edited_records)에 주문번호로 병합 → 표의 택배사·송장번호 칸 자동 채움.
            recs = st.session_state.get("rts_edited_records", []) or []
            by_moid = {str(r.get("주문번호")): dict(r) for r in recs if r.get("주문번호")}
            for moid, (code, inv) in matched.items():
                rec = by_moid.get(moid) or {"주문번호": moid}
                rec["송장번호"] = inv
                rec["택배사"] = common.courier_label(code)
                by_moid[moid] = rec
            st.session_state["rts_edited_records"] = list(by_moid.values())
            st.success(
                f"✅ {res['filled']}건 가송장 자동입력 완료 (택배사: CJ대한통운). "
                f"표에서 확인한 뒤 **발송처리**를 누르면 쿠팡에 등록됩니다.  "
                f"· 가송장 아직 없음 {res['not_ready']}건 · GR없음 {res['no_gr']}건 · 오류 {res['errors']}건"
            )
            st.rerun(scope="app")
        else:
            st.warning(
                "자동입력할 가송장이 없습니다.  "
                f"· GR있음 {res['with_gr']}건 중 가송장 아직 없음 {res['not_ready']}건 · GR없음 {res['no_gr']}건 · 오류 {res['errors']}건"
            )
            if res["error_samples"]:
                st.caption("오류 예: " + " / ".join(res["error_samples"]))

    # 발송처리: 표 안 택배사·송장번호로 쿠팡 송장 등록 → 배송중.
    if do_ship:
        rows_to_ship = []
        blocked_rows = []  # 송장은 넣었지만 통관검증 미통과(불가)라 원래는 발송 못 하는 건
        for r in records:
            try:
                idx = int(r.get(aggrid_table.IDX_COL))
            except Exception:
                continue
            if not (0 <= idx < len(filtered_orders)):
                continue
            order = filtered_orders[idx]
            courier_code = common.COURIER_LABEL_TO_CODE.get(r.get("택배사"))
            invoice = str(r.get("송장번호") or "").strip()
            # ★표에 송장이 비어있으면("-"/빈칸) 배대지 가송장(quickstar_invoice, DB)으로 폴백.
            #   → 미러가 풀려 표가 "-"여도, GR이 있어 가송장이 저장돼 있으면 발송처리됨.
            if invoice in ("", "-"):
                invoice = str(order.get("quickstar_invoice") or "").strip()
                if invoice not in ("", "-") and not courier_code:
                    courier_code = order.get("delivery_company_code") or settings.get_default_courier_code()
            # ★그래도 송장이 없으면(가송장도 없음) 발송 대상에서 제외(송장 "-"가 등록되는 사고 방지).
            if invoice in ("", "-"):
                continue
            if not courier_code:
                continue
            if order["work_status"] != models.WORK_STATUS_READY_TO_SHIP:
                continue
            _row = {"order": order, "택배사코드": courier_code, "송장번호": invoice}
            # 통관검증 미통과(불가)는 따로 모아둠 → 아래 '그래도 강제 발송처리' 버튼으로 보낼 수 있게.
            if (order["shipping"] or {}).get("validation_status") not in models.SHIPPABLE_VALIDATION_STATUSES:
                blocked_rows.append(_row)
            else:
                rows_to_ship.append(_row)
        if rows_to_ship:
            st.session_state.pop("rts_blocked_ship", None)
            _confirm_bulk_register_invoice(rows_to_ship)
        elif blocked_rows:
            # 불가 건만 있음 → 경고 + 강제 발송 대기목록 저장(아래 버튼에서 처리).
            st.session_state["rts_blocked_ship"] = blocked_rows
        else:
            st.warning("발송할 주문의 **택배사와 송장번호**를 표에 직접 입력해주세요. (송장번호가 '-'이면 아직 입력 안 된 상태입니다)")

    # 통관검증 '불가'인데 송장을 넣은 건 → '그래도 강제 발송처리' 버튼(발송처리 눌렀을 때 대기목록에 담김).
    _blocked = st.session_state.get("rts_blocked_ship")
    if _blocked:
        st.warning(
            f"통관검증 **불가**(공식검증 불일치·미검증) **{len(_blocked)}건**은 원래 발송 불가입니다. "
            "먼저 '관세청 공식검증'을 통과시키는 게 안전하지만, 그냥 보내려면 아래 버튼을 누르세요."
        )
        _fc1, _fc2 = st.columns([2, 1])
        with _fc1:
            if st.button(f"⚠️ 그래도 이 {len(_blocked)}건 강제 발송처리", type="primary", width="stretch", key="rts_force_ship_now"):
                _rows = st.session_state.pop("rts_blocked_ship", None) or []
                if _rows:
                    _confirm_bulk_register_invoice(_rows)
        with _fc2:
            if st.button("취소", width="stretch", key="rts_force_ship_cancel"):
                st.session_state.pop("rts_blocked_ship", None)
                st.rerun()
