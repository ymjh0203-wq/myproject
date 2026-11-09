from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        from PySide6.QtCore import QLockFile
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWidgets import QMessageBox
    except ImportError:
        print("PySide6가 설치되지 않았습니다.")
        print("먼저 이 폴더의 '설치.bat'을 더블클릭한 뒤 '실행.bat'을 실행하세요.")
        return 1

    from poc.ui import Phase0Window

    app = QApplication(sys.argv)
    lock_path = Path(__file__).resolve().parent / "data" / "catalog_match_poc.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    app_lock = QLockFile(str(lock_path))
    app_lock.setStaleLockTime(0)
    if not app_lock.tryLock(100):
        QMessageBox.information(
            None,
            "프로그램이 이미 실행 중입니다",
            "쿠팡 상품 일괄등록 작업 도우미가 이미 열려 있습니다.\n\n"
            "기존 프로그램 창을 사용하거나, 기존 창을 완전히 닫은 뒤 다시 실행하세요.",
        )
        return 0
    window = Phase0Window()
    window.show()
    try:
        return app.exec()
    finally:
        app_lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
