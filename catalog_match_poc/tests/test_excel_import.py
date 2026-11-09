from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from poc.excel_import import ExcelImportError, read_registration_workbook


def _xlsx(path: Path, data_rows: list[list[str]]) -> None:
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="상품등록" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    rows = [["등록 선택", "쿠팡 상품 URL", "배송비 방식", "WING 처리 방식", "상세 이미지 방식"]] + data_rows
    row_xml = []
    for row_number, values in enumerate(rows, start=9):
        cells = []
        for column, value in enumerate(values):
            reference = f"{chr(65 + column)}{row_number}"
            cells.append(
                f'<c r="{reference}" t="inlineStr"><is><t>{value}</t></is></c>'
            )
        row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


class ExcelImportTests(unittest.TestCase):
    def test_reads_selected_rows_and_maps_options(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "input.xlsx"
            _xlsx(
                path,
                [
                    ["예", "https://www.coupang.com/vp/products/9334344464", "무료배송", "상품등록", "원본 유지"],
                    ["아니오", "https://www.coupang.com/vp/products/9226911400", "착불배송", "임시저장", "한국어 자연화"],
                    ["Y", "https://www.coupang.com/vp/products/8287975215", "착불", "draft", "번역"],
                ],
            )
            result = read_registration_workbook(path)

        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.excluded, 1)
        self.assertEqual(result.rows[0].shipping_fee_type, "free")
        self.assertEqual(result.rows[0].submission_mode, "final")
        self.assertEqual(result.rows[0].translation_mode, "original")
        self.assertEqual(result.rows[1].shipping_fee_type, "cash_on_delivery")
        self.assertEqual(result.rows[1].submission_mode, "draft")
        self.assertEqual(result.rows[1].translation_mode, "auto_ko")

    def test_reports_the_row_with_an_invalid_choice(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "input.xlsx"
            _xlsx(
                path,
                [["예", "https://www.coupang.com/vp/products/9334344464", "직접배송", "상품등록", "원본 유지"]],
            )
            with self.assertRaisesRegex(ExcelImportError, "10행"):
                read_registration_workbook(path)


if __name__ == "__main__":
    unittest.main()
