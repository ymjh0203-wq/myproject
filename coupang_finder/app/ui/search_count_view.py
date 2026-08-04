from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.analyzers.search_priority import estimate_priority
from app.core.wing.search_count_manual import apply_search_count
from app.db import repository
from app.ui.widgets import build_product_table, populate_product_table

WING_URL = "https://wing.coupang.com"

QUEUE_COLUMNS = [
    ("product_name", "상품명"),
    ("seller_name", "판매자"),
    ("price", "가격"),
    ("brand_risk_level", "브랜드 위험"),
    ("hyoja_status", "효자상품 여부"),
    ("option_quality", "옵션 상태"),
    ("search_rank", "키워드 내 랭킹"),
    ("grade", "이전 등급"),
    ("search_count", "이전 검색수"),
]


class SearchCountView(QWidget):
    """검색수 확인 화면 — WING 카탈로그 매칭 검색수는 공식 API가 없어(확인됨)
    수동 보조 방식이 기본이다. 상품번호를 복사해 WING에서 직접 확인한 뒤,
    그 숫자를 여기 입력하면 등급이 자동으로 매겨진다.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.table = build_product_table(QUEUE_COLUMNS)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)

        self.selected_name_label = QLabel("-")
        self.selected_id_label = QLabel("-")

        self.copy_button = QPushButton("상품번호 복사")
        self.copy_button.clicked.connect(self.copy_product_id)

        self.open_wing_button = QPushButton("WING 열기")
        self.open_wing_button.setToolTip(
            "wing.coupang.com을 브라우저로 엽니다. 로그인 후 "
            "상품관리 > 상품등록 > 카탈로그 매칭하기에서 복사한 번호를 붙여넣어 검색수를 확인해주세요."
        )
        self.open_wing_button.clicked.connect(self.open_wing)

        self.search_count_input = QSpinBox()
        self.search_count_input.setRange(0, 1_000_000)

        self.apply_button = QPushButton("검색수 적용")
        self.apply_button.clicked.connect(self.apply_search_count)

        self._selection_widgets = (
            self.copy_button,
            self.open_wing_button,
            self.search_count_input,
            self.apply_button,
        )
        for widget in self._selection_widgets:
            widget.setEnabled(False)

        detail_box = QGroupBox("선택한 상품")
        detail_layout = QGridLayout(detail_box)
        detail_layout.addWidget(QLabel("상품명"), 0, 0)
        detail_layout.addWidget(self.selected_name_label, 0, 1, 1, 3)
        detail_layout.addWidget(QLabel("상품번호"), 1, 0)
        detail_layout.addWidget(self.selected_id_label, 1, 1)
        detail_layout.addWidget(self.copy_button, 1, 2)
        detail_layout.addWidget(self.open_wing_button, 1, 3)
        detail_layout.addWidget(QLabel("검색수 (0~29 제외 / 30~999 일반 / 1000+ S등급)"), 2, 0, 1, 2)
        detail_layout.addWidget(self.search_count_input, 2, 2)
        detail_layout.addWidget(self.apply_button, 2, 3)

        refresh_button = QPushButton("새로고침")
        refresh_button.clicked.connect(self.refresh)

        top_row = QHBoxLayout()
        top_row.addWidget(
            QLabel(
                "검색수 확인 대기열 (신규 검토중 + 재검사 지난 저장 상품, "
                "키워드 내 랭킹이 높은 상품 순으로 정렬 — 검색수 추정치는 아니며 확인 순서 참고용)"
            )
        )
        top_row.addStretch()
        top_row.addWidget(refresh_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self.table)
        layout.addWidget(detail_box)

        self.current_product_id = None
        self.current_coupang_id = None

        self.refresh()

    def refresh(self):
        rows = repository.list_products_awaiting_check()
        rows = sorted(rows, key=lambda row: estimate_priority(row["search_rank"]), reverse=True)
        populate_product_table(self.table, rows, QUEUE_COLUMNS)
        self.clear_selection_panel()

    def on_selection_changed(self):
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            self.clear_selection_panel()
            return
        item = self.table.item(selected[0].row(), 0)
        db_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        product = repository.get_product(db_id) if db_id is not None else None
        if product is None:
            self.clear_selection_panel()
            return

        self.current_product_id = db_id
        self.current_coupang_id = product["coupang_product_id"]
        self.selected_name_label.setText(product["product_name"])
        self.selected_id_label.setText(str(product["coupang_product_id"]))
        self.search_count_input.setValue(0)
        for widget in self._selection_widgets:
            widget.setEnabled(True)

    def clear_selection_panel(self):
        self.current_product_id = None
        self.current_coupang_id = None
        self.selected_name_label.setText("-")
        self.selected_id_label.setText("-")
        for widget in self._selection_widgets:
            widget.setEnabled(False)

    def copy_product_id(self):
        if not self.current_coupang_id:
            return
        QApplication.clipboard().setText(str(self.current_coupang_id))

    def open_wing(self):
        QDesktopServices.openUrl(QUrl(WING_URL))

    def apply_search_count(self):
        if self.current_product_id is None:
            return
        value = self.search_count_input.value()
        result = apply_search_count(self.current_product_id, value)
        if result["grade"]:
            message = f"검색수 {value} 적용 → {result['status']} ({result['grade']})"
        else:
            message = f"검색수 {value} 적용 → {result['status']} ({result['exclusion_reason']})"
        QMessageBox.information(self, "적용 완료", message)
        self.refresh()
