from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.db import repository
from app.ui.widgets import build_product_table, populate_product_table


class SGradeView(QWidget):
    """3. S등급 화면 — 검색수 1,000 이상 상품만 필터링."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.table = build_product_table()

        refresh_button = QPushButton("새로고침")
        refresh_button.clicked.connect(self.refresh)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("S등급 상품"))
        top_row.addStretch()
        top_row.addWidget(refresh_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self.table)

        self.refresh()

    def refresh(self):
        rows = repository.list_products(grade="S등급")
        populate_product_table(self.table, rows)
