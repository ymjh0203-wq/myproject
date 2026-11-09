import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image, ImageDraw

from poc.seller_page_filter import filter_known_seller_pages, perceptual_hash


class SellerPageFilterTests(unittest.TestCase):
    def test_filters_learned_page_and_keeps_different_product_image(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seller = root / "seller.png"
            product = root / "product.png"
            first = Image.new("RGB", (180, 240), "white")
            draw = ImageDraw.Draw(first)
            for row in range(20, 220, 24):
                draw.rectangle((20, row, 160, row + 7), fill="black")
            first.save(seller)
            second = Image.new("RGB", (180, 240), "black")
            draw = ImageDraw.Draw(second)
            draw.ellipse((25, 45, 155, 175), fill="white")
            second.save(product)
            fingerprints = root / "fingerprints.json"
            fingerprints.write_text(
                json.dumps({"fingerprints": [perceptual_hash(seller)]}),
                encoding="utf-8",
            )

            kept, removed = filter_known_seller_pages(
                [str(product), str(seller)], fingerprint_path=fingerprints
            )

            self.assertEqual(kept, [str(product)])
            self.assertEqual(removed, [str(seller)])


if __name__ == "__main__":
    unittest.main()
