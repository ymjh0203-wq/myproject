from __future__ import annotations

import unittest

from poc.detail_page import build_editor_content, classify_detail_block


class DetailPageTests(unittest.TestCase):
    def test_removes_clear_other_seller_fixed_page(self) -> None:
        decision = classify_detail_block(
            "달팍몰 판매자 고객센터 010-1234-5678 카카오톡 문의"
        )

        self.assertEqual(decision["action"], "remove")
        self.assertEqual(decision["reason"], "other_seller_fixed_page")

    def test_keeps_own_fixed_page(self) -> None:
        decision = classify_detail_block(
            "미노바 고객센터 배송 안내",
            own_seller_markers=["미노바"],
        )

        self.assertEqual(decision["action"], "keep")
        self.assertEqual(decision["reason"], "own_fixed_page")

    def test_reviews_mixed_product_and_seller_information(self) -> None:
        decision = classify_detail_block(
            "제품 사이즈와 옵션 안내, 판매자 문의전화 010-1234-5678"
        )

        self.assertEqual(decision["action"], "review")

    def test_builds_editor_order_and_keeps_review_candidate(self) -> None:
        result = build_editor_content(
            [
                {"id": "product", "asset": "product.jpg", "text": "제품 사양과 치수"},
                {
                    "id": "seller",
                    "asset": "seller.jpg",
                    "text": "다른 스토어 판매자 고객센터 카카오톡 문의",
                },
                {
                    "id": "mixed",
                    "asset": "mixed.jpg",
                    "text": "상품 옵션과 배송 안내",
                },
            ],
            fixed_page_top_assets=["livingred-top.jpg"],
            fixed_page_bottom_assets=["livingred-bottom.jpg"],
        )

        self.assertEqual(
            [item["asset"] for item in result["editor_sequence"]],
            [
                "livingred-top.jpg",
                "product.jpg",
                "mixed.jpg",
                "livingred-bottom.jpg",
            ],
        )
        self.assertEqual(
            [item["id"] for item in result["removed_blocks"]], ["seller"]
        )
        self.assertEqual([item["id"] for item in result["review_blocks"]], ["mixed"])
        self.assertTrue(result["preview_required"])


if __name__ == "__main__":
    unittest.main()
