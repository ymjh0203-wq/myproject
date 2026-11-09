from __future__ import annotations

import unittest

from poc.url_parser import parse_coupang_url


class ProductUrlParserTests(unittest.TestCase):
    def test_extracts_ids_from_normal_product_url(self) -> None:
        result = parse_coupang_url(
            "https://www.coupang.com/vp/products/123456789?itemId=987654321&vendorItemId=765432109"
        )
        self.assertTrue(result.is_coupang_url)
        self.assertEqual(result.value_for("productId"), "123456789")
        self.assertEqual(result.value_for("itemId"), "987654321")
        self.assertEqual(result.value_for("vendorItemId"), "765432109")
        self.assertEqual(
            [item.kind for item in result.search_candidates()],
            ["productId", "vendorItemId", "itemId"],
        )

    def test_accepts_url_without_scheme(self) -> None:
        result = parse_coupang_url("www.coupang.com/vp/products/12345")
        self.assertTrue(result.is_coupang_url)
        self.assertEqual(result.value_for("productId"), "12345")

    def test_rejects_lookalike_domain(self) -> None:
        result = parse_coupang_url("https://coupang.com.example.org/vp/products/12345")
        self.assertFalse(result.is_coupang_url)
        self.assertEqual(result.identifiers, ())

    def test_does_not_guess_missing_ids(self) -> None:
        result = parse_coupang_url("https://www.coupang.com/vp/products/12345?foo=999")
        self.assertEqual(result.value_for("productId"), "12345")
        self.assertIsNone(result.value_for("vendorItemId"))
        self.assertIsNone(result.value_for("itemId"))

    def test_records_but_does_not_use_unverified_catalog_query(self) -> None:
        result = parse_coupang_url(
            "https://www.coupang.com/vp/products/12345?catalogId=ABC-999"
        )
        observed = [item for item in result.identifiers if item.kind.startswith("관찰됨:")]
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0].official_meaning_confirmed)
        self.assertNotIn(observed[0], result.search_candidates())

    def test_keeps_path_product_id_on_conflict(self) -> None:
        result = parse_coupang_url(
            "https://www.coupang.com/vp/products/12345?productId=99999"
        )
        self.assertEqual(result.value_for("productId"), "12345")
        self.assertTrue(any("서로 달라" in warning for warning in result.warnings))

    def test_short_url_requires_final_url_when_no_ids(self) -> None:
        result = parse_coupang_url("https://coupa.ng/example")
        self.assertTrue(result.is_coupang_url)
        self.assertTrue(any("최종" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
