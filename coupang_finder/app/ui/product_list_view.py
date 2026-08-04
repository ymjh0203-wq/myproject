from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.db import repository
from app.ui.widgets import build_product_table, populate_product_table


class ProductListView(QWidget):
    """2. 발굴상품 화면 — 필터를 통과해 저장된 상품 목록."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.table = build_product_table()

        refresh_button = QPushButton("새로고침")
        refresh_button.clicked.connect(self.refresh)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("발굴상품 (저장된 유망상품)"))
        top_row.addStretch()
        top_row.addWidget(refresh_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self.table)

        self.refresh()

    def refresh(self):
        rows = repository.list_products(status="저장")
        populate_product_table(self.table, rows)
