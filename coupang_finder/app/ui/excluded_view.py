from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from app.db import repository
from app.ui.widgets import EXCLUDED_COLUMNS, build_product_table, populate_product_table


class ExcludedView(QWidget):
    """4. 제외상품 화면 — 제외 사유와 함께 보관, 재검토 후 되돌리기 가능."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.table = build_product_table(EXCLUDED_COLUMNS)

        requeue_button = QPushButton("선택 항목 검토중으로 되돌리기")
        requeue_button.clicked.connect(self.requeue_selected)

        refresh_button = QPushButton("새로고침")
        refresh_button.clicked.connect(self.refresh)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("제외상품 (사유와 함께 보관)"))
        top_row.addStretch()
        top_row.addWidget(requeue_button)
        top_row.addWidget(refresh_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self.table)

        self.refresh()

    def refresh(self):
        rows = repository.list_products(status="제외")
        populate_product_table(self.table, rows, EXCLUDED_COLUMNS)

    def requeue_selected(self):
        selected_rows = {index.row() for index in self.table.selectionModel().selectedIndexes()}
        if not selected_rows:
            QMessageBox.information(self, "안내", "되돌릴 상품을 목록에서 선택해주세요.")
            return

        learned_words = []
        for row in selected_rows:
            item = self.table.item(row, 0)
            product_id = item.data(Qt.ItemDataRole.UserRole) if item else None
            if product_id is None:
                continue

            product = repository.get_product(product_id)
            if (
                product
                and product["exclusion_reason"] == "효자상품"
                and product["hyoja_prefix"]
            ):
                repository.promote_word_to_dictionary(product["hyoja_prefix"], "속성어")
                learned_words.append(product["hyoja_prefix"])
                repository.update_product_filters(
                    product_id, hyoja_status="정상", hyoja_prefix=None
                )

            repository.update_product_status(product_id, "검토중", None)

        if learned_words:
            QMessageBox.information(
                self,
                "사전 학습됨",
                "다음 단어를 정상 단어로 사전에 등록했습니다 (다음부터 효자상품으로 의심하지 않습니다):\n"
                + ", ".join(sorted(set(learned_words))),
            )
        self.refresh()
