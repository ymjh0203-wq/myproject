import sys

from PySide6.QtWidgets import QApplication

from app.db.repository import init_db
from app.ui.main_window import MainWindow


def main():
    init_db()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
