# ==========================================================
# 엑셀(.xlsx) 분리 저장 (excel_export.py)
# ----------------------------------------------------------
# find_keywords.py의 최종 결과를 "추천 후보"와 "제외/검토 필요"로 나눠서
# 진짜 엑셀 전용 파일(.xlsx) 두 개로 저장합니다.
#
# openpyxl 패키지가 설치되어 있어야 합니다 (pip install openpyxl).
# 혹시 없거나 문제가 있으면, 에러로 죽는 대신 같은 이름의 .csv로 대신 저장합니다.
# ==========================================================

import csv
import os

try:
    import openpyxl
    from openpyxl.utils import get_column_letter

    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False


def _write_xlsx(rows, headers, path, sheet_title):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]  # 엑셀 시트 이름은 31자 제한

    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])

    # 컬럼 폭을 내용 길이에 맞춰 대략 조정 (최대 40자)
    for i, header in enumerate(headers, start=1):
        col_letter = get_column_letter(i)
        lengths = [len(str(header))] + [len(str(row.get(header, ""))) for row in rows]
        ws.column_dimensions[col_letter].width = min(max(lengths) + 2, 40)

    wb.save(path)


def _write_csv(rows, headers, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def export_candidates_and_excluded(results, candidates_path, excluded_path, headers=None):
    """
    results(딕셔너리 리스트)를 '제외검토사유'가 있는지 없는지로 나눠서 두 파일로 저장합니다.
    - 제외검토사유 없음 -> candidates_path (추천 후보)
    - 제외검토사유 있음 -> excluded_path (제외/검토 필요)

    results가 0건이어도 두 파일을 항상 최신 상태(빈 결과)로 다시 써서, 예전 실행 결과가
    엉뚱하게 남아있는 일이 없도록 합니다. headers를 안 넘기면 results의 첫 행에서 뽑아 씁니다
    (그래서 results가 0건일 때는 headers를 반드시 넘겨줘야 헤더 줄이 만들어집니다).

    openpyxl이 없으면 .xlsx 대신 같은 이름의 .csv로 저장합니다. (에러로 죽지 않음)

    반환값: (실제로 저장된 candidates 경로, 실제로 저장된 excluded 경로, 후보 수, 제외 수)
    """
    if headers is None:
        headers = list(results[0].keys()) if results else []

    candidates = [r for r in results if not r.get("제외검토사유")]
    excluded = [r for r in results if r.get("제외검토사유")]

    os.makedirs(os.path.dirname(candidates_path), exist_ok=True)

    if OPENPYXL_AVAILABLE:
        _write_xlsx(candidates, headers, candidates_path, "추천 후보")
        _write_xlsx(excluded, headers, excluded_path, "제외 또는 검토 필요")
        return candidates_path, excluded_path, len(candidates), len(excluded)

    print("[안내] openpyxl 패키지가 없어 .xlsx 대신 .csv로 저장합니다.")
    print("       (pip install openpyxl 실행 후 다시 실행하면 진짜 엑셀 파일로 저장됩니다)")
    csv_candidates_path = candidates_path.rsplit(".", 1)[0] + ".csv"
    csv_excluded_path = excluded_path.rsplit(".", 1)[0] + ".csv"
    _write_csv(candidates, headers, csv_candidates_path)
    _write_csv(excluded, headers, csv_excluded_path)
    return csv_candidates_path, csv_excluded_path, len(candidates), len(excluded)
