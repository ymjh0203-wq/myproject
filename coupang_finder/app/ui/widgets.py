from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem

PRODUCT_COLUMNS = [
    ("product_name", "상품명"),
    ("rating", "별점"),
    ("review_count", "리뷰"),
    ("search_count", "검색수"),
    ("option_quality", "옵션 상태"),
    ("brand_risk_level", "브랜드 위험"),
    ("hyoja_status", "효자상품 여부"),
    ("overseas_score", "해외구매대행 가능성"),
    ("grade", "등급"),
    ("product_url", "URL"),
]

EXCLUDED_COLUMNS = PRODUCT_COLUMNS + [("exclusion_reason", "제외 사유")]


def build_product_table(columns=PRODUCT_COLUMNS) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels([label for _, label in columns])
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    return table


def populate_product_table(table: QTableWidget, rows, columns=PRODUCT_COLUMNS) -> None:
    table.setRowCount(len(rows))
    for row_idx, row in enumerate(rows):
        row_keys = row.keys()
        for col_idx, (field, _label) in enumerate(columns):
            value = row[field] if field in row_keys else ""
            item = QTableWidgetItem("" if value is None else str(value))
            if col_idx == 0 and "id" in row_keys:
                item.setData(Qt.ItemDataRole.UserRole, row["id"])
            table.setItem(row_idx, col_idx, item)
