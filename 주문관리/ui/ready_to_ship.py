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
from repositories import order_repository
from services import customs_service, privacy, sms_templates, sync_service
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
                            if sms.get("sent"):
                                sms_sent += 1
                            elif sms.get("reason") == "send_failed":
                                sms_failed += 1
                    else:
                        error_count += 1
            summary = f"일치 {match_count}건 / 불일치 {mismatch_count}건 / 오류 {error_count}건"
            if auto_error_sms:
                summary += f" · 자동 오류문구 발송 {sms_sent}건"
                if sms_failed:
                    summary += f" (발송실패 {sms_failed}건)"
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
                    st.session_state["ready_to_ship_sms_result"] = {
                        "succeeded": False,
                        "message": str(error),
                    }
                else:
                    st.session_state["ready_to_ship_sms_result"] = {"succeeded": True, "message": None}
            st.rerun()
    with col_no:
        if st.button("취소", width="stretch"):
            st.rerun()


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
            _confirm_send_sms(order, phone, message)


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

    # ------------------------------------------------------
    # 상단: 결제일시 기간 + 수집하기
    # 쿠팡에서 이미 "상품준비중"(INSTRUCT) 상태인 주문을 직접 가져옵니다.
    # 신규주문에서 '발송대기로 이동'을 거치지 않고 쿠팡 Wing에서 직접 처리한
    # 주문도 여기서 다시 수집하면 나타납니다.
    # ------------------------------------------------------
    period_from, period_to = common.render_period_picker("rts", "결제일시(시작)", "결제일시(종료)")
    collect_clicked = st.button("수집하기", type="primary", width="stretch", key="rts_collect")

    if collect_clicked:
        if period_from > period_to:
            st.error("시작일이 종료일보다 늦을 수 없습니다.")
        else:
            # 발송대기만이 아니라 전 단계를 함께 최신화합니다. 그래야 이미 배송지시로
            # 넘어간 주문이 발송대기에 그대로 남지 않고 배송중으로 이동합니다.
            result = common.collect_all_stages_with_progress(period_from, period_to)
            if result["status"] == "fail":
                st.error(f"주문 수집 실패: {result['error_message']}")
            else:
                closed = result.get("closed_count") or 0
                message = (
                    f"수집 완료 - 전체 단계 최신화 "
                    f"(신규 {result['new_count']}건, 갱신 {result['updated_count']}건"
                    + (f", 취소·반품 정리 {closed}건" if closed else "")
                    + ")"
                )
                if result["error_message"]:
                    st.warning(message + f"\n일부 오류: {result['error_message']}")
                else:
                    st.success(message)
                st.rerun()

    common.render_live_count_banner(models.WORK_STATUS_READY_TO_SHIP)

    st.divider()

    orders = order_repository.list_orders_by_work_status(models.WORK_STATUS_READY_TO_SHIP)

    if not orders:
        st.info("발송대기 주문이 없습니다. 위 '수집하기'를 누르거나, 신규주문 화면에서 '선택 주문 발송대기로 이동'을 사용해주세요.")
        return

    col_search, col_problem_only = st.columns([3, 1])
    with col_search:
        keyword = st.text_input("발송대기 목록 검색", placeholder="주문번호, 수령자 등으로 검색")
    with col_problem_only:
        st.write("")
        problem_only = st.checkbox("불일치·오류만 보기")

    # 전화번호·개인통관고유부호는 실제 발송 업무에 매번 필요해서 항상 그대로 보여줍니다.
    reveal = True
    all_rows = [common.build_full_row(order, idx + 1, reveal=reveal) for idx, order in enumerate(orders)]

    col_official_selected, col_excel = st.columns([1, 1])
    with col_official_selected:
        official_selected_clicked = st.button(
            "선택 주문 관세청 공식검증",
            width="stretch",
            help="표 왼쪽 체크박스로 선택한 주문들을 관세청에 검증합니다. (머리글 체크박스로 전체선택)",
        )
    with col_excel:
        # 엑셀 파일은 사용자가 실제 발송 작업(운송장 출력 등)에 쓰는 파일이라
        # 화면과 달리 민감정보를 가리지 않고 그대로 담습니다.
        excel_rows = [common.build_full_row(order, idx + 1, reveal=True) for idx, order in enumerate(orders)]
        excel_bytes = common.build_excel_bytes(excel_rows)
        st.download_button(
            "엑셀파일생성",
            data=excel_bytes,
            file_name="발송대기.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
            key="rts_excel_download",
        )

    filtered_pairs = []
    for order, row in zip(orders, all_rows):
        if not common.matches_search(row, keyword):
            continue
        if problem_only and row["통관검증 상태"] in (models.VALIDATION_STATUS_OFFICIAL_PASSED, "미검증"):
            continue
        filtered_pairs.append((order, row))

    if not filtered_pairs:
        st.info("조건에 맞는 주문이 없습니다.")
        return

    filtered_orders = [pair[0] for pair in filtered_pairs]
    filtered_rows = [pair[1] for pair in filtered_pairs]

    # AG-Grid 표: 왼쪽 체크박스(+머리글 전체선택), 마우스로 컬럼/행(순번) 드래그 순서변경(자동저장).
    import pandas as pd

    from ui import aggrid_table

    grid_df = pd.DataFrame(filtered_rows)
    _front = ["No", "택배사", "송장번호", "통관검증 상태", "발송 가능 여부", "수령자", "주문번호", "상품명", "퀵스타"]
    _cols = [c for c in _front if c in grid_df.columns] + [c for c in grid_df.columns if c not in _front]
    grid_df = grid_df[_cols]

    # 표 안에서 택배사(드롭다운)·송장번호(직접입력) 편집 가능하게 설정.
    _courier_labels = [common.COURIER_UNSET_LABEL] + list(common.COURIER_LABEL_TO_CODE.keys())
    _editable = {
        "택배사": {"cellEditor": "agSelectCellEditor", "cellEditorParams": {"values": _courier_labels}},
        "송장번호": {"cellEditor": "agTextCellEditor"},
    }
    selected_orders, edited_df = aggrid_table.render_orders_grid(
        grid_df, key="rts", orders=filtered_orders, editable=_editable, height=520
    )

    if official_selected_clicked:
        if not selected_orders:
            st.warning("검증할 주문을 표 왼쪽 체크박스로 선택해주세요.")
        else:
            _confirm_official_check(selected_orders)

    # 발송처리: 표 안에 입력한 택배사·송장번호로 쿠팡 송장 등록 → 배송중.
    if st.button("🚚 발송처리 (표에 입력한 택배사·송장 등록 → 배송중)", type="primary", width="stretch", key="rts_bulk_ship"):
        try:
            records = edited_df.to_dict("records")
        except Exception:
            records = []
        rows_to_ship = []
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
            if not (courier_code and invoice):
                continue
            if order["work_status"] != models.WORK_STATUS_READY_TO_SHIP:
                continue
            if (order["shipping"] or {}).get("validation_status") not in models.SHIPPABLE_VALIDATION_STATUSES:
                continue
            rows_to_ship.append({"order": order, "택배사코드": courier_code, "송장번호": invoice})
        if not rows_to_ship:
            st.warning("표에 택배사·송장번호를 입력하고, 통관검증을 통과한 주문이 있어야 합니다.")
        else:
            _confirm_bulk_register_invoice(rows_to_ship)

    # 상세: 체크한 주문(여럿이면 마지막)의 상세 + 퀵스타 접수 + 문자.
    st.divider()
    if selected_orders:
        order = selected_orders[-1]
        shipping = order.get("shipping") or {}
        st.subheader(f"상세내역 — {shipping.get('receiver_name') or order['market_order_id']}")
        common.render_full_detail(order, reveal)
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("🔎 이 주문 관세청 공식검증", key=f"rts_official_{order['id']}", width="stretch"):
                _confirm_official_check([order])
        with col_b:
            if st.button("🛒 퀵스타 배대지 접수", key=f"rts_quickstar_{order['id']}", width="stretch"):
                _confirm_quickstar_submit([order])
        if order.get("quickstar_order_no"):
            st.caption(f"퀵스타 접수됨: {order['quickstar_order_no']}")
        _render_sms_form(order, reveal)
    else:
        st.caption("표 왼쪽 체크박스를 체크하면 그 주문의 상세·퀵스타·문자가 여기에 나타납니다.")
