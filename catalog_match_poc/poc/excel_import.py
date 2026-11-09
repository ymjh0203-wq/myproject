"""쿠팡 상품 일괄등록 엑셀 양식 읽기."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CELL_REF_RE = re.compile(r"([A-Z]+)")


class ExcelImportError(ValueError):
    """사용자가 수정할 수 있는 엑셀 양식 오류."""


@dataclass(frozen=True)
class RegistrationExcelRow:
    row_number: int
    source_url: str
    shipping_fee_type: str
    submission_mode: str
    translation_mode: str


@dataclass(frozen=True)
class RegistrationExcelResult:
    rows: list[RegistrationExcelRow]
    excluded: int
    empty: int


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split()).strip()


def _column_index(reference: str) -> int:
    match = CELL_REF_RE.match(reference.upper())
    if not match:
        return 0
    value = 0
    for character in match.group(1):
        value = value * 26 + ord(character) - 64
    return value - 1


def _shared_strings(archive: ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(node.text or "" for node in item.findall(f".//{{{MAIN_NS}}}t"))
        for item in root.findall(f"{{{MAIN_NS}}}si")
    ]


def _worksheet_path(archive: ZipFile, wanted_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        item.attrib.get("Id", ""): item.attrib.get("Target", "")
        for item in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
    }
    sheets = workbook.find(f"{{{MAIN_NS}}}sheets")
    if sheets is None:
        raise ExcelImportError("엑셀 파일에서 시트를 찾지 못했습니다.")
    chosen = None
    first = None
    for sheet in sheets.findall(f"{{{MAIN_NS}}}sheet"):
        first = first or sheet
        if _clean(sheet.attrib.get("name")) == wanted_name:
            chosen = sheet
            break
    if chosen is None:
        chosen = first
    if chosen is None:
        raise ExcelImportError("엑셀 파일에서 시트를 찾지 못했습니다.")
    relation_id = chosen.attrib.get(f"{{{REL_NS}}}id", "")
    target = targets.get(relation_id, "")
    if not target:
        raise ExcelImportError("엑셀 시트 연결 정보를 읽지 못했습니다.")
    if target.startswith("/"):
        return target.lstrip("/")
    return str(PurePosixPath("xl") / target)


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(
            node.text or "" for node in cell.findall(f".//{{{MAIN_NS}}}t")
        )
    value_node = cell.find(f"{{{MAIN_NS}}}v")
    value = value_node.text if value_node is not None and value_node.text else ""
    if cell_type == "s" and value:
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return ""
    return value


def _sheet_rows(archive: ZipFile, worksheet_path: str) -> list[tuple[int, list[str]]]:
    shared = _shared_strings(archive)
    root = ET.fromstring(archive.read(worksheet_path))
    output: list[tuple[int, list[str]]] = []
    for row in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
        row_number = int(row.attrib.get("r", len(output) + 1))
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            index = _column_index(cell.attrib.get("r", ""))
            values[index] = _cell_text(cell, shared)
        last = max(values, default=-1)
        output.append((row_number, [values.get(index, "") for index in range(last + 1)]))
    return output


def _mapped_value(value: str, mapping: dict[str, str], label: str, row: int) -> str:
    normalized = _clean(value).lower().replace(" ", "").replace("_", "")
    result = mapping.get(normalized)
    if result:
        return result
    allowed = " / ".join(dict.fromkeys(mapping.values()))
    raise ExcelImportError(
        f"{row}행의 ‘{label}’ 값이 올바르지 않습니다. 양식의 선택 목록을 사용하세요. "
        f"(허용값: {allowed})"
    )


def read_registration_workbook(path: str | Path) -> RegistrationExcelResult:
    """고정 양식 또는 같은 머리글을 가진 xlsx 파일에서 등록 행을 읽는다."""

    source = Path(path)
    if source.suffix.lower() != ".xlsx":
        raise ExcelImportError(".xlsx 형식의 엑셀 파일만 불러올 수 있습니다.")
    try:
        with ZipFile(source) as archive:
            rows = _sheet_rows(archive, _worksheet_path(archive, "상품등록"))
    except FileNotFoundError as exc:
        raise ExcelImportError("선택한 엑셀 파일을 찾을 수 없습니다.") from exc
    except (BadZipFile, ET.ParseError, KeyError) as exc:
        raise ExcelImportError("엑셀 파일이 손상되었거나 지원하지 않는 형식입니다.") from exc

    header_row = None
    header_map: dict[str, int] = {}
    for row_number, values in rows[:40]:
        normalized = {_clean(value): index for index, value in enumerate(values)}
        if "쿠팡 상품 URL" in normalized:
            header_row = row_number
            header_map = normalized
            break
    required = ("등록 선택", "쿠팡 상품 URL", "배송비 방식", "WING 처리 방식", "상세 이미지 방식")
    if header_row is None or any(name not in header_map for name in required):
        raise ExcelImportError(
            "상품등록 양식의 제목 행을 찾지 못했습니다. 프로그램에서 받은 원본 양식을 사용하세요."
        )

    selection_map = {
        "예": "yes", "y": "yes", "yes": "yes", "1": "yes", "등록": "yes",
        "아니오": "no", "아니요": "no", "n": "no", "no": "no", "0": "no", "제외": "no",
    }
    shipping_map = {
        "무료배송": "free", "무료": "free", "free": "free",
        "착불배송": "cash_on_delivery", "착불": "cash_on_delivery", "cod": "cash_on_delivery",
    }
    submission_map = {
        "상품등록": "final", "등록": "final", "final": "final",
        "임시저장": "draft", "임시": "draft", "draft": "draft",
    }
    translation_map = {
        "원본유지": "original", "원본": "original", "original": "original",
        "한국어자연화": "auto_ko", "자연화": "auto_ko", "번역": "auto_ko", "autoko": "auto_ko",
    }

    imported: list[RegistrationExcelRow] = []
    excluded = empty = 0
    for row_number, values in rows:
        if row_number <= header_row:
            continue
        get = lambda name: values[header_map[name]] if header_map[name] < len(values) else ""
        source_url = _clean(get("쿠팡 상품 URL"))
        if not source_url:
            empty += 1
            continue
        selection = _mapped_value(get("등록 선택") or "예", selection_map, "등록 선택", row_number)
        if selection == "no":
            excluded += 1
            continue
        imported.append(
            RegistrationExcelRow(
                row_number=row_number,
                source_url=source_url,
                shipping_fee_type=_mapped_value(get("배송비 방식"), shipping_map, "배송비 방식", row_number),
                submission_mode=_mapped_value(get("WING 처리 방식"), submission_map, "WING 처리 방식", row_number),
                translation_mode=_mapped_value(get("상세 이미지 방식"), translation_map, "상세 이미지 방식", row_number),
            )
        )
    if not imported:
        raise ExcelImportError(
            "불러올 상품이 없습니다. ‘등록 선택’이 예인 행에 쿠팡 상품 URL을 넣어주세요."
        )
    return RegistrationExcelResult(rows=imported, excluded=excluded, empty=empty)
