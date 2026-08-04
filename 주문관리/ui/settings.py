# ==========================================================
# 설정 화면 (ui/settings.py)
# ----------------------------------------------------------
# .env의 기본 쿠팡 계정은 프로그램이 처음 켜질 때 자동으로 "마켓 연동 관리"
# 목록에도 등록됩니다 (market_repository.ensure_default_coupang_account).
# 그래서 상점을 여러 개 쓰시는 경우, 여기서 추가로 등록하시면 됩니다.
#
# 마켓마다 연동 방식이 다를 수 있어서 두 가지를 지원합니다.
#   - 공식 API : 쿠팡처럼 Access Key/Secret Key 같은 정식 API 키가 있는 경우
#   - 로그인(자동화) : 공식 API가 없어서 로그인 아이디/비밀번호로 판매자
#                      화면에 자동 로그인해서 데이터를 가져와야 하는 경우.
#                      (계정 정지 위험이 있고, 실제 자동화 코드는 마켓별로
#                       별도로 만들어야 합니다. 여기서는 등록만 합니다.)
#
# "플랫폼"은 실제로 어떤 연동 코드를 쓸지 구분하는 값입니다. 지금은 "쿠팡"만
# 실제로 동작합니다 - 다른 플랫폼은 등록은 되지만 수집 기능은 아직 없습니다.
#
# API 키/비밀번호는 기본적으로 가려서 보여주고, "API 키 전체 보기" 체크박스를
# 눌러야 실제 값이 보입니다 (전화번호/개인통관고유부호와 같은 방식).
# ==========================================================

import pandas as pd
import streamlit as st

import config
import models
from repositories import market_repository, settings_repository

DEFAULT_COURIER_SETTING_KEY = "default_courier_code"
DEFAULT_COURIER_FALLBACK = "CJGLS"  # CJ대한통운

# 콜센터문의(callCenterInquiries) 수집 사용 여부.
# 2026-07 기준 이 쿠팡 API는 어떤 조회 조건으로 호출해도, 상점 3개 모두에서
# "internal error (코드 500)"만 돌려줍니다. 켜두면 새로고침할 때마다 고칠 수
# 없는 빨간 오류가 뜨기 때문에 기본값을 "꺼짐"으로 둡니다.
# 쿠팡 쪽에서 해결되면 설정 화면에서 다시 켜면 됩니다.
CALL_CENTER_SYNC_SETTING_KEY = "collect_call_center_inquiries"
CALL_CENTER_SYNC_DEFAULT = "off"


def get_default_courier_code() -> str:
    """송장 등록 화면에서 기본으로 선택해둘 택배사 코드를 돌려줍니다."""
    saved = settings_repository.get_setting(DEFAULT_COURIER_SETTING_KEY, DEFAULT_COURIER_FALLBACK)
    return saved if saved in models.COURIER_CODES else DEFAULT_COURIER_FALLBACK


def is_call_center_sync_enabled() -> bool:
    """콜센터문의를 수집할지 여부를 돌려줍니다. (기본: 수집 안 함)"""
    saved = settings_repository.get_setting(CALL_CENTER_SYNC_SETTING_KEY, CALL_CENTER_SYNC_DEFAULT)
    return saved == "on"


def _mask_secret(value: str) -> str:
    if not value:
        return "-"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _secret_display(value: str, reveal: bool) -> str:
    if not value:
        return "미설정"
    return value if reveal else _mask_secret(value)


PLATFORM_LABELS = {
    market_repository.PLATFORM_COUPANG: "쿠팡",
    market_repository.PLATFORM_OTHER: "기타(연동 코드 준비 중)",
}


def _render_market_detail(account: dict, reveal: bool) -> None:
    """
    표에서 상점/마켓 행을 하나 선택했을 때 보여줄 상세정보입니다.
    Access Key/Secret Key/로그인 비밀번호는 기본적으로 가려서 보여주고,
    "API 키 전체 보기" 체크박스를 눌러야 실제 값이 보입니다.
    """
    rows = [
        ("상점/마켓명", account["market_name"]),
        ("플랫폼", PLATFORM_LABELS.get(account["platform"], account["platform"])),
        ("연동방식", "공식 API" if account["connection_type"] == "api" else "로그인(자동화)"),
        ("등록일", account["created_at"]),
        ("사용 여부", "사용 중" if account["is_active"] else "사용 안 함"),
        ("WING 아이디", account.get("wing_id") or "-"),
    ]

    if account["connection_type"] == "api":
        rows += [
            ("업체코드 / Vendor ID", account["api_vendor_id"] or "-"),
            ("Access Key", _secret_display(account["api_access_key"], reveal)),
            ("Secret Key", _secret_display(account["api_secret_key"], reveal)),
        ]
    else:
        rows += [
            ("로그인 아이디", account["login_id"] or "-"),
            ("로그인 비밀번호", _secret_display(account["login_password"], reveal)),
        ]

    st.markdown("**상점/마켓 상세정보**")
    detail_df = pd.DataFrame(rows, columns=["항목", "값"]).set_index("항목")
    st.table(detail_df)

    # 상품문의에 답변을 등록하려면 쿠팡이 답변자 WING 아이디(replyBy)를 필수로
    # 요구합니다. 비밀번호가 아니라 아이디만 필요합니다.
    if account["platform"] == market_repository.PLATFORM_COUPANG:
        wing_id = st.text_input(
            "WING 아이디",
            value=account.get("wing_id") or "",
            key=f"wing_id_{account['id']}",
            help=(
                "쿠팡 판매자센터(WING) 로그인 아이디입니다. 상품문의에 답변을 등록할 때 "
                "쿠팡이 '누가 답변했는지'를 필수로 요구해서 필요합니다. 비밀번호는 필요 없습니다.\n\n"
                "정확한 아이디를 모르시면 비워두셔도 됩니다. 그 경우 위 업체코드로 답변이 "
                "등록됩니다(쿠팡이 업체코드도 답변자 값으로 받아줍니다)."
            ),
        )
        if st.button("WING 아이디 저장", key=f"save_wing_id_{account['id']}"):
            market_repository.set_wing_id(account["id"], wing_id)
            st.success(f"'{account['market_name']}' 상점의 WING 아이디를 저장했습니다.")
            st.rerun()


def _render_market_management() -> None:
    st.subheader("마켓 연동 관리")
    st.caption(
        "여러 상점(쿠팡 계정)이나 다른 마켓을 등록할 수 있습니다. "
        ".env의 기본 쿠팡 계정도 자동으로 이 목록에 포함되어 있습니다."
    )

    accounts = market_repository.list_market_accounts()

    if accounts:
        rows = [
            {
                "상점/마켓명": acc["market_name"],
                "플랫폼": PLATFORM_LABELS.get(acc["platform"], acc["platform"]),
                "연동방식": "공식 API" if acc["connection_type"] == "api" else "로그인(자동화)",
                "등록일": acc["created_at"],
            }
            for acc in accounts
        ]
        st.caption("행을 클릭하면 아래에 해당 상점/마켓의 연동 정보가 나타납니다.")
        event = st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key="market_accounts_table",
        )

        selected_rows = event.selection.rows
        if selected_rows:
            reveal = st.checkbox("API 키 전체 보기", key="market_detail_reveal")
            _render_market_detail(accounts[selected_rows[0]], reveal)

        delete_target = st.selectbox(
            "삭제할 상점/마켓",
            options=[None] + [acc["id"] for acc in accounts],
            format_func=lambda x: "선택 안 함" if x is None else next(
                acc["market_name"] for acc in accounts if acc["id"] == x
            ),
        )
        if delete_target is not None and st.button("선택한 상점/마켓 삭제"):
            market_repository.deactivate_market_account(delete_target)
            st.success("삭제했습니다.")
            st.rerun()
    else:
        st.caption("등록된 상점/마켓이 없습니다.")

    with st.form("add_market_form", clear_on_submit=True):
        st.markdown("**새 상점/마켓 추가**")
        market_name = st.text_input("상점/마켓명", placeholder="예: 굿디얼2호점")
        platform_label = st.selectbox("플랫폼", options=list(PLATFORM_LABELS.values()))
        platform = market_repository.PLATFORM_COUPANG if platform_label == PLATFORM_LABELS[market_repository.PLATFORM_COUPANG] else market_repository.PLATFORM_OTHER

        connection_type_label = st.radio(
            "연동 방식", ["공식 API", "로그인(자동화)"], horizontal=True
        )

        vendor_id = access_key = secret_key = None
        login_id = login_password = None

        if connection_type_label == "공식 API":
            vendor_id = st.text_input("업체코드 / Vendor ID")
            access_key = st.text_input("Access Key")
            secret_key = st.text_input("Secret Key", type="password")
            if platform != market_repository.PLATFORM_COUPANG:
                st.info("쿠팡이 아닌 플랫폼은 공식 API 방식이라도 아직 실제 연동 코드가 없습니다. 등록만 됩니다.")
        else:
            st.warning(
                "로그인 자동화 방식은 계정 정지·약관 위반 위험이 있고, "
                "실제 자동화 코드는 아직 만들지 않았습니다. 지금은 정보만 등록해두고, "
                "해당 마켓의 로그인 화면·주문목록 화면 구조를 확인한 뒤 연동 코드를 따로 붙일 예정입니다."
            )
            login_id = st.text_input("로그인 아이디")
            login_password = st.text_input("로그인 비밀번호", type="password")

        submitted = st.form_submit_button("추가")
        if submitted:
            if not market_name.strip():
                st.error("상점/마켓명을 입력해주세요.")
            else:
                try:
                    market_repository.create_market_account(
                        market_name=market_name.strip(),
                        connection_type="api" if connection_type_label == "공식 API" else "login",
                        platform=platform,
                        api_vendor_id=vendor_id or None,
                        api_access_key=access_key or None,
                        api_secret_key=secret_key or None,
                        login_id=login_id or None,
                        login_password=login_password or None,
                    )
                except market_repository.DuplicateMarketNameError as error:
                    st.error(str(error))
                else:
                    st.success(f"'{market_name}'이(가) 추가되었습니다.")
                    st.rerun()


def _render_shipping_settings() -> None:
    st.subheader("발송 설정")

    current_default = get_default_courier_code()
    courier_options = list(models.COURIER_CODES.keys())

    selected = st.selectbox(
        "기본 택배사",
        options=courier_options,
        index=courier_options.index(current_default),
        format_func=lambda code: f"{models.COURIER_CODES[code]} ({code})",
        key="settings_default_courier",
        help="발송대기 화면에서 송장 등록 폼을 열면 이 택배사가 기본으로 선택되어 있습니다.",
    )

    if st.button("기본 택배사로 저장", key="settings_save_default_courier"):
        settings_repository.set_setting(DEFAULT_COURIER_SETTING_KEY, selected)
        st.success(f"기본 택배사를 '{models.COURIER_CODES[selected]}'(으)로 저장했습니다.")
        st.rerun()


def _render_collection_settings() -> None:
    st.subheader("수집 설정")

    enabled = st.checkbox(
        "콜센터문의도 함께 수집하기",
        value=is_call_center_sync_enabled(),
        key="settings_call_center_sync",
        help=(
            "쿠팡 콜센터문의 API는 현재 어떤 조회 조건으로 호출해도 "
            "'internal error (코드 500)'만 응답합니다. 상점 3개 모두 같은 증상이라 "
            "쿠팡 쪽 문제로 보입니다. 켜두면 새로고침할 때마다 오류 문구가 뜨기 때문에 "
            "기본값은 꺼짐입니다."
        ),
    )

    if not enabled:
        st.caption(
            "지금은 콜센터문의를 건너뜁니다. 상품문의는 정상적으로 수집되며, "
            "CS문의 목록에도 그대로 나옵니다."
        )

    if st.button("수집 설정 저장", key="settings_save_collection"):
        settings_repository.set_setting(
            CALL_CENTER_SYNC_SETTING_KEY, "on" if enabled else "off"
        )
        st.success("수집 설정을 저장했습니다." if enabled else "콜센터문의 수집을 껐습니다.")
        st.rerun()


def render_general() -> None:
    """설정 · 일반 (실행 모드 / 기본 계정 키 / 발송·수집 설정 / DB)."""
    st.header("설정 · 일반")

    st.subheader("실행 모드")
    st.write(f"현재 모드: **{config.APP_MODE}** ({'가상 데이터' if config.is_mock_mode() else '실제 쿠팡 API'})")

    env_reveal = st.checkbox("API 키 전체 보기", key="env_detail_reveal")

    st.subheader("쿠팡 기본 계정 (.env)")
    st.write(f"판매자(Vendor) ID: {config.COUPANG_VENDOR_ID or '미설정'}")
    st.write(f"Access Key: {_secret_display(config.COUPANG_ACCESS_KEY, env_reveal)}")
    st.write(f"Secret Key: {_secret_display(config.COUPANG_SECRET_KEY, env_reveal)}")

    st.subheader("관세청 유니패스 연동 정보")
    st.write(f"인증키: {_secret_display(config.UNIPASS_API_KEY, env_reveal)}")

    st.caption("위 값들을 바꾸려면 .env 파일을 수정한 뒤 프로그램을 다시 실행하세요.")

    st.divider()
    _render_shipping_settings()

    st.divider()
    _render_collection_settings()

    st.divider()
    st.subheader("데이터베이스")
    st.write(f"파일 경로: {config.DB_PATH}")


def render_shop_accounts() -> None:
    """설정 · 쇼핑몰 계정 연동 (마켓 계정 등록/관리)."""
    st.header("설정 · 쇼핑몰 계정 연동")
    _render_market_management()


def render() -> None:
    """예전 호환용 - '설정'을 통째로 부르면 일반 설정을 보여줍니다."""
    render_general()
