from PySide6.QtWidgets import QHBoxLayout, QListWidget, QMainWindow, QStackedWidget, QWidget

from app.ui.discovery_view import DiscoveryView
from app.ui.excluded_view import ExcludedView
from app.ui.product_list_view import ProductListView
from app.ui.s_grade_view import SGradeView
from app.ui.search_count_view import SearchCountView
from app.ui.settings_view import SettingsView

MENU_ITEMS = ["상품발굴", "검색수 확인", "발굴상품", "S등급", "제외상품", "설정"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("쿠팡 해외구매대행 상품 발굴 프로그램")
        self.resize(1100, 700)

        self.nav_list = QListWidget()
        self.nav_list.addItems(MENU_ITEMS)
        self.nav_list.setFixedWidth(160)

        self.discovery_view = DiscoveryView()
        self.search_count_view = SearchCountView()
        self.product_list_view = ProductListView()
        self.s_grade_view = SGradeView()
        self.excluded_view = ExcludedView()
        self.settings_view = SettingsView()

        self.stack = QStackedWidget()
        for view in (
            self.discovery_view,
            self.search_count_view,
            self.product_list_view,
            self.s_grade_view,
            self.excluded_view,
            self.settings_view,
        ):
            self.stack.addWidget(view)

        self.nav_list.currentRowChanged.connect(self.on_nav_changed)
        self.nav_list.setCurrentRow(0)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.nav_list)
        layout.addWidget(self.stack, stretch=1)
        self.setCentralWidget(central)

    def on_nav_changed(self, index: int):
        self.stack.setCurrentIndex(index)
        view = self.stack.widget(index)
        if hasattr(view, "refresh"):
            view.refresh()
