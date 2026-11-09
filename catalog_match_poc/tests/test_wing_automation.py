import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from poc.wing_automation import (
    PURCHASE_AGENT_CERTIFICATION_TEXT,
    _align_option_image_paths,
    _cached_detail_main_candidate,
    _catalog_other_seller_count,
    _catalog_option_name,
    _click_reliably,
    _detail_upload_assets,
    _detail_urls_from_wing_state,
    _dismiss_visible_wing_confirmation,
    _ensure_purchase_agent_certification,
    _infer_required_option_values,
    _is_select_all_catalog_option,
    _make_square_product_image,
    _matched_option_winner_price,
    _new_wing_identifier,
    _normalize_option_identity,
    _normalize_coupang_image_url,
    _open_new_registration_after_success,
    _option_price_editor_state,
    _post_save_choice_visible,
    _parse_money_input,
    _parse_option_row_winner_price,
    _parse_option_total,
    _preferred_wing_page,
    _required_option_row_name,
    _original_coupang_image_url,
    _return_fee_adjustment_target,
    _select_detail_upload_files,
    _set_dropzone_hidden_input_files,
    _shipping_layout_version,
    _source_detail_url_candidates,
    _source_page_block_reason,
    _standard_size_values,
    _submission_diagnostic_messages,
    _wing_page_target,
    _wing_password_guide_return_url,
    _wake_option_price_editor,
    choose_return_fee,
    parse_winner_price,
)


class _FakeLocator:
    def __init__(self, page, *, count: int = 1) -> None:
        self.page = page
        self._count = count

    @property
    def first(self):
        return self

    def count(self) -> int:
        return self._count

    def nth(self, _index: int):
        return self

    def is_visible(self) -> bool:
        return True

    def click(self) -> None:
        self.page.clicked_new_registration = True

    def wait_for(self, **_kwargs) -> None:
        self.page.waited_for_registration_form = True


class _FakePage:
    def __init__(self, *, has_new_registration_link: bool = True) -> None:
        self.has_new_registration_link = has_new_registration_link
        self.clicked_new_registration = False
        self.waited_for_registration_form = False
        self.goto_url = ""

    def get_by_text(self, text: str, *, exact: bool):
        assert text == "새로운 상품등록"
        assert exact
        return _FakeLocator(self, count=1 if self.has_new_registration_link else 0)

    def locator(self, _selector: str):
        return _FakeLocator(self)

    def goto(self, url: str, **_kwargs) -> None:
        self.goto_url = url


class _ChoicePage:
    def __init__(self, visible_labels) -> None:
        self.visible_labels = set(visible_labels)

    def get_by_text(self, text: str, *, exact: bool):
        assert exact
        return _FakeLocator(
            self,
            count=1 if text in self.visible_labels else 0,
        )


class _StateLocator:
    def __init__(self, items) -> None:
        self.items = items

    def count(self) -> int:
        return len(self.items)

    def nth(self, index: int):
        return self.items[index]


class _StateItem:
    def __init__(self, *, visible=True, disabled=False, text="", rows=None) -> None:
        self.visible = visible
        self.disabled = disabled
        self.text = text
        self.rows = rows or []

    def is_visible(self) -> bool:
        return self.visible

    def is_disabled(self) -> bool:
        return self.disabled

    def inner_text(self) -> str:
        return self.text

    def locator(self, selector: str):
        if selector == "tr":
            return _StateLocator(self.rows)
        if selector == 'input[type="text"]':
            return _StateLocator([_StateItem()] * (3 if self.visible else 0))
        return _StateLocator([])


class _OptionStatePage:
    def __init__(
        self,
        *,
        modal=False,
        table_rows=0,
        blank_inputs=0,
        bulk=False,
        option_total=0,
        completion=False,
    ):
        self.modal = modal
        self.table_rows = table_rows
        self.blank_inputs = blank_inputs
        self.bulk = bulk
        self.option_total = option_total
        self.completion = completion

    def locator(self, selector: str):
        if "상품명, 브랜드명, 상품 URL" in selector:
            return _StateLocator([_StateItem(visible=self.modal)])
        if selector == "table":
            rows = [_StateItem() for _ in range(self.table_rows)]
            return _StateLocator(
                [_StateItem(text="아이템 위너 가격", rows=rows)] if rows else []
            )
        if selector == 'input[type="text"][placeholder=""]':
            return _StateLocator([_StateItem()] * self.blank_inputs)
        if selector == "body":
            return _StateItem(text=f"옵션 총 {self.option_total}")
        return _StateLocator([])

    def get_by_text(self, text: str, *, exact: bool):
        assert exact
        visible = self.completion if text == "옵션 완성하기" else self.bulk
        return _StateLocator([_StateItem(visible=visible)])


class WingAutomationTests(unittest.TestCase):
    def test_builds_clean_source_detail_retry_urls(self) -> None:
        candidates = _source_detail_url_candidates(
            "https://www.coupang.com/vp/products/9414841296?"
            "itemId=27975753559&searchId=temporary&vendorItemId=94933596192"
        )
        self.assertEqual(
            candidates,
            [
                "https://www.coupang.com/vp/products/9414841296?"
                "itemId=27975753559&searchId=temporary&vendorItemId=94933596192",
                "https://www.coupang.com/vp/products/9414841296?"
                "itemId=27975753559&vendorItemId=94933596192",
                "https://www.coupang.com/vp/products/9414841296",
            ],
        )

    def test_detects_source_page_access_block(self) -> None:
        self.assertEqual(
            _source_page_block_reason("Access Denied. Please try later."),
            "접근 차단 화면",
        )
        self.assertEqual(
            _source_page_block_reason("비정상적인 접근입니다. 로봇이 아닙니다."),
            "비정상 접근 차단 화면",
        )
        self.assertEqual(
            _source_page_block_reason(
                "요청하신 페이지의 사용권한이 없습니다. 사용권한이 제한된 페이지입니다."
            ),
            "상품페이지 사용권한 제한 화면",
        )
        self.assertEqual(_source_page_block_reason("정상 상품 상세 설명"), "")

    def test_wakes_lazy_virtual_option_editor(self) -> None:
        class Page:
            def __init__(self) -> None:
                self.evaluated = False
                self.waited = 0

            def evaluate(self, script: str):
                self.evaluated = "scrollIntoView" in script
                return True

            def wait_for_timeout(self, milliseconds: int) -> None:
                self.waited = milliseconds

        page = Page()
        _wake_option_price_editor(page)
        self.assertTrue(page.evaluated)
        self.assertEqual(page.waited, 700)

    def test_shipping_layout_uses_new_structure_after_method_is_selected(self) -> None:
        class Page:
            def locator(self, _selector: str):
                return _StateLocator([])

            def get_by_text(self, text, *, exact=None):
                if text == "기본 배송비" and exact:
                    return _StateLocator([_StateItem(visible=True)])
                return _StateLocator([])

        self.assertEqual(_shipping_layout_version(Page()), "new")

    def test_shipping_layout_keeps_legacy_dropdown_for_old_business(self) -> None:
        class Page:
            def locator(self, selector: str):
                if "deliveryChargeTypeSelectionWrapper" in selector:
                    return _StateLocator([_StateItem(visible=True)])
                return _StateLocator([])

            def get_by_text(self, _text, *, exact=None):
                return _StateLocator([])

        self.assertEqual(_shipping_layout_version(Page()), "legacy")

    def test_parses_formatted_return_fee_input(self) -> None:
        self.assertEqual(_parse_money_input("150,000"), 150_000)
        self.assertEqual(_parse_money_input("20,000원"), 20_000)
        self.assertIsNone(_parse_money_input(""))

    def test_submission_diagnostics_prioritize_return_fee_and_remove_ui_noise(self) -> None:
        messages = _submission_diagnostic_messages(
            "\n".join(
                (
                    "이미지 URL 입력",
                    "기본 입력",
                    "반품배송비(편도) *",
                    "상품정보제공고시 분류는 필수항목입니다.",
                    "구매 옵션별로 입력",
                )
            ),
            ["[반품배송비는 0원 ~ 20,000원 까지 가능합니다.]"],
        )
        self.assertEqual(
            messages,
            [
                "[반품배송비는 0원 ~ 20,000원 까지 가능합니다.]",
                "상품정보제공고시 분류는 필수항목입니다.",
            ],
        )

    def test_purchase_agent_certification_is_rechecked(self) -> None:
        class Option:
            checked = False

            def check(self, *, force: bool) -> None:
                assert force
                self.checked = True

            def is_checked(self) -> bool:
                return self.checked

        class Locator:
            def __init__(self, option) -> None:
                self.option = option

            def count(self) -> int:
                return 1

            def nth(self, _index: int):
                return self.option

        class Page:
            def __init__(self) -> None:
                self.option = Option()

            def locator(self, selector: str):
                assert selector == "#certificationType_PURCHASED_WITHOUT_KC_MARK_4"
                return Locator(self.option)

            def wait_for_timeout(self, _milliseconds: int) -> None:
                pass

        page = Page()
        _ensure_purchase_agent_certification(page)
        self.assertTrue(page.option.checked)

    def test_purchase_agent_text_matches_wing_wording(self) -> None:
        self.assertIsNotNone(
            PURCHASE_AGENT_CERTIFICATION_TEXT.search(
                "구매대행으로 구매가능한 품목"
            )
        )

    def test_purchase_agent_text_label_selects_nested_radio(self) -> None:
        class EmptyLocator:
            def count(self) -> int:
                return 0

        class Option:
            checked = False

            def check(self, *, force: bool) -> None:
                assert force
                self.checked = True

            def is_checked(self) -> bool:
                return self.checked

        class OptionLocator:
            def __init__(self, option) -> None:
                self.option = option

            @property
            def last(self):
                return self.option

            def count(self) -> int:
                return 1

        class Label:
            def __init__(self, option) -> None:
                self.option = option

            def get_attribute(self, _name: str):
                return None

            def locator(self, _selector: str):
                return OptionLocator(self.option)

        class Labels:
            def __init__(self, label) -> None:
                self.label = label

            def filter(self, *, has_text):
                assert has_text.search("구매대행으로 구매가능한 품목")
                return self

            def count(self) -> int:
                return 1

            def nth(self, _index: int):
                return self.label

        class Page:
            def __init__(self) -> None:
                self.option = Option()
                self.labels = Labels(Label(self.option))

            def locator(self, selector: str):
                return self.labels if selector == "label" else EmptyLocator()

            def wait_for_timeout(self, _milliseconds: int) -> None:
                pass

        page = Page()
        self.assertTrue(_ensure_purchase_agent_certification(page))
        self.assertTrue(page.option.checked)

    def test_missing_certification_choice_does_not_block_category(self) -> None:
        class EmptyLocator:
            def count(self) -> int:
                return 0

            def nth(self, _index: int):
                raise AssertionError("빈 선택지를 사용하면 안 됩니다.")

            def filter(self, **_kwargs):
                return self

        class Page:
            def locator(self, _selector: str):
                return EmptyLocator()

            def get_by_text(self, _pattern):
                return EmptyLocator()

        self.assertFalse(_ensure_purchase_agent_certification(Page()))

    def test_dropzone_upload_does_not_reuse_removed_hidden_input(self) -> None:
        class UploadScope:
            def evaluate(self, _script: str, _token: str) -> bool:
                return True

        class DirectInput:
            removed = False

            def set_input_files(self, path: str) -> None:
                assert path == "main.jpg"
                self.removed = True

            def evaluate(self, _script: str) -> None:
                raise AssertionError("업로드 뒤 사라진 입력칸을 다시 사용하면 안 됩니다.")

        class Page:
            def __init__(self) -> None:
                self.input = DirectInput()

            def locator(self, selector: str):
                self.assert_token_selector = selector
                return self.input

        page = Page()
        self.assertTrue(
            _set_dropzone_hidden_input_files(page, UploadScope(), "main.jpg")
        )
        self.assertTrue(page.input.removed)

    def test_detail_retry_reacquires_replaced_input_for_each_file(self) -> None:
        class Collection:
            def __init__(self, items) -> None:
                self.items = items

            @property
            def last(self):
                return self.items[-1]

            def count(self) -> int:
                return len(self.items)

        class Input:
            def __init__(self, dialog, generation: int) -> None:
                self.dialog = dialog
                self.generation = generation

            def get_attribute(self, name: str):
                assert name == "multiple"
                return None

            def set_input_files(self, value) -> None:
                self.dialog.calls.append((self.generation, value))
                self.dialog.selected.append(Path(value).name)
                self.dialog.inputs.append(Input(self.dialog, self.generation + 1))

        class Dialog:
            def __init__(self) -> None:
                self.calls = []
                self.selected = []
                self.inputs = [Input(self, 0)]

            def locator(self, selector: str):
                if selector == 'input[type="file"]':
                    return Collection(self.inputs)
                if selector.startswith('[filename="'):
                    filename = selector.split('"')[1]
                    return Collection([object()] if filename in self.selected else [])
                if selector.startswith("[filename],"):
                    return Collection([object()] * len(self.selected))
                raise AssertionError(selector)

        class Page:
            def wait_for_timeout(self, _milliseconds: int) -> None:
                pass

        dialog = Dialog()
        latest = _select_detail_upload_files(
            Page(),
            dialog,
            dialog.inputs[-1],
            ["one.jpg", "two.jpg", "three.jpg"],
            force_individual=True,
        )

        self.assertEqual(
            dialog.calls,
            [(0, "one.jpg"), (1, "two.jpg"), (2, "three.jpg")],
        )
        self.assertEqual(latest.generation, 3)

    def test_late_wing_confirmation_is_closed_before_next_click(self) -> None:
        class Collection:
            def __init__(self, items) -> None:
                self.items = items

            @property
            def last(self):
                return self.items[-1]

            def count(self) -> int:
                return len(self.items)

            def nth(self, index: int):
                return self.items[index]

            def filter(self, **_kwargs):
                return self

        class Button:
            def __init__(self, page) -> None:
                self.page = page

            def click(self, *, force: bool) -> None:
                assert force
                self.page.overlay_visible = False
                self.page.alert_visible = False

        class Alert:
            def __init__(self, page) -> None:
                self.page = page

            def inner_text(self) -> str:
                return "대표이미지 입력 방식을 변경합니다. 확인"

            def locator(self, _selector: str):
                return Collection([Button(self.page)])

        class Overlay:
            def __init__(self, page) -> None:
                self.page = page

            def is_visible(self) -> bool:
                return self.page.overlay_visible

        class Page:
            def __init__(self) -> None:
                self.alert_visible = True
                self.overlay_visible = True
                self.alert = Alert(self)
                self.overlay = Overlay(self)

            def locator(self, selector: str):
                if selector == ".sweet-alert:visible":
                    return Collection([self.alert] if self.alert_visible else [])
                if selector == ".sweet-overlay":
                    return Collection([self.overlay])
                raise AssertionError(selector)

            def wait_for_timeout(self, _milliseconds: int) -> None:
                pass

        page = Page()
        message = _dismiss_visible_wing_confirmation(page, wait_ms=0)
        self.assertIn("대표이미지", message)
        self.assertFalse(page.overlay_visible)

    def test_prefers_latest_registration_page_over_other_wing_tabs(self) -> None:
        class Page:
            def __init__(self, url):
                self.url = url

        class Context:
            pages = [
                Page("https://wing.coupang.com/"),
                Page("https://example.com/"),
                Page(
                    "https://wing.coupang.com/tenants/seller-web/vendor-inventory/formV2"
                ),
            ]

        selected = _preferred_wing_page(Context())

        self.assertIn("vendor-inventory/formV2", selected.url)

    def test_finds_wing_page_among_controlled_chrome_targets(self) -> None:
        target = _wing_page_target(
            [
                {"type": "page", "url": "https://example.com", "id": "other"},
                {
                    "type": "page",
                    "url": "https://wing.coupang.com/",
                    "id": "wing-page",
                },
            ]
        )

        self.assertEqual(target["id"], "wing-page")

    def test_reads_virtual_option_total_and_winner_price(self) -> None:
        self.assertEqual(_parse_option_total("옵션 목록 (총 22 개)"), 22)
        self.assertEqual(_parse_option_total("총 25 | 선택됨 0"), 25)
        self.assertEqual(
            _parse_option_row_winner_price("19,800\n(-0,+0)"), 19_800
        )
        self.assertEqual(
            _parse_option_row_winner_price(
                "4. 옵션명 판매자배송 ₩ ₩ 327,800 (-0,+0)"
            ),
            327_800,
        )

    def test_falls_back_to_uniquely_matched_catalog_winner_price(self) -> None:
        matched = [
            {
                "name": "130x70 테이블 6인 의자, 1cm, 기본 의자 구성",
                "winner_price": 851_800,
            },
            {"name": "120x60 테이블 4인 의자", "winner_price": 712_000},
        ]
        self.assertEqual(
            _matched_option_winner_price(
                "130x70 테이블 6인 의자, 1cm, 기본 의자 구성",
                matched,
            ),
            851_800,
        )
        self.assertEqual(
            _normalize_option_identity("130x70 테이블, 6인"),
            "130x70테이블6인",
        )

    def test_does_not_guess_ambiguous_catalog_winner_price(self) -> None:
        matched = [
            {"name": "블랙 대형", "winner_price": 20_000},
            {"name": "블랙 대형 세트", "winner_price": 30_000},
        ]
        self.assertIsNone(_matched_option_winner_price("블랙", matched))

    def test_matches_option_price_when_size_and_color_order_changes(self) -> None:
        matched = [
            {"name": "3XL, 그레이 블랙", "winner_price": 216_280},
            {"name": "L, 그레이 블랙", "winner_price": 216_280},
        ]
        self.assertEqual(
            _matched_option_winner_price("그레이 블랙, 3XL", matched),
            216_280,
        )

    def test_matches_option_when_new_wing_hides_required_middle_part(self) -> None:
        matched = [
            {
                "name": "130x70 테이블 6인 의자, 1cm, 기본 의자 구성",
                "winner_price": 851_800,
            },
            {
                "name": "160x90 테이블, 1cm, 기본 의자 구성",
                "winner_price": 912_000,
            },
        ]
        self.assertEqual(
            _matched_option_winner_price(
                "130x70 테이블 6인 의자, 기본 의자 구성", matched
            ),
            851_800,
        )

    def test_does_not_guess_when_hidden_required_part_is_ambiguous(self) -> None:
        matched = [
            {
                "name": "130x70 테이블 6인 의자, 1cm, 기본 의자 구성",
                "winner_price": 851_800,
            },
            {
                "name": "130x70 테이블 6인 의자, 2cm, 기본 의자 구성",
                "winner_price": 861_800,
            },
        ]
        self.assertIsNone(
            _matched_option_winner_price(
                "130x70 테이블 6인 의자, 기본 의자 구성", matched
            )
        )

    def test_reliable_click_uses_dom_click_when_button_is_outside_viewport(self) -> None:
        class Button:
            def __init__(self) -> None:
                self.evaluated = False

            def scroll_into_view_if_needed(self, *, timeout: int) -> None:
                self.timeout = timeout

            def click(self, *, timeout: int) -> None:
                raise RuntimeError("element is outside of the viewport")

            def evaluate(self, script: str) -> None:
                self.evaluated = "element.click" in script

        button = Button()
        _click_reliably(button)
        self.assertTrue(button.evaluated)

    def test_restores_original_option_name_from_completed_wing_row(self) -> None:
        self.assertEqual(
            _required_option_row_name("3XL, 그레이 블랙, 3XL 수정"),
            "3XL, 그레이 블랙",
        )
        self.assertEqual(
            _required_option_row_name("L, 그레이 블랙 수정"),
            "L, 그레이 블랙",
        )

    def test_option_editor_waits_until_catalog_modal_is_closed(self) -> None:
        state = _option_price_editor_state(
            _OptionStatePage(modal=True, table_rows=2), 2
        )
        self.assertFalse(state["ready"])

    def test_option_editor_accepts_rendered_table_rows(self) -> None:
        state = _option_price_editor_state(
            _OptionStatePage(modal=False, table_rows=2), 2
        )
        self.assertTrue(state["ready"])

    def test_option_editor_accepts_div_grid_only_with_bulk_controls(self) -> None:
        self.assertTrue(
            _option_price_editor_state(
                _OptionStatePage(blank_inputs=12, bulk=True), 2
            )["ready"]
        )
        self.assertFalse(
            _option_price_editor_state(
                _OptionStatePage(blank_inputs=12, bulk=False), 2
            )["ready"]
        )

    def test_option_editor_accepts_required_option_completion_stage(self) -> None:
        state = _option_price_editor_state(
            _OptionStatePage(option_total=21, completion=True), 21
        )
        self.assertTrue(state["ready"])
        self.assertTrue(state["required_options_pending"])

    def test_parses_winner_price_with_spacing_and_commas(self) -> None:
        self.assertEqual(
            parse_winner_price("아이템 위너 가격: 24,000원"),
            24_000,
        )
        self.assertEqual(parse_winner_price("아이템 위너가: ₩493,640"), 493_640)

    def test_reads_new_wing_catalog_option_name(self) -> None:
        self.assertEqual(
            _catalog_option_name(
                "상품 제목 옵션명 3. 크레인 무선 모니터링 30배 배터리 "
                "상품번호 9217548732 아이템 위너가: ₩493,640",
                "옵션 1",
            ),
            "크레인 무선 모니터링 30배 배터리",
        )

    def test_uses_product_id_for_new_wing_identifier_search(self) -> None:
        self.assertEqual(
            _new_wing_identifier(
                "https://www.coupang.com/vp/products/9217548732?itemId=27234407888"
            ),
            "9217548732",
        )

    def test_returns_none_when_winner_price_is_blank(self) -> None:
        self.assertIsNone(parse_winner_price("아이템 위너 가격: -"))

    def test_uses_requested_low_price_return_fee_policy(self) -> None:
        self.assertEqual(choose_return_fee(19_150), 10_000)
        self.assertEqual(choose_return_fee(36_160), 30_000)

    def test_uses_primary_cap_for_higher_price(self) -> None:
        self.assertEqual(choose_return_fee(250_000), 200_000)

    def test_return_fee_uses_explicit_wing_maximum(self) -> None:
        self.assertEqual(
            _return_fee_adjustment_target(
                30_000,
                ["[반품배송비는 0원 ~ 20,000원 까지 가능합니다.]"],
                allow_generic_fallback=True,
            ),
            20_000,
        )

    def test_return_fee_uses_non_round_wing_maximum_exactly(self) -> None:
        self.assertEqual(
            _return_fee_adjustment_target(
                30_000,
                ["[반품배송비는 0원 ~ 15,500원 까지 가능합니다.]"],
                allow_generic_fallback=True,
            ),
            15_500,
        )

    def test_return_fee_generic_rejection_falls_back_to_one_hundred_thousand(self) -> None:
        self.assertEqual(
            _return_fee_adjustment_target(
                200_000,
                ["입력항목을 모두 확인해주세요! 반품/교환 항목을 확인해주세요"],
                allow_generic_fallback=True,
            ),
            100_000,
        )

    def test_return_fee_does_not_fall_back_before_validation(self) -> None:
        self.assertIsNone(
            _return_fee_adjustment_target(
                200_000,
                ["반품배송비(편도)"],
                allow_generic_fallback=False,
            )
        )

    def test_catalog_select_all_row_is_not_treated_as_an_option(self) -> None:
        self.assertTrue(_is_select_all_catalog_option("전체 선택"))
        self.assertTrue(_is_select_all_catalog_option("모두선택"))
        self.assertFalse(_is_select_all_catalog_option("그린 레이저 1개"))

    def test_reads_other_seller_count_from_catalog_option(self) -> None:
        self.assertEqual(_catalog_other_seller_count("이 상품의 다른 판매자(3)"), 3)
        self.assertEqual(_catalog_other_seller_count("다른 판매자 ( 0 )"), 0)
        self.assertEqual(
            _catalog_other_seller_count("이 상품의 다른 판매자(1,203)"),
            1_203,
        )
        self.assertIsNone(_catalog_other_seller_count("아이템 위너 가격 12,000원"))

    def test_coupang_http_image_url_is_upgraded_to_https(self) -> None:
        self.assertEqual(
            _normalize_coupang_image_url(
                "http://image1.coupangcdn.com/image/vendor_inventory/a.jpg"
            ),
            "https://image1.coupangcdn.com/image/vendor_inventory/a.jpg",
        )
        self.assertEqual(
            _normalize_coupang_image_url("//image1.coupangcdn.com/a.png"),
            "https://image1.coupangcdn.com/a.png",
        )

    def test_coupang_thumbnail_url_is_changed_to_original_cdn(self) -> None:
        self.assertEqual(
            _original_coupang_image_url(
                "//thumbnail.coupangcdn.com/thumbnails/remote/492x492ex/"
                "image/vendor_inventory/ab/main.jpg"
            ),
            "https://image.coupangcdn.com/image/vendor_inventory/ab/main.jpg",
        )
        self.assertEqual(
            _original_coupang_image_url(
                "https://thumbnail6.coupangcdn.com/thumbnails/remote/q89/"
                "image/retail/images/main.jpg"
            ),
            "https://image.coupangcdn.com/image/retail/images/main.jpg",
        )

    def test_product_image_is_prepared_as_one_thousand_pixel_square(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            destination = root / "prepared.jpg"
            Image.new("RGB", (492, 360), "red").save(source)

            result = _make_square_product_image(source, destination)

            self.assertEqual(result, str(destination))
            with Image.open(destination) as image:
                self.assertEqual(image.size, (1_000, 1_000))

    def test_cached_detail_image_can_supply_missing_main_image(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            source_dir.mkdir()
            source = source_dir / "detail_002.jpg"
            Image.new("RGB", (780, 500), "navy").save(source)

            result = _cached_detail_main_candidate(
                root / "product_image",
                root / "product_image" / "main_1000.jpg",
            )

            self.assertTrue(Path(result).is_file())
            with Image.open(result) as image:
                self.assertEqual(image.size, (1_000, 1_000))

    def test_missing_quantity_option_defaults_to_one(self) -> None:
        self.assertEqual(
            _infer_required_option_values(
                {"type_name": "수량", "component": "attributes/create/Numeric"},
                [],
                [{"name": "대형", "winner_price": 20_000}],
                "123",
            ),
            ["1"],
        )

    def test_missing_unit_option_defaults_to_numeric_one(self) -> None:
        self.assertEqual(
            _infer_required_option_values(
                {
                    "type_name": "개당 중량/개당 용량",
                    "component": "attributes/create/UnitInput",
                },
                [],
                [{"name": "새로운 철 트럼펫", "winner_price": 50_500}],
                "8728851565",
            ),
            ["1"],
        )

    def test_missing_size_option_uses_single_basic_value(self) -> None:
        self.assertEqual(
            _infer_required_option_values(
                {
                    "type_name": "사이즈",
                    "component": "attributes/create/TextInput",
                },
                [],
                [{"name": "S5 브라운 플러그인", "winner_price": 314_930}],
                "9335025825",
            ),
            ["기본형"],
        )

    def test_missing_fashion_size_uses_actual_catalog_sizes(self) -> None:
        matched = [
            {"name": "3XL, 그레이 블랙", "winner_price": 216_280},
            {"name": "L, 그레이 블랙", "winner_price": 216_280},
            {"name": "XL, 그레이 블랙", "winner_price": 216_280},
            {"name": "M, 샴페인 아이보리", "winner_price": 216_280},
            {"name": "2XL, 샴페인 아이보리", "winner_price": 216_280},
        ]
        self.assertEqual(
            _infer_required_option_values(
                {
                    "type_name": "패션의류/잡화 사이즈",
                    "component": "attributes/create/TextInput",
                },
                [],
                matched,
                "123",
            ),
            ["3XL", "L", "XL", "M", "2XL"],
        )
        self.assertEqual(
            _standard_size_values(["S5 브라운 플러그인"]),
            [],
        )

    def test_missing_model_uses_existing_optional_variant_values(self) -> None:
        attributes = [
            {"mandatory": True, "type_name": "모델명/품번", "values": []},
            {"mandatory": False, "type_name": "스타일", "values": ["B", "C"]},
        ]
        self.assertEqual(
            _infer_required_option_values(
                attributes[0], attributes, [{"name": "1개"}], "123"
            ),
            ["B", "C"],
        )

    def test_missing_color_uses_named_color_or_mixed_color(self) -> None:
        attribute = {"type_name": "색상", "component": "attributes/create/TextInput"}
        self.assertEqual(
            _infer_required_option_values(
                attribute, [], [{"name": "오렌지 휠"}], "123"
            ),
            ["오렌지"],
        )
        self.assertEqual(
            _infer_required_option_values(attribute, [], [{"name": "B형"}], "123"),
            ["혼합색상"],
        )

    def test_many_detail_images_keep_independent_section_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources = []
            for index in range(24):
                source = root / f"source_{index:02d}.png"
                Image.new("RGB", (780, 780), (index, 80, 160)).save(source)
                sources.append(str(source))

            results = _detail_upload_assets(
                sources,
                output_dir=root / "panels",
            )

            self.assertEqual(len(results), len(sources))
            for result in results:
                with Image.open(result) as panel:
                    self.assertEqual(panel.width, 780)
                    self.assertLessEqual(panel.height, 4_800)

    def test_narrow_images_are_normalized_without_merging_boundaries(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            red = root / "red.png"
            blue = root / "blue.png"
            Image.new("RGB", (520, 400), "red").save(red)
            Image.new("RGB", (520, 400), "blue").save(blue)

            results = _detail_upload_assets(
                [str(red), str(blue)],
                output_dir=root / "sections",
            )

            self.assertEqual(len(results), 2)
            with Image.open(results[0]) as first, Image.open(results[1]) as second:
                self.assertEqual(first.width, 780)
                self.assertEqual(second.width, 780)
                self.assertGreater(first.getpixel((10, 10))[0], 200)
                self.assertGreater(second.getpixel((10, 10))[2], 200)

    def test_reads_detail_urls_from_new_wing_form_state(self) -> None:
        html = (
            '{"imageType":"REPRESENTATION","cdnPath":"vendor_inventory/main.jpg"},'
            '{"imageType":"DETAIL","cdnPath":"vendor_inventory/a/one.jpg"},'
            '{"imageType":"DETAIL","cdnPath":"vendor_inventory/b/two.png"},'
            '{"imageType":"DETAIL","cdnPath":"vendor_inventory/a/one.jpg"},'
            '{"contentsType":"IMAGE_NO_SPACE","contentDetails":['
            '{"content":"vendor_inventory/c/three.jpg","detailType":"IMAGE"}]}'
        )

        self.assertEqual(
            _detail_urls_from_wing_state(html),
            [
                "https://image.coupangcdn.com/image/vendor_inventory/a/one.jpg",
                "https://image.coupangcdn.com/image/vendor_inventory/b/two.png",
                "https://image.coupangcdn.com/image/vendor_inventory/c/three.jpg",
            ],
        )

    def test_small_detail_set_keeps_original_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources = []
            for index in range(3):
                source = root / f"source_{index:02d}.jpg"
                Image.new("RGB", (780, 600), "white").save(source)
                sources.append(str(source))

            results = _detail_upload_assets(
                sources,
                output_dir=root / "panels",
            )

            self.assertEqual(results, sources)
            self.assertFalse((root / "panels").exists())

    def test_narrow_fixed_page_is_uploaded_at_full_editor_width(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top = root / "top.jpg"
            product = root / "product.jpg"
            bottom = root / "bottom.png"
            Image.new("RGB", (780, 700), "white").save(top)
            Image.new("RGB", (780, 900), "navy").save(product)
            Image.new("RGB", (532, 2_048), "skyblue").save(bottom)

            results = _detail_upload_assets(
                [str(top), str(product), str(bottom)],
                output_dir=root / "panels",
            )

            self.assertGreaterEqual(len(results), 1)
            for result in results:
                with Image.open(result) as panel:
                    self.assertEqual(panel.width, 780)

    def test_opens_new_registration_form_after_success(self) -> None:
        page = _FakePage()

        _open_new_registration_after_success(page)

        self.assertTrue(page.clicked_new_registration)
        self.assertTrue(page.waited_for_registration_form)
        self.assertEqual(page.goto_url, "")

    def test_falls_back_to_registration_url_when_success_link_changes(self) -> None:
        page = _FakePage(has_new_registration_link=False)

        _open_new_registration_after_success(page)

        self.assertFalse(page.clicked_new_registration)
        self.assertIn("vendor-inventory/formV2", page.goto_url)
        self.assertTrue(page.waited_for_registration_form)

    def test_recognizes_wing_post_save_choice_as_completion(self) -> None:
        page = _ChoicePage({"상품목록", "새로운 상품등록"})

        self.assertTrue(_post_save_choice_visible(page))

    def test_does_not_treat_partial_choice_text_as_completion(self) -> None:
        page = _ChoicePage({"새로운 상품등록"})

        self.assertFalse(_post_save_choice_visible(page))

    def test_reads_safe_return_url_from_wing_password_reminder(self) -> None:
        url = (
            "https://wing.coupang.com/configuration/account/change-password/guide"
            "?returnUrl=http%3A%2F%2Fwing.coupang.com%2Ftenants%2Fseller-web%2F"
            "vendor-inventory%2Fmodify%3FvendorInventoryId%3D123"
        )

        self.assertEqual(
            _wing_password_guide_return_url(url),
            "https://wing.coupang.com/tenants/seller-web/vendor-inventory/modify"
            "?vendorInventoryId=123",
        )

    def test_rejects_external_return_url_from_wing_password_reminder(self) -> None:
        url = (
            "https://wing.coupang.com/configuration/account/change-password/guide"
            "?returnUrl=https%3A%2F%2Fevil.example%2F"
        )

        self.assertEqual(_wing_password_guide_return_url(url), "")

    def test_aligns_manual_option_images_and_uses_main_only_for_missing_options(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            red = root / "red.jpg"
            blue = root / "blue.jpg"
            main = root / "main.jpg"
            for path in (red, blue, main):
                Image.new("RGB", (100, 100), "white").save(path)

            result = _align_option_image_paths(
                [str(red), str(blue)],
                option_count=3,
                fallback_main=str(main),
            )

            self.assertEqual(result, [str(red), str(blue), str(main)])


if __name__ == "__main__":
    unittest.main()
