# ==========================================================
# CSV 읽기 공통 도구 (csv_utils.py)
# ----------------------------------------------------------
# 엑셀로 CSV 파일을 저장할 때, "CSV UTF-8"이 아니라 그냥 "CSV(쉼표로 분리)"로
# 저장하면 한글 윈도우 기본 인코딩(CP949/ANSI)으로 저장되어 버립니다.
# 그러면 UTF-8 기준으로 읽던 프로그램이 UnicodeDecodeError로 멈추게 됩니다.
#
# 이 파일은 어떤 인코딩으로 저장됐든 순서대로 시도해보면서 자동으로 맞는 것을 찾아 읽습니다.
# 사장님이 엑셀 저장 방식을 매번 신경 쓰지 않으셔도 되도록 하기 위함입니다.
# ==========================================================

import csv

ENCODINGS_TO_TRY = ["utf-8-sig", "cp949", "utf-8"]


def read_csv_rows(path):
    """
    CSV 파일을 읽어서 딕셔너리 리스트로 돌려줍니다. (csv.DictReader 결과와 동일)
    인코딩을 자동으로 맞춰서 읽으므로, 엑셀에서 어떤 형식으로 저장했든 상관없습니다.
    """
    last_error = None
    for encoding in ENCODINGS_TO_TRY:
        try:
            with open(path, encoding=encoding, newline="") as f:
                reader = csv.DictReader(f)
                return list(reader)
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
    # 모든 인코딩 시도가 실패한 경우에만 에러를 전달
    raise last_error
