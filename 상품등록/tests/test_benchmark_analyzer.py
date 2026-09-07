import unittest

from benchmark_analyzer import (
    _choose_image,
    _clean_title,
    _derive_shop_name,
    _parse_number,
    detect_marketplace,
    validate_product_url,
)


class BenchmarkAnalyzerUnitTests(unittest.TestCase):
    def test_supported_url_and_marketplace(self):
        url = "https://smartstore.naver.com/sample/products/123"
        self.assertEqual(validate_product_url(url), url)
        self.assertEqual(detect_marketplace(url), "smartstore")

    def test_unsupported_url(self):
        with self.assertRaises(ValueError):
            validate_product_url("https://example.com/product/1")

    def test_title_cleanup(self):
        self.assertEqual(
            _clean_title("  충전식   철근 절단기 - 네이버 스마트스토어  "),
            "충전식 철근 절단기",
        )

    def test_parse_number(self):
        self.assertEqual(_parse_number("129,800원"), 129800)
        self.assertEqual(_parse_number("0.00건 리뷰"), 0)
        self.assertEqual(_parse_number("1.2만 리뷰"), 12000)
        self.assertIsNone(_parse_number("가격 문의"))

    def test_shop_name_from_naver_page_title(self):
        snapshot = {"shopName": "", "pageTitle": "충전식 철근 절단기 : 글로벌웨이픽"}
        self.assertEqual(_derive_shop_name(snapshot), "글로벌웨이픽")

    def test_choose_large_product_image(self):
        candidates = [
            {"src": "https://img.test/icon-login.png", "width": 512, "height": 512, "source": "img"},
            {"src": "https://img.test/product.jpg", "width": 860, "height": 860, "source": "img"},
        ]
        self.assertEqual(_choose_image(candidates), "https://img.test/product.jpg")


if __name__ == "__main__":
    unittest.main()
