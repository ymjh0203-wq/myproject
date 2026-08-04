from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.db import repository


class SettingsView(QWidget):
    """5. 설정 화면 — 설계서 11장 기본값을 반영, settings 테이블과 저장/조회 연동."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.daily_max_analyze = QSpinBox()
        self.daily_max_analyze.setRange(1, 5000)

        self.api_request_interval_sec = QSpinBox()
        self.api_request_interval_sec.setRange(1, 3600)
        self.api_request_interval_sec.setSuffix(" 초")

        self.recheck_days_normal = QSpinBox()
        self.recheck_days_normal.setRange(1, 365)
        self.recheck_days_normal.setSuffix(" 일")

        self.recheck_days_s_grade = QSpinBox()
        self.recheck_days_s_grade.setRange(1, 365)
        self.recheck_days_s_grade.setSuffix(" 일")

        self.category_max_pages = QSpinBox()
        self.category_max_pages.setRange(1, 100)

        self.wing_check_mode = QComboBox()
        self.wing_check_mode.addItem("수동 보조", "manual")
        self.wing_check_mode.addItem("브라우저 자동화 (옵트인)", "auto")

        self.hyoja_auto_exclude_repeat = QSpinBox()
        self.hyoja_auto_exclude_repeat.setRange(1, 50)
        self.hyoja_auto_exclude_repeat.setSuffix(" 회")

        form = QFormLayout()
        form.addRow("하루 최대 분석 상품 수", self.daily_max_analyze)
        form.addRow("API 요청 간격", self.api_request_interval_sec)
        form.addRow("재검사 주기 (일반 유망상품)", self.recheck_days_normal)
        form.addRow("재검사 주기 (S등급)", self.recheck_days_s_grade)
        form.addRow("카테고리별 최대 탐색 페이지", self.category_max_pages)
        form.addRow("WING 검색수 확인 모드", self.wing_check_mode)
        form.addRow("효자상품 자동 제외 임계치 (동일판매자 반복)", self.hyoja_auto_exclude_repeat)

        save_button = QPushButton("저장")
        save_button.clicked.connect(self.save)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(save_button)
        layout.addStretch()

        self.load()

    def load(self):
        values = repository.get_all_settings()
        self.daily_max_analyze.setValue(int(values.get("daily_max_analyze", 150)))
        self.api_request_interval_sec.setValue(int(values.get("api_request_interval_sec", 450)))
        self.recheck_days_normal.setValue(int(values.get("recheck_days_normal", 30)))
        self.recheck_days_s_grade.setValue(int(values.get("recheck_days_s_grade", 14)))
        self.category_max_pages.setValue(int(values.get("category_max_pages", 5)))
        mode_index = self.wing_check_mode.findData(values.get("wing_check_mode", "manual"))
        self.wing_check_mode.setCurrentIndex(max(mode_index, 0))
        self.hyoja_auto_exclude_repeat.setValue(int(values.get("hyoja_auto_exclude_repeat", 5)))

    def save(self):
        repository.set_settings(
            {
                "daily_max_analyze": str(self.daily_max_analyze.value()),
                "api_request_interval_sec": str(self.api_request_interval_sec.value()),
                "recheck_days_normal": str(self.recheck_days_normal.value()),
                "recheck_days_s_grade": str(self.recheck_days_s_grade.value()),
                "category_max_pages": str(self.category_max_pages.value()),
                "wing_check_mode": self.wing_check_mode.currentData(),
                "hyoja_auto_exclude_repeat": str(self.hyoja_auto_exclude_repeat.value()),
            }
        )
        QMessageBox.information(self, "저장 완료", "설정이 저장되었습니다.")
