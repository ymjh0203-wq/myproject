from __future__ import annotations

import unittest

from poc.registration_plan import (
    build_registration_plan,
    build_return_shipping_fee_policy,
    extract_return_fee_allowed_maximum,
    extract_return_fee_upper_bound,
    maximum_return_fee_at_or_below,
    maximum_return_fee_below,
    recalculate_return_shipping_fee,
)


class RegistrationPlanTests(unittest.TestCase):
    def test_builds_requested_defaults_and_matches_price(self) -> None:
        plan = build_registration_plan(
            catalog_match_price=29_700,
            shipping_fee_type="free",
            country_of_origin="중국",
            fixed_page_top_assets=["C:/assets/livingred-top.jpg"],
            fixed_page_bottom_assets=["C:/assets/livingred-bottom.jpg"],
        )

        self.assertEqual(plan["selling_price"], 29_700)
        self.assertEqual(plan["stock_quantity"], 999)
        self.assertEqual(plan["shipping_lead_days"], 9)
        self.assertEqual(plan["shipping_fee_label"], "무료배송")
        self.assertEqual(plan["return_shipping_fee"]["target"], 20_000)
        self.assertEqual(plan["product_notice"]["category"], "기타 재화")
        self.assertEqual(plan["product_notice"]["detail"], "상세페이지 참조")
        self.assertTrue(plan["kc_certification"]["purchase_agent_product"])
        self.assertEqual(plan["detail_page"]["entry_mode_label"], "에디터 작성")
        self.assertTrue(
            plan["detail_page"]["source_cleanup"]["remove_other_seller_sections"]
        )
        self.assertEqual(
            plan["detail_page"]["fixed_page"]["editor_order_label"],
            "상단 → 상품 설명 → 하단",
        )
        self.assertEqual(
            plan["detail_page"]["fixed_page"]["top_assets"],
            ["C:/assets/livingred-top.jpg"],
        )
        self.assertEqual(
            plan["detail_page"]["fixed_page"]["bottom_assets"],
            ["C:/assets/livingred-bottom.jpg"],
        )
        self.assertTrue(plan["ready_for_manual_wing_entry"])

    def test_requires_shipping_choice_price_and_real_origin(self) -> None:
        plan = build_registration_plan(
            catalog_match_price=0,
            shipping_fee_type=None,
            country_of_origin="상세페이지 참조",
        )

        self.assertIn("카탈로그 매칭 판매가", plan["missing_fields"])
        self.assertIn("배송비 방식", plan["missing_fields"])
        self.assertIn("실제 원산지", plan["missing_fields"])
        self.assertIn("상단 고정페이지 이미지", plan["missing_fields"])
        self.assertIn("하단 고정페이지 이미지", plan["missing_fields"])
        self.assertFalse(plan["ready_for_manual_wing_entry"])

    def test_requires_editor_cleanup_and_own_seller_marker(self) -> None:
        plan = build_registration_plan(
            catalog_match_price=86_400,
            shipping_fee_type="free",
            country_of_origin="중국",
            detail_entry_mode="html",
            remove_other_seller_sections=False,
            fixed_page_top_assets=["C:/assets/top.jpg"],
            fixed_page_bottom_assets=["C:/assets/bottom.jpg"],
            own_seller_markers=[],
        )

        self.assertIn("상세페이지 입력 방식(에디터 작성)", plan["missing_fields"])
        self.assertIn("다른 판매자 고정영역 제거", plan["missing_fields"])
        self.assertIn("내 판매자 식별어", plan["missing_fields"])
        self.assertFalse(plan["ready_for_manual_wing_entry"])

    def test_deduplicates_top_and_bottom_assets_without_reordering(self) -> None:
        plan = build_registration_plan(
            catalog_match_price=86_400,
            shipping_fee_type="free",
            country_of_origin="중국",
            fixed_page_top_assets=[" top.jpg ", "top2.jpg", "top.jpg", ""],
            fixed_page_bottom_assets=[" bottom.jpg ", "bottom.jpg", ""],
        )

        self.assertEqual(
            plan["detail_page"]["fixed_page"]["top_assets"],
            ["top.jpg", "top2.jpg"],
        )
        self.assertEqual(
            plan["detail_page"]["fixed_page"]["bottom_assets"],
            ["bottom.jpg"],
        )

    def test_return_fee_is_rounded_down_to_ten_thousand_for_low_price(self) -> None:
        policy = build_return_shipping_fee_policy(86_400)

        self.assertEqual(policy["field_label"], "반품배송비(편도)")
        self.assertEqual(policy["wing_global_minimum"], 0)
        self.assertEqual(policy["wing_global_maximum"], 250_000)
        self.assertIn(20_000, policy["known_wing_maximums"])
        self.assertEqual(policy["target"], 80_000)
        self.assertEqual(policy["attempts_in_order"], [80_000])

    def test_return_fee_for_19150_won_is_10000_won(self) -> None:
        policy = build_return_shipping_fee_policy(19_150)

        self.assertEqual(policy["input_unit"], 10_000)
        self.assertEqual(policy["target"], 10_000)

    def test_return_fee_for_36160_won_is_30000_won(self) -> None:
        policy = build_return_shipping_fee_policy(36_160)

        self.assertEqual(policy["target"], 30_000)

    def test_return_fee_tries_two_hundred_then_one_hundred_thousand(self) -> None:
        policy = build_return_shipping_fee_policy(150_000)

        self.assertEqual(policy["target"], 200_000)
        self.assertEqual(policy["attempts_in_order"], [200_000, 100_000])

    def test_approved_korean_translation_replaces_product_detail_assets(self) -> None:
        plan = build_registration_plan(
            catalog_match_price=86_400,
            shipping_fee_type="free",
            country_of_origin="중국",
            fixed_page_top_assets=["top.png"],
            fixed_page_bottom_assets=["bottom.png"],
            source_detail_assets=["source-1.jpg", "source-2.jpg"],
            translated_detail_assets=["ko-1.jpg", "ko-2.jpg"],
            translation_mode="auto_ko",
            translation_status="approved",
            translation_review_approved=True,
        )

        localization = plan["detail_page"]["localization"]
        self.assertTrue(localization["using_translated_assets"])
        self.assertEqual(
            localization["effective_product_assets"],
            ["ko-1.jpg", "ko-2.jpg"],
        )
        self.assertTrue(localization["rules"]["detect_language_per_image"])
        self.assertTrue(localization["rules"]["translate_chinese_to_korean"])
        self.assertTrue(localization["rules"]["rewrite_awkward_korean"])
        self.assertTrue(localization["rules"]["preserve_natural_korean"])
        self.assertNotIn("한국어 이미지 검수 승인", plan["missing_fields"])

    def test_unapproved_translation_is_not_used_for_registration(self) -> None:
        plan = build_registration_plan(
            catalog_match_price=86_400,
            shipping_fee_type="free",
            country_of_origin="중국",
            fixed_page_top_assets=["top.png"],
            fixed_page_bottom_assets=["bottom.png"],
            source_detail_assets=["source.jpg"],
            translated_detail_assets=["ko.jpg"],
            translation_mode="auto_ko",
            translation_status="review_required",
            translation_review_approved=False,
        )

        localization = plan["detail_page"]["localization"]
        self.assertFalse(localization["using_translated_assets"])
        self.assertEqual(localization["effective_product_assets"], ["source.jpg"])
        self.assertIn("한국어 이미지 검수 승인", plan["missing_fields"])

    def test_uses_watermarked_option_images_but_keeps_main_image_clean(self) -> None:
        plan = build_registration_plan(
            catalog_match_price=86_400,
            shipping_fee_type="free",
            country_of_origin="중국",
            fixed_page_top_assets=["top.png"],
            fixed_page_bottom_assets=["bottom.png"],
            source_option_assets=["red.jpg", "blue.jpg"],
            watermarked_option_assets=["red-marked.jpg", "blue-marked.jpg"],
            watermark_enabled=True,
        )

        images = plan["product_images"]
        self.assertFalse(images["main_image_watermark"])
        self.assertEqual(
            images["effective_option_assets"],
            ["red-marked.jpg", "blue-marked.jpg"],
        )

    def test_extracts_explicit_wing_return_fee_limit(self) -> None:
        self.assertEqual(
            extract_return_fee_upper_bound("반품배송비는 86,400원보다 낮아야 합니다."),
            86_400,
        )

    def test_extracts_inclusive_wing_return_fee_maximum(self) -> None:
        self.assertEqual(
            extract_return_fee_allowed_maximum(
                "[반품배송비는 0원 ~ 15,000원 까지 가능합니다.]"
            ),
            15_000,
        )
        self.assertEqual(
            extract_return_fee_allowed_maximum(
                "반품배송비는 0원~100,000원까지만 입력이 가능합니다."
            ),
            100_000,
        )

    def test_calculates_maximum_in_ten_thousand_won_units_below_limit(self) -> None:
        self.assertEqual(maximum_return_fee_below(86_400), 80_000)

    def test_calculates_inclusive_maximum_in_ten_thousand_won_units(self) -> None:
        self.assertEqual(maximum_return_fee_at_or_below(30_000), 30_000)
        self.assertEqual(maximum_return_fee_at_or_below(15_000), 10_000)
        self.assertEqual(maximum_return_fee_at_or_below(100_000), 100_000)

    def test_recalculates_to_explicit_wing_maximum(self) -> None:
        policy = recalculate_return_shipping_fee(
            minimum_selling_price=120_000,
            wing_validation_message="반품배송비는 75,000원 미만으로 입력해주세요.",
        )

        self.assertEqual(policy["target"], 70_000)
        self.assertEqual(policy["wing_upper_bound"], 75_000)

    def test_recalculates_generic_rejection_to_one_hundred_thousand(self) -> None:
        policy = recalculate_return_shipping_fee(
            minimum_selling_price=150_000,
            wing_validation_message="입력 가능한 반품배송비를 확인해주세요.",
        )

        self.assertEqual(policy["target"], 100_000)


if __name__ == "__main__":
    unittest.main()
