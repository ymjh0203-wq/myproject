"""쿠팡 상품 일괄등록 GUI와 WING 자동입력 실행 관리."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .batch_jobs import (
    STATUS_AWAITING_CONFIRMATION,
    STATUS_DRAFT_SAVED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_READY,
    STATUS_SKIPPED,
    STATUS_SUCCEEDED,
    BatchJob,
    BatchQueue,
)
from .batch_store import load_batch_queue, save_batch_queue
from .chrome_launcher import ChromeLaunchError, launch_chrome
from .completed_jobs import archive_and_remove_completed
from .excel_import import ExcelImportError, read_registration_workbook
from .detail_translation import (
    DEFAULT_IMAGES_PER_PRODUCT,
    apply_watermark_to_images,
    delete_translation_job,
    estimate_localization_workload,
    import_translation_results,
    prepare_option_images,
    prepare_translation_job,
)
from .registration_plan import (
    DEFAULT_DETAIL_ENTRY_MODE,
    DEFAULT_NOTICE_CATEGORY,
    DEFAULT_NOTICE_DETAIL,
    DEFAULT_SHIPPING_LEAD_DAYS,
    DEFAULT_STOCK_QUANTITY,
    DETAIL_ENTRY_MODE_LABELS,
    build_registration_plan,
)
from .report_store import save_report, strip_url_query
from .settings_store import (
    fixed_page_defaults,
    load_fixed_page_settings,
    load_watermark_settings,
    save_fixed_page_settings,
    save_watermark_settings,
    watermark_defaults,
)
from .url_parser import ParseResult, parse_coupang_url
from .wing_automation import WingAutomationError, launch_controlled_chrome


BASE_DIR = Path(__file__).resolve().parent.parent
REPORT_DIR = BASE_DIR / "data" / "reports"
COMPLETED_REPORT_DIR = REPORT_DIR / "completed_jobs"
BATCH_QUEUE_PATH = BASE_DIR / "data" / "batches" / "current_batch.json"
DEFAULT_FIXED_TOP_PATH = BASE_DIR / "assets" / "fixed_pages" / "livingred_top.png"
DEFAULT_FIXED_BOTTOM_PATH = (
    BASE_DIR / "assets" / "fixed_pages" / "livingred_bottom.png"
)
FIXED_PAGE_SETTINGS_PATH = BASE_DIR / "data" / "settings" / "fixed_pages.json"
WATERMARK_SETTINGS_PATH = BASE_DIR / "data" / "settings" / "watermark.json"
TRANSLATION_JOB_ROOT = BASE_DIR / "data" / "translation_jobs"
WING_PROFILE_DIR = BASE_DIR / "data" / "wing_chrome_profile"
WING_AUTOMATION_LOG = BASE_DIR / "data" / "logs" / "wing_automation.log"
WING_REGISTRATION_URL = "https://wing.coupang.com/tenants/seller-web/vendor-inventory/formV2"
REGISTRATION_EXCEL_TEMPLATE_PATH = (
    BASE_DIR / "assets" / "templates" / "쿠팡_상품등록_양식.xlsx"
)

CHECKS = (
    ("catalog_screen_reached", "WING 상품 등록의 '카탈로그 매칭하기' 화면에 도착함"),
    ("search_id_confirmed", "추출한 ID 중 하나로 검색 결과가 나오는 것을 확인함"),
    ("correct_catalog_verified", "상품명·이미지·카테고리·규격·옵션을 보고 같은 상품임을 확인함"),
    ("catalog_selected", "사용자가 올바른 카탈로그를 직접 선택함"),
    ("registration_form_reached", "상품등록 입력 화면까지 이동함"),
    ("required_fields_recorded", "현재 화면의 필수 입력항목을 아래 메모에 기록함"),
    ("stopped_before_submit", "최종 등록 버튼 직전에서 중단함"),
    ("no_product_registered", "이 검증에서 실제 상품을 등록하지 않았음"),
)


class Phase0Window(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.result: ParseResult | None = None
        self.checkboxes: dict[str, QCheckBox] = {}
        self.batch_load_error = ""
        try:
            self.batch_queue = load_batch_queue(BATCH_QUEUE_PATH)
        except (OSError, ValueError) as exc:
            self.batch_queue = BatchQueue()
            self.batch_load_error = str(exc)
        self.recovered_interrupted_jobs = self.batch_queue.recover_interrupted_jobs()
        self.archived_completed_jobs = 0
        try:
            self.archived_completed_jobs = len(
                archive_and_remove_completed(
                    self.batch_queue,
                    COMPLETED_REPORT_DIR,
                )
            )
        except OSError as exc:
            self.batch_load_error = str(exc)
        if self.recovered_interrupted_jobs or self.archived_completed_jobs:
            try:
                save_batch_queue(self.batch_queue, BATCH_QUEUE_PATH)
            except OSError as exc:
                self.batch_load_error = str(exc)
        self.fixed_page_settings_error = ""
        try:
            fixed_page_settings = load_fixed_page_settings(
                FIXED_PAGE_SETTINGS_PATH,
                default_top=DEFAULT_FIXED_TOP_PATH,
                default_bottom=DEFAULT_FIXED_BOTTOM_PATH,
            )
        except (OSError, ValueError) as exc:
            fixed_page_settings = fixed_page_defaults(
                default_top=DEFAULT_FIXED_TOP_PATH,
                default_bottom=DEFAULT_FIXED_BOTTOM_PATH,
            )
            self.fixed_page_settings_error = str(exc)
        self.fixed_page_top_paths = fixed_page_settings["top_assets"]
        self.fixed_page_bottom_paths = fixed_page_settings["bottom_assets"]
        self.watermark_settings_error = ""
        try:
            self.watermark_settings = load_watermark_settings(
                WATERMARK_SETTINGS_PATH
            )
        except (OSError, ValueError) as exc:
            self.watermark_settings = watermark_defaults()
            self.watermark_settings_error = str(exc)
        self.active_batch_job_id = next(
            (
                job.job_id
                for job in self.batch_queue.jobs
                if job.status in {STATUS_IN_PROGRESS, STATUS_AWAITING_CONFIRMATION}
            ),
            None,
        )
        self.automation_process: subprocess.Popen | None = None
        self.automation_action = ""
        self.confirmation_prompted_job_id: str | None = None
        self._refreshing_batch_selection = False
        self.setWindowTitle("쿠팡 상품 일괄등록 작업 도우미")
        self.resize(1120, 860)
        self._build_ui()
        self.automation_poll_timer = QTimer(self)
        self.automation_poll_timer.setInterval(800)
        self.automation_poll_timer.timeout.connect(self._poll_wing_automation)
        self.automation_poll_timer.start()
        QTimer.singleShot(600, self._initialize_wing_on_startup)

    def _build_ui(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(14)

        title = QLabel("쿠팡 상품 일괄등록 작업 도우미")
        title.setFont(QFont("Malgun Gothic", 16, QFont.Weight.Bold))
        root.addWidget(title)

        warning = QLabel(
            "버튼을 누르면 전용 Chrome에서 카탈로그 매칭, 판매가·재고·배송·인증·"
            "상세페이지 입력을 실제로 진행합니다. 로그인·2차 인증·CAPTCHA는 직접 "
            "진행하세요. 각 행에서 선택한 임시저장/상품등록 방식으로 저장한 뒤 "
            "새 상품등록 화면을 열어 다음 대기 상품을 자동으로 이어서 처리합니다."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "background:#fff3cd; color:#664d03; border:1px solid #ffecb5; "
            "padding:10px; border-radius:5px;"
        )
        root.addWidget(warning)

        root.addWidget(self._browser_startup_group())
        root.addWidget(self._batch_group())
        root.addWidget(self._url_group())
        root.addWidget(self._wing_group())
        root.addWidget(self._shipping_choice_group())
        root.addWidget(self._registration_defaults_group())
        root.addWidget(self._verification_group())
        root.addWidget(self._notes_group())

        self.summary_label = QLabel("아직 검증하지 않았습니다.")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        save_button = QPushButton("검증 결과 저장")
        save_button.setMinimumHeight(42)
        save_button.clicked.connect(self._save)
        root.addWidget(save_button)

        root.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.setCentralWidget(scroll)

        if self.batch_load_error:
            self.statusBar().showMessage(
                f"기존 일괄 작업을 불러오지 못했습니다: {self.batch_load_error}",
                8000,
            )
        elif self.fixed_page_settings_error:
            self.statusBar().showMessage(
                "고정페이지 설정을 불러오지 못해 기본 이미지로 시작했습니다: "
                f"{self.fixed_page_settings_error}",
                8000,
            )
        elif self.watermark_settings_error:
            self.statusBar().showMessage(
                "워터마크 설정을 불러오지 못해 기본값으로 시작했습니다: "
                f"{self.watermark_settings_error}",
                8000,
            )

    def _browser_startup_group(self) -> QGroupBox:
        group = QGroupBox("Chrome/WING 자동 준비")
        layout = QVBoxLayout(group)

        self.browser_status_label = QLabel(
            "프로그램 시작 후 Chrome과 WING 상품등록 화면을 자동으로 준비합니다."
        )
        self.browser_status_label.setWordWrap(True)
        self.browser_status_label.setStyleSheet(
            "background:#e7f1ff; color:#084298; border:1px solid #b6d4fe; "
            "padding:9px; border-radius:5px;"
        )
        layout.addWidget(self.browser_status_label)

        if self.recovered_interrupted_jobs:
            recovery_notice = QLabel(
                f"이전 실행에서 남은 작업 {self.recovered_interrupted_jobs}개를 "
                "'등록 대기' 상태로 자동 복구했습니다. 실제 입력 중이라는 잘못된 표시가 "
                "남지 않습니다."
            )
            recovery_notice.setWordWrap(True)
            recovery_notice.setStyleSheet(
                "background:#d1e7dd; color:#0f5132; border:1px solid #badbcc; "
                "padding:8px; border-radius:5px;"
            )
            layout.addWidget(recovery_notice)

        action_row = QHBoxLayout()
        retry_button = QPushButton("Chrome/WING 다시 준비")
        retry_button.clicked.connect(
            lambda: self._open_wing_for_session(show_dialog=True)
        )
        action_row.addWidget(retry_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        return group

    def _set_browser_status(self, text: str, *, success: bool) -> None:
        self.browser_status_label.setText(text)
        if success:
            self.browser_status_label.setStyleSheet(
                "background:#d1e7dd; color:#0f5132; border:1px solid #badbcc; "
                "padding:9px; border-radius:5px;"
            )
        else:
            self.browser_status_label.setStyleSheet(
                "background:#f8d7da; color:#842029; border:1px solid #f5c2c7; "
                "padding:9px; border-radius:5px;"
            )

    def _initialize_wing_on_startup(self) -> None:
        self._open_wing_for_session(show_dialog=False)

    def _open_wing_for_session(self, *, show_dialog: bool) -> bool:
        try:
            launch_controlled_chrome(
                profile_dir=WING_PROFILE_DIR,
                focus_existing=True,
            )
        except (WingAutomationError, OSError) as exc:
            self._set_browser_status(
                "Chrome/WING 자동 준비 실패 — 상품 상태는 등록 대기로 유지했습니다. "
                f"{exc}",
                success=False,
            )
            if show_dialog:
                QMessageBox.warning(self, "Chrome/WING 준비 실패", str(exc))
            return False

        self._set_browser_status(
            "자동입력 전용 Chrome의 WING 탭을 화면 앞으로 열었습니다. 처음 한 번만 이 창에서 "
            "WING에 로그인하면 다음 실행부터 로그인 상태를 그대로 사용합니다.",
            success=True,
        )
        if show_dialog:
            self.statusBar().showMessage("Chrome/WING 화면을 다시 준비했습니다.", 4000)
        return True

    def _batch_group(self) -> QGroupBox:
        group = QGroupBox("0. 여러 상품 일괄등록 대기열")
        layout = QVBoxLayout(group)

        guide = QLabel(
            "쿠팡 상품 URL을 한 줄에 하나씩 넣으세요. 각 상품의 배송비와 WING 처리 방식 "
            "(이미지 편집용 임시저장/바로 상품등록)을 선택하면 등록 대기 상태가 됩니다. "
            "맨 앞 체크박스가 선택된 상품만 자동등록하며, 해제한 상품은 목록에 그대로 "
            "보관됩니다. 작업 목록은 자동 저장됩니다."
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)

        layout.addWidget(self._fixed_page_quick_group())
        layout.addWidget(self._translation_quick_group())

        self.batch_url_input = QTextEdit()
        self.batch_url_input.setPlaceholderText(
            "https://www.coupang.com/vp/products/...\n"
            "https://www.coupang.com/vp/products/..."
        )
        self.batch_url_input.setMaximumHeight(100)
        layout.addWidget(self.batch_url_input)

        add_row = QHBoxLayout()
        add_button = QPushButton("URL 목록에 추가")
        add_button.clicked.connect(self._add_batch_urls)
        save_excel_template_button = QPushButton("엑셀 양식 저장")
        save_excel_template_button.setToolTip(
            "상품 URL과 등록 방식을 입력할 수 있는 엑셀 양식을 원하는 곳에 저장합니다."
        )
        save_excel_template_button.clicked.connect(self._save_excel_template_copy)
        import_excel_button = QPushButton("엑셀 파일 불러오기")
        import_excel_button.setToolTip(
            "작성한 엑셀 양식의 선택 상품을 현재 자동등록 대기열에 넣습니다."
        )
        import_excel_button.clicked.connect(self._import_batch_excel)
        self.select_all_batch_checkbox = QCheckBox("전체 상품 선택/해제")
        self.select_all_batch_checkbox.setTristate(True)
        self.select_all_batch_checkbox.setToolTip(
            "체크하면 전체 상품을 자동등록 대상으로 선택하고, 다시 누르면 전체 해제합니다."
        )
        self.select_all_batch_checkbox.clicked.connect(
            self._toggle_all_batch_selection
        )
        all_free_button = QPushButton("대기 상품 전체 무료배송")
        all_free_button.clicked.connect(
            lambda: self._set_all_batch_shipping("free")
        )
        all_cod_button = QPushButton("대기 상품 전체 착불배송")
        all_cod_button.clicked.connect(
            lambda: self._set_all_batch_shipping("cash_on_delivery")
        )
        save_batch_button = QPushButton("작업 목록 저장")
        save_batch_button.clicked.connect(self._save_batch_queue)
        reload_batch_button = QPushButton("저장 목록 다시 불러오기")
        reload_batch_button.clicked.connect(self._reload_batch_queue)
        for button in (
            add_button,
            save_excel_template_button,
            import_excel_button,
            self.select_all_batch_checkbox,
            all_free_button,
            all_cod_button,
            save_batch_button,
            reload_batch_button,
        ):
            add_row.addWidget(button)
        add_row.addStretch(1)
        layout.addLayout(add_row)

        columns = (
            "등록 선택",
            "상태",
            "상품 ID",
            "상품 URL",
            "배송비 선택",
            "WING 처리 방식",
            "상세 이미지",
            "번역 상태",
            "매칭 판매가",
            "반품배송비",
            "원산지",
            "시도",
            "등록상품 ID",
            "결과/오류",
        )
        self.batch_table = QTableWidget(0, len(columns))
        self.batch_table.setHorizontalHeaderLabels(columns)
        self.batch_table.setMinimumHeight(300)
        self.batch_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.batch_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.batch_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        header = self.batch_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(13, QHeaderView.ResizeMode.Stretch)
        self.batch_table.currentCellChanged.connect(
            lambda *_args: self._refresh_translation_previews()
        )
        layout.addWidget(self.batch_table)

        action_row = QHBoxLayout()
        self.open_next_button = QPushButton("다음 상품 자동입력 시작")
        self.open_next_button.setMinimumHeight(42)
        self.open_next_button.clicked.connect(self._open_next_batch_job)
        self.stop_automation_button = QPushButton("프로그램 중지")
        self.stop_automation_button.setMinimumHeight(42)
        self.stop_automation_button.setEnabled(False)
        self.stop_automation_button.setToolTip(
            "현재 자동입력만 중지합니다. 중지한 상품은 실패 상품 재시도로 다시 시작할 수 있습니다."
        )
        self.stop_automation_button.setStyleSheet(
            "QPushButton:enabled { background:#dc3545; color:white; font-weight:bold; "
            "border:1px solid #bb2d3b; border-radius:4px; padding:6px 12px; }"
        )
        self.stop_automation_button.clicked.connect(self._stop_wing_automation)
        success_button = QPushButton("선택 상품 최종 실행")
        success_button.clicked.connect(self._mark_batch_succeeded)
        rerun_button = QPushButton("선택 상품 다시 등록")
        rerun_button.clicked.connect(self._requeue_completed_batch_job)
        failed_button = QPushButton("선택 상품 실패 처리")
        failed_button.clicked.connect(self._mark_batch_failed)
        retry_button = QPushButton("실패 상품 재시도")
        retry_button.clicked.connect(self._retry_failed_batch_jobs)
        skip_button = QPushButton("체크 상품 삭제")
        skip_button.setToolTip("맨 앞 체크박스가 선택된 상품을 작업 목록에서 삭제합니다.")
        skip_button.clicked.connect(self._skip_batch_job)
        for button in (
            self.open_next_button,
            self.stop_automation_button,
            success_button,
            rerun_button,
            failed_button,
            retry_button,
            skip_button,
        ):
            action_row.addWidget(button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        layout.addWidget(self._watermark_quick_group())

        self.batch_summary_label = QLabel()
        self.batch_summary_label.setWordWrap(True)
        layout.addWidget(self.batch_summary_label)
        self._refresh_batch_table()
        return group

    def _translation_quick_group(self) -> QGroupBox:
        group = QGroupBox("상세페이지 문구 자동 확인 → 자연스러운 한국어 이미지")
        layout = QVBoxLayout(group)
        description = QLabel(
            "표에서 상품을 선택한 뒤 상세 원본 이미지를 넣으세요. 중국어는 자연스러운 "
            "한국어로 번역하고, 어색한 한국어는 뜻을 유지해 교정합니다. 이미 자연스러운 "
            "한국어와 글자 없는 이미지는 그대로 두며, 결과 검수 승인 전에는 실제 상품등록을 "
            "진행하지 않습니다."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        previews = QHBoxLayout()
        source_box = QGroupBox("원본 첫 이미지")
        source_layout = QVBoxLayout(source_box)
        self.translation_source_preview = QLabel("선택 상품의 원본 이미지 없음")
        self._prepare_fixed_page_preview(self.translation_source_preview)
        source_layout.addWidget(self.translation_source_preview)
        previews.addWidget(source_box)
        result_box = QGroupBox("한국어 교정·번역 첫 이미지")
        result_layout = QVBoxLayout(result_box)
        self.translation_result_preview = QLabel("한국어 결과 이미지 없음")
        self._prepare_fixed_page_preview(self.translation_result_preview)
        result_layout.addWidget(self.translation_result_preview)
        previews.addWidget(result_box)
        layout.addLayout(previews)

        action_row = QHBoxLayout()
        source_button = QPushButton("선택 상품 상세 이미지 넣기")
        source_button.clicked.connect(self._prepare_selected_translation_job)
        copy_request_button = QPushButton("Codex 한국어 이미지 요청 복사")
        copy_request_button.clicked.connect(self._copy_translation_request)
        open_folder_button = QPushButton("번역 작업 폴더 열기")
        open_folder_button.clicked.connect(self._open_translation_folder)
        import_button = QPushButton("한국어 결과 불러오기")
        import_button.clicked.connect(self._import_translation_results)
        approve_button = QPushButton("한국어 결과 검수 승인")
        approve_button.clicked.connect(self._approve_translation_results)
        for button in (
            source_button,
            copy_request_button,
            open_folder_button,
            import_button,
            approve_button,
        ):
            action_row.addWidget(button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        workload_row = QHBoxLayout()
        workload_row.addWidget(QLabel("오늘 작업 예정 상품"))
        self.daily_localization_product_count = QSpinBox()
        self.daily_localization_product_count.setRange(1, 100)
        self.daily_localization_product_count.setValue(10)
        self.daily_localization_product_count.setSuffix("개")
        workload_row.addWidget(self.daily_localization_product_count)
        workload_row.addWidget(QLabel("상품당 평균 이미지"))
        self.daily_images_per_product = QSpinBox()
        self.daily_images_per_product.setRange(1, 30)
        self.daily_images_per_product.setValue(DEFAULT_IMAGES_PER_PRODUCT)
        self.daily_images_per_product.setSuffix("장")
        workload_row.addWidget(self.daily_images_per_product)
        self.localization_workload_label = QLabel()
        self.localization_workload_label.setWordWrap(True)
        workload_row.addWidget(self.localization_workload_label, 1)
        layout.addLayout(workload_row)
        self.daily_localization_product_count.valueChanged.connect(
            self._update_localization_workload_estimate
        )
        self.daily_images_per_product.valueChanged.connect(
            self._update_localization_workload_estimate
        )
        self._update_localization_workload_estimate()

        self.translation_help_label = QLabel(
            "한국어 이미지 생성은 Codex의 이미지 편집 기능을 사용합니다. 요청 복사 후 현재 "
            "Codex 대화에 붙여넣으면 상품별 작업 폴더의 원본을 처리할 수 있습니다."
        )
        self.translation_help_label.setWordWrap(True)
        self.translation_help_label.setStyleSheet("color:#555;")
        layout.addWidget(self.translation_help_label)
        return group

    def _watermark_quick_group(self) -> QGroupBox:
        group = QGroupBox("내 워터마크 넣기")
        layout = QVBoxLayout(group)
        guide = QLabel(
            "옵션 이미지와 한국어 번역·교정이 끝난 상세이미지 복사본에 적용합니다. "
            "쿠팡 메인 대표이미지는 변경하지 않습니다. 이미지와 문구를 모두 "
            "입력하면 이미지 워터마크를 우선 사용합니다."
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)

        first_row = QHBoxLayout()
        self.watermark_enabled = QCheckBox("옵션·상세이미지에 워터마크 사용")
        self.watermark_enabled.setChecked(self.watermark_settings["enabled"])
        first_row.addWidget(self.watermark_enabled)
        self.watermark_image_path = QLineEdit(self.watermark_settings["image_path"])
        self.watermark_image_path.setReadOnly(True)
        self.watermark_image_path.setPlaceholderText("워터마크 PNG/JPG 이미지 선택")
        first_row.addWidget(self.watermark_image_path, 1)
        select_button = QPushButton("이미지 선택")
        select_button.clicked.connect(self._select_watermark_image)
        clear_button = QPushButton("이미지 지우기")
        clear_button.clicked.connect(self._clear_watermark_image)
        first_row.addWidget(select_button)
        first_row.addWidget(clear_button)
        layout.addLayout(first_row)

        second_row = QHBoxLayout()
        second_row.addWidget(QLabel("워터마크 문구"))
        self.watermark_text = QLineEdit(self.watermark_settings["text"])
        self.watermark_text.setPlaceholderText("예: 리빙레드")
        second_row.addWidget(self.watermark_text, 1)
        second_row.addWidget(QLabel("위치"))
        self.watermark_position = QComboBox()
        for label, value in (
            ("오른쪽 아래", "bottom_right"),
            ("왼쪽 아래", "bottom_left"),
            ("가운데", "center"),
            ("오른쪽 위", "top_right"),
            ("왼쪽 위", "top_left"),
        ):
            self.watermark_position.addItem(label, value)
        current_position = self.watermark_position.findData(
            self.watermark_settings["position"]
        )
        self.watermark_position.setCurrentIndex(max(0, current_position))
        second_row.addWidget(self.watermark_position)
        second_row.addWidget(QLabel("방향"))
        self.watermark_angle = QComboBox()
        self.watermark_angle.addItem("대각선 ↗", 30)
        self.watermark_angle.addItem("대각선 ↘", -30)
        self.watermark_angle.addItem("가로", 0)
        current_angle = self.watermark_angle.findData(
            self.watermark_settings["angle_degrees"]
        )
        self.watermark_angle.setCurrentIndex(max(0, current_angle))
        second_row.addWidget(self.watermark_angle)
        second_row.addWidget(QLabel("투명도"))
        self.watermark_opacity = QSpinBox()
        self.watermark_opacity.setRange(5, 100)
        self.watermark_opacity.setSuffix("%")
        self.watermark_opacity.setValue(self.watermark_settings["opacity_percent"])
        second_row.addWidget(self.watermark_opacity)
        second_row.addWidget(QLabel("크기"))
        self.watermark_size = QSpinBox()
        self.watermark_size.setRange(5, 50)
        self.watermark_size.setSuffix("%")
        self.watermark_size.setValue(self.watermark_settings["size_percent"])
        second_row.addWidget(self.watermark_size)
        apply_button = QPushButton("선택 상품 옵션·상세에 다시 적용")
        apply_button.clicked.connect(self._apply_watermark_to_selected_results)
        second_row.addWidget(apply_button)
        layout.addLayout(second_row)

        option_row = QHBoxLayout()
        option_button = QPushButton("선택 상품 옵션 이미지 넣기")
        option_button.clicked.connect(self._prepare_selected_option_images)
        option_row.addWidget(option_button)
        self.option_watermark_status = QLabel("선택 상품의 옵션 이미지 없음")
        self.option_watermark_status.setWordWrap(True)
        option_row.addWidget(self.option_watermark_status, 1)
        layout.addLayout(option_row)

        self.watermark_enabled.toggled.connect(self._save_watermark_controls)
        self.watermark_text.editingFinished.connect(self._save_watermark_controls)
        self.watermark_position.currentIndexChanged.connect(
            self._save_watermark_controls
        )
        self.watermark_angle.currentIndexChanged.connect(
            self._save_watermark_controls
        )
        self.watermark_opacity.valueChanged.connect(self._save_watermark_controls)
        self.watermark_size.valueChanged.connect(self._save_watermark_controls)
        return group

    def _current_watermark_settings(self) -> dict:
        return {
            "enabled": self.watermark_enabled.isChecked(),
            "image_path": self.watermark_image_path.text().strip(),
            "text": self.watermark_text.text().strip(),
            "position": self.watermark_position.currentData() or "bottom_right",
            "opacity_percent": self.watermark_opacity.value(),
            "size_percent": self.watermark_size.value(),
            "angle_degrees": self.watermark_angle.currentData() or 0,
        }

    def _save_watermark_controls(self, *_args) -> bool:
        settings = self._current_watermark_settings()
        try:
            save_watermark_settings(WATERMARK_SETTINGS_PATH, **settings)
        except OSError as exc:
            QMessageBox.warning(self, "워터마크 설정 저장 실패", str(exc))
            return False
        self.watermark_settings = settings
        return True

    def _select_watermark_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "워터마크 이미지 선택",
            str(BASE_DIR),
            "이미지 파일 (*.png *.jpg *.jpeg *.webp)",
        )
        if not path:
            return
        self.watermark_image_path.setText(path)
        self.watermark_enabled.setChecked(True)
        if self._save_watermark_controls():
            self.statusBar().showMessage("워터마크 이미지를 저장했습니다.", 5000)

    def _clear_watermark_image(self) -> None:
        self.watermark_image_path.clear()
        if self._save_watermark_controls():
            self.statusBar().showMessage(
                "워터마크 이미지를 지웠습니다. 문구가 있으면 문구를 사용합니다.", 5000
            )

    def _update_localization_workload_estimate(self, *_args) -> None:
        if not hasattr(self, "localization_workload_label"):
            return
        estimate = estimate_localization_workload(
            self.daily_localization_product_count.value(),
            self.daily_images_per_product.value(),
        )
        self.localization_workload_label.setText(
            f"기본 {estimate['base_images']}장 · 재작업 20% 포함 약 "
            f"{estimate['estimated_total_images']}장 (하루 작업 예정량 계산)"
        )

    def _fixed_page_quick_group(self) -> QGroupBox:
        group = QGroupBox("고정페이지 이미지 삽입")
        layout = QVBoxLayout(group)

        description = QLabel(
            "상단·하단 이미지는 언제든지 바꿀 수 있습니다. 여러 장을 선택하면 선택한 "
            "순서대로 삽입되며 이후 등록 상품부터 적용됩니다."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        cards = QHBoxLayout()
        top_card = QGroupBox("상단 고정페이지")
        top_layout = QVBoxLayout(top_card)
        self.quick_fixed_top_preview = QLabel("상단 이미지 없음")
        self._prepare_fixed_page_preview(self.quick_fixed_top_preview)
        self.quick_fixed_top_path = QLineEdit()
        self.quick_fixed_top_path.setReadOnly(True)
        top_button = QPushButton("상단 이미지 삽입/변경")
        top_button.setMinimumHeight(38)
        top_button.clicked.connect(self._select_fixed_page_top_images)
        top_layout.addWidget(self.quick_fixed_top_preview)
        top_layout.addWidget(self.quick_fixed_top_path)
        top_layout.addWidget(top_button)
        cards.addWidget(top_card)

        bottom_card = QGroupBox("하단 고정페이지")
        bottom_layout = QVBoxLayout(bottom_card)
        self.quick_fixed_bottom_preview = QLabel("하단 이미지 없음")
        self._prepare_fixed_page_preview(self.quick_fixed_bottom_preview)
        self.quick_fixed_bottom_path = QLineEdit()
        self.quick_fixed_bottom_path.setReadOnly(True)
        bottom_button = QPushButton("하단 이미지 삽입/변경")
        bottom_button.setMinimumHeight(38)
        bottom_button.clicked.connect(self._select_fixed_page_bottom_images)
        bottom_layout.addWidget(self.quick_fixed_bottom_preview)
        bottom_layout.addWidget(self.quick_fixed_bottom_path)
        bottom_layout.addWidget(bottom_button)
        cards.addWidget(bottom_card)
        layout.addLayout(cards)

        reset_button = QPushButton("리빙레드 기본 상·하단 이미지로 되돌리기")
        reset_button.clicked.connect(self._reset_fixed_page_images)
        layout.addWidget(reset_button)
        self._sync_fixed_page_controls()
        return group

    @staticmethod
    def _prepare_fixed_page_preview(label: QLabel) -> None:
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumHeight(150)
        label.setStyleSheet(
            "background:#f8f9fa; border:1px solid #ced4da; border-radius:5px; "
            "color:#6c757d;"
        )

    def _sync_fixed_page_controls(self) -> None:
        controls = (
            (
                self.fixed_page_top_paths,
                getattr(self, "quick_fixed_top_path", None),
                getattr(self, "quick_fixed_top_preview", None),
                "상단 이미지 없음",
            ),
            (
                self.fixed_page_bottom_paths,
                getattr(self, "quick_fixed_bottom_path", None),
                getattr(self, "quick_fixed_bottom_preview", None),
                "하단 이미지 없음",
            ),
        )
        for paths, path_field, preview, empty_text in controls:
            if path_field is not None:
                display = paths[0] if len(paths) <= 1 else f"{paths[0]} 외 {len(paths) - 1}장"
                path_field.setText(display if paths else "")
                path_field.setToolTip("\n".join(paths))
            if preview is not None:
                preview.clear()
                image_path = Path(paths[0]) if paths else None
                pixmap = QPixmap(str(image_path)) if image_path else QPixmap()
                if pixmap.isNull():
                    preview.setText(empty_text if not paths else "이미지를 불러올 수 없습니다")
                else:
                    preview.setPixmap(
                        pixmap.scaled(
                            440,
                            150,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )

        if hasattr(self, "fixed_page_top_files"):
            self.fixed_page_top_files.setPlainText(
                "\n".join(self.fixed_page_top_paths)
            )
        if hasattr(self, "fixed_page_bottom_files"):
            self.fixed_page_bottom_files.setPlainText(
                "\n".join(self.fixed_page_bottom_paths)
            )

    def _save_fixed_page_settings(self) -> bool:
        try:
            save_fixed_page_settings(
                FIXED_PAGE_SETTINGS_PATH,
                top_assets=self.fixed_page_top_paths,
                bottom_assets=self.fixed_page_bottom_paths,
            )
            return True
        except OSError as exc:
            QMessageBox.warning(self, "고정페이지 저장 실패", str(exc))
            return False

    def _reset_fixed_page_images(self) -> None:
        defaults = fixed_page_defaults(
            default_top=DEFAULT_FIXED_TOP_PATH,
            default_bottom=DEFAULT_FIXED_BOTTOM_PATH,
        )
        self.fixed_page_top_paths = defaults["top_assets"]
        self.fixed_page_bottom_paths = defaults["bottom_assets"]
        self._sync_fixed_page_controls()
        if self._save_fixed_page_settings():
            self.statusBar().showMessage(
                "리빙레드 기본 상·하단 이미지로 되돌렸습니다.", 5000
            )

    def _url_group(self) -> QGroupBox:
        group = QGroupBox("1. 상품 URL 분석")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "예: https://www.coupang.com/vp/products/123...?itemId=...&vendorItemId=..."
        )
        analyze_button = QPushButton("URL 분석")
        analyze_button.clicked.connect(self._analyze)
        row.addWidget(self.url_input, 1)
        row.addWidget(analyze_button)
        layout.addLayout(row)

        self.id_table = QTableWidget(0, 4)
        self.id_table.setHorizontalHeaderLabels(("종류", "값", "URL 위치", "확인된 의미"))
        self.id_table.setMinimumHeight(155)
        self.id_table.horizontalHeader().setStretchLastSection(True)
        self.id_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.id_table)

        self.parse_message = QLabel("URL을 입력하고 분석을 누르세요.")
        self.parse_message.setWordWrap(True)
        layout.addWidget(self.parse_message)
        return group

    def _wing_group(self) -> QGroupBox:
        group = QGroupBox("2. WING 상품 등록에서 반자동으로 확인")
        layout = QVBoxLayout(group)

        wing_row = QHBoxLayout()
        self.wing_url_input = QLineEdit()
        self.wing_url_input.setPlaceholderText(
            "선택 사항: 직접 확인한 상품 등록 화면 주소 (비우면 WING 상품 등록 화면)"
        )
        open_button = QPushButton("WING 열기")
        open_button.clicked.connect(self._open_wing)
        wing_row.addWidget(self.wing_url_input, 1)
        wing_row.addWidget(open_button)
        layout.addLayout(wing_row)

        candidate_row = QHBoxLayout()
        candidate_row.addWidget(QLabel("시험할 검색값:"))
        self.candidate_combo = QComboBox()
        self.candidate_combo.setMinimumWidth(360)
        copy_button = QPushButton("검색값 복사")
        copy_button.clicked.connect(self._copy_candidate)
        candidate_row.addWidget(self.candidate_combo)
        candidate_row.addWidget(copy_button)
        candidate_row.addStretch(1)
        layout.addLayout(candidate_row)

        help_text = QLabel(
            "실제 검증에서 productId 검색이 성공했습니다. 상품 URL도 검색창에서 지원하며, "
            "vendorItemId와 itemId는 아직 확인되지 않은 대체 후보입니다."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color:#555;")
        layout.addWidget(help_text)
        return group

    def _verification_group(self) -> QGroupBox:
        group = QGroupBox("5. 사람이 확인할 체크포인트")
        layout = QVBoxLayout(group)
        for key, label in CHECKS:
            checkbox = QCheckBox(label)
            checkbox.stateChanged.connect(self._refresh_summary)
            self.checkboxes[key] = checkbox
            layout.addWidget(checkbox)
        return group

    def _shipping_choice_group(self) -> QGroupBox:
        group = QGroupBox("3. 사용자가 상품마다 선택 — 배송비")
        layout = QHBoxLayout(group)
        self.shipping_fee_group = QButtonGroup(self)
        self.shipping_fee_group.setExclusive(True)
        self.free_shipping_button = QRadioButton("무료배송")
        self.cod_shipping_button = QRadioButton("착불배송")
        button_style = """
            QRadioButton {
                border: 2px solid #adb5bd;
                border-radius: 8px;
                padding: 14px 28px;
                font-size: 15px;
                font-weight: bold;
                background: white;
            }
            QRadioButton:checked {
                border-color: #0d6efd;
                color: #0d6efd;
                background: #e7f1ff;
            }
        """
        for button in (self.free_shipping_button, self.cod_shipping_button):
            button.setMinimumHeight(52)
            button.setStyleSheet(button_style)
            self.shipping_fee_group.addButton(button)
            button.toggled.connect(self._refresh_summary)
            layout.addWidget(button)
        layout.addStretch(1)
        note = QLabel("이 항목만 상품마다 직접 선택합니다. 나머지는 등록 규칙으로 자동 계산합니다.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#555;")
        layout.addWidget(note, 1)
        return group

    def _registration_defaults_group(self) -> QGroupBox:
        group = QGroupBox("4. 자동 적용되는 상품 등록값")
        form = QFormLayout(group)

        self.catalog_match_price = QSpinBox()
        self.catalog_match_price.setRange(0, 1_000_000_000)
        self.catalog_match_price.setSingleStep(100)
        self.catalog_match_price.setSuffix(" 원")
        self.catalog_match_price.setSpecialValueText("WING 매칭가 확인 필요")
        form.addRow("판매가 (매칭가와 동일)", self.catalog_match_price)

        self.stock_quantity = QSpinBox()
        self.stock_quantity.setRange(0, 999_999)
        self.stock_quantity.setValue(DEFAULT_STOCK_QUANTITY)
        form.addRow("재고수량", self.stock_quantity)

        self.shipping_lead_days = QSpinBox()
        self.shipping_lead_days.setRange(1, 20)
        self.shipping_lead_days.setValue(DEFAULT_SHIPPING_LEAD_DAYS)
        self.shipping_lead_days.setSuffix(" 일")
        form.addRow("출고 소요일", self.shipping_lead_days)

        self.country_of_origin = QLineEdit()
        self.country_of_origin.setPlaceholderText("상품별 실제 제조국 입력 (예: 중국)")
        form.addRow("원산지", self.country_of_origin)

        self.notice_category = QLabel(DEFAULT_NOTICE_CATEGORY)
        form.addRow("상품정보제공고시 품목", self.notice_category)

        self.notice_detail = QLineEdit(DEFAULT_NOTICE_DETAIL)
        form.addRow("상품정보제공고시 내용", self.notice_detail)

        self.kc_purchase_agent = QCheckBox("구매대행 상품으로 진행")
        self.kc_purchase_agent.setChecked(True)
        form.addRow("KC 인증", self.kc_purchase_agent)

        self.detail_entry_mode = QLabel(
            DETAIL_ENTRY_MODE_LABELS[DEFAULT_DETAIL_ENTRY_MODE]
        )
        self.detail_entry_mode.setStyleSheet("font-weight:bold; color:#0f5132;")
        form.addRow("상세페이지 입력 방식", self.detail_entry_mode)

        self.remove_other_seller_sections = QCheckBox(
            "판매자 상호·연락처·QR·자체 배송/교환 안내를 자동 제거 후보로 분류"
        )
        self.remove_other_seller_sections.setChecked(True)
        form.addRow("다른 판매자 고정영역", self.remove_other_seller_sections)

        self.own_seller_markers = QLineEdit("미노바")
        self.own_seller_markers.setPlaceholderText(
            "내 상호·스토어명 (여러 개면 쉼표로 구분)"
        )
        form.addRow("내 판매자 식별어", self.own_seller_markers)

        self.fixed_page_top_files = QTextEdit()
        self.fixed_page_top_files.setReadOnly(True)
        self.fixed_page_top_files.setPlaceholderText("상단 고정페이지 이미지")
        self.fixed_page_top_files.setMaximumHeight(70)
        self.fixed_page_top_files.setPlainText("\n".join(self.fixed_page_top_paths))
        select_top_button = QPushButton("상단 이미지 삽입/변경")
        select_top_button.clicked.connect(self._select_fixed_page_top_images)
        top_layout = QVBoxLayout()
        top_layout.addWidget(self.fixed_page_top_files)
        top_layout.addWidget(select_top_button)
        form.addRow("상단 고정페이지", top_layout)

        self.fixed_page_bottom_files = QTextEdit()
        self.fixed_page_bottom_files.setReadOnly(True)
        self.fixed_page_bottom_files.setPlaceholderText("하단 고정페이지 이미지")
        self.fixed_page_bottom_files.setMaximumHeight(70)
        self.fixed_page_bottom_files.setPlainText(
            "\n".join(self.fixed_page_bottom_paths)
        )
        select_bottom_button = QPushButton("하단 이미지 삽입/변경")
        select_bottom_button.clicked.connect(self._select_fixed_page_bottom_images)
        bottom_layout = QVBoxLayout()
        bottom_layout.addWidget(self.fixed_page_bottom_files)
        bottom_layout.addWidget(select_bottom_button)
        form.addRow("하단 고정페이지", bottom_layout)

        editor_order = QLabel("상단 → 정제된 상품 상세 → 하단")
        editor_order.setStyleSheet("font-weight:bold;")
        form.addRow("에디터 이미지 순서", editor_order)

        self.detail_preview_required = QCheckBox(
            "WING 에디터 입력 전에 삭제 후보와 최종 이미지 순서를 확인"
        )
        self.detail_preview_required.setChecked(True)
        form.addRow("상세페이지 미리보기", self.detail_preview_required)

        origin_warning = QLabel(
            "원산지는 '상세페이지 참조'로 대체하지 않고 상품별 실제 제조국을 확인해 입력합니다. "
            "최종 상품등록은 사용자가 WING에서 직접 확인합니다."
        )
        origin_warning.setWordWrap(True)
        origin_warning.setStyleSheet("color:#664d03;")
        form.addRow("", origin_warning)

        self.catalog_match_price.valueChanged.connect(self._refresh_summary)
        self.stock_quantity.valueChanged.connect(self._refresh_summary)
        self.shipping_lead_days.valueChanged.connect(self._refresh_summary)
        self.country_of_origin.textChanged.connect(self._refresh_summary)
        self.notice_detail.textChanged.connect(self._refresh_summary)
        self.kc_purchase_agent.stateChanged.connect(self._refresh_summary)
        self.remove_other_seller_sections.stateChanged.connect(self._refresh_summary)
        self.own_seller_markers.textChanged.connect(self._refresh_summary)
        self.fixed_page_top_files.textChanged.connect(self._refresh_summary)
        self.fixed_page_bottom_files.textChanged.connect(self._refresh_summary)
        self.detail_preview_required.stateChanged.connect(self._refresh_summary)
        return group

    def _notes_group(self) -> QGroupBox:
        group = QGroupBox("6. 실제 WING 관찰 기록")
        form = QFormLayout(group)
        self.used_id_combo = QComboBox()
        self.used_id_combo.addItems(
            ("미확인", "productId", "상품 URL", "vendorItemId", "itemId", "상품명/기타")
        )
        form.addRow("검색에 성공한 값 종류", self.used_id_combo)

        self.catalog_view_count = QLineEdit()
        self.catalog_view_count.setPlaceholderText("예: 544 (최근 28일)")
        form.addRow("카탈로그 조회수", self.catalog_view_count)

        self.catalog_rating_display = QLineEdit()
        self.catalog_rating_display.setPlaceholderText("예: 별 아이콘 4개, (4)")
        form.addRow("상품별점 표시", self.catalog_rating_display)

        self.catalog_delivery_type = QLineEdit()
        self.catalog_delivery_type.setPlaceholderText("예: 해외배송")
        form.addRow("배송정보 표시", self.catalog_delivery_type)

        self.catalog_notes = QTextEdit()
        self.catalog_notes.setPlaceholderText(
            "후보 개수, 상품명, 이미지, 카테고리, 모델/규격, 옵션, 구성수량 비교 결과"
        )
        self.catalog_notes.setMaximumHeight(100)
        form.addRow("카탈로그 비교 메모", self.catalog_notes)

        self.required_fields = QTextEdit()
        self.required_fields.setPlaceholderText(
            "등록 화면에 실제 표시된 필수 항목만 기록 (예: 판매가격, 배송정보...)"
        )
        self.required_fields.setMaximumHeight(100)
        form.addRow("등록폼 필수 항목", self.required_fields)

        self.failure_mode = QComboBox()
        self.failure_mode.addItems(("실패 없음/검증 중", "검색 결과 없음", "올바른 카탈로그 없음", "로그인·권한 차단", "CAPTCHA·2차 인증 대기", "WING 화면 변경", "기타 실패"))
        form.addRow("실패·차단 종류", self.failure_mode)

        self.failure_notes = QTextEdit()
        self.failure_notes.setPlaceholderText(
            "막힌 화면과 표시된 안내를 적으세요. 비밀번호·쿠키·인증토큰은 입력하지 마세요."
        )
        self.failure_notes.setMaximumHeight(85)
        form.addRow("실패 메모", self.failure_notes)
        return group

    @staticmethod
    def _money_text(value: int | None) -> str:
        return f"{value:,}원" if value is not None else "WING 확인 후 자동 계산"

    def _refresh_batch_table(self, selected_job_id: str | None = None) -> None:
        if selected_job_id is None and hasattr(self, "batch_table"):
            selected_job_id = self._selected_batch_job_id()

        self.batch_table.setRowCount(len(self.batch_queue.jobs))
        selected_row = -1
        self._refreshing_batch_selection = True
        for row, job in enumerate(self.batch_queue.jobs):
            selection_widget = QWidget()
            selection_layout = QHBoxLayout(selection_widget)
            selection_layout.setContentsMargins(2, 2, 2, 2)
            selection_checkbox = QCheckBox()
            selection_checkbox.setToolTip(
                "체크: 자동등록 대상 / 해제: 목록은 유지하고 자동등록에서 제외"
            )
            selection_checkbox.setChecked(job.is_selected)
            selection_checkbox.setEnabled(
                job.status
                not in {
                    STATUS_DRAFT_SAVED,
                    STATUS_SUCCEEDED,
                    STATUS_SKIPPED,
                    STATUS_IN_PROGRESS,
                    STATUS_AWAITING_CONFIRMATION,
                }
            )
            selection_checkbox.toggled.connect(
                lambda checked, job_id=job.job_id: self._on_batch_selection_changed(
                    job_id, checked
                )
            )
            selection_layout.addStretch(1)
            selection_layout.addWidget(selection_checkbox)
            selection_layout.addStretch(1)
            self.batch_table.setCellWidget(row, 0, selection_widget)

            status_item = QTableWidgetItem(job.status_label)
            status_item.setData(Qt.ItemDataRole.UserRole, job.job_id)
            if job.status == STATUS_SUCCEEDED:
                status_item.setBackground(Qt.GlobalColor.green)
            elif job.status == STATUS_DRAFT_SAVED:
                status_item.setBackground(Qt.GlobalColor.cyan)
            elif job.status == STATUS_FAILED:
                status_item.setBackground(Qt.GlobalColor.red)
            elif job.status == STATUS_IN_PROGRESS:
                status_item.setBackground(Qt.GlobalColor.yellow)
            elif job.status == STATUS_AWAITING_CONFIRMATION:
                status_item.setBackground(Qt.GlobalColor.cyan)
            self.batch_table.setItem(row, 1, status_item)
            self.batch_table.setItem(
                row, 2, QTableWidgetItem(job.product_id or job.search_value or "-")
            )
            url_item = QTableWidgetItem(job.normalized_url)
            url_item.setToolTip(job.normalized_url)
            self.batch_table.setItem(row, 3, url_item)

            shipping_widget = QWidget()
            shipping_layout = QHBoxLayout(shipping_widget)
            shipping_layout.setContentsMargins(2, 2, 2, 2)
            shipping_layout.setSpacing(4)
            shipping_group = QButtonGroup(shipping_widget)
            shipping_group.setExclusive(True)
            free_button = QRadioButton("무료배송")
            cod_button = QRadioButton("착불배송")
            shipping_button_style = """
                QRadioButton {
                    border: 1px solid #adb5bd;
                    border-radius: 5px;
                    padding: 6px 9px;
                    background: white;
                }
                QRadioButton::indicator { width: 0; height: 0; }
                QRadioButton:checked {
                    border: 2px solid #0d6efd;
                    color: #0d6efd;
                    background: #e7f1ff;
                    font-weight: bold;
                }
                QRadioButton:disabled { color: #6c757d; background: #e9ecef; }
            """
            editable = job.status not in {
                STATUS_DRAFT_SAVED,
                STATUS_SUCCEEDED,
                STATUS_SKIPPED,
                STATUS_IN_PROGRESS,
                STATUS_AWAITING_CONFIRMATION,
            }
            for button in (free_button, cod_button):
                button.setStyleSheet(shipping_button_style)
                button.setEnabled(editable)
                shipping_group.addButton(button)
                shipping_layout.addWidget(button)
            free_button.setChecked(job.shipping_fee_type == "free")
            cod_button.setChecked(job.shipping_fee_type == "cash_on_delivery")
            free_button.toggled.connect(
                lambda checked, job_id=job.job_id: (
                    self._on_batch_shipping_changed(job_id, "free")
                    if checked
                    else None
                )
            )
            cod_button.toggled.connect(
                lambda checked, job_id=job.job_id: (
                    self._on_batch_shipping_changed(job_id, "cash_on_delivery")
                    if checked
                    else None
                )
            )
            self.batch_table.setRowHeight(row, 44)
            self.batch_table.setCellWidget(row, 4, shipping_widget)

            submission_widget = QWidget()
            submission_layout = QHBoxLayout(submission_widget)
            submission_layout.setContentsMargins(2, 2, 2, 2)
            submission_layout.setSpacing(4)
            submission_group = QButtonGroup(submission_widget)
            submission_group.setExclusive(True)
            draft_button = QRadioButton("임시저장")
            final_button = QRadioButton("상품등록")
            draft_button.setStyleSheet(
                shipping_button_style.replace("#0d6efd", "#fd7e14").replace(
                    "#e7f1ff", "#fff3cd"
                )
            )
            final_button.setStyleSheet(
                shipping_button_style.replace("#0d6efd", "#198754").replace(
                    "#e7f1ff", "#d1e7dd"
                )
            )
            for button in (draft_button, final_button):
                button.setEnabled(editable)
                submission_group.addButton(button)
                submission_layout.addWidget(button)
            draft_button.setChecked(job.submission_mode == "draft")
            final_button.setChecked(job.submission_mode == "final")
            draft_button.toggled.connect(
                lambda checked, job_id=job.job_id: (
                    self._on_batch_submission_changed(job_id, "draft")
                    if checked
                    else None
                )
            )
            final_button.toggled.connect(
                lambda checked, job_id=job.job_id: (
                    self._on_batch_submission_changed(job_id, "final")
                    if checked
                    else None
                )
            )
            self.batch_table.setCellWidget(row, 5, submission_widget)

            translation_widget = QWidget()
            translation_layout = QHBoxLayout(translation_widget)
            translation_layout.setContentsMargins(2, 2, 2, 2)
            translation_layout.setSpacing(4)
            translation_group = QButtonGroup(translation_widget)
            translation_group.setExclusive(True)
            translate_button = QRadioButton("한국어 자연화")
            original_button = QRadioButton("원본 유지")
            translation_style = shipping_button_style.replace(
                "#0d6efd", "#6f42c1"
            ).replace("#e7f1ff", "#e2d9f3")
            for button in (translate_button, original_button):
                button.setStyleSheet(translation_style)
                button.setEnabled(editable)
                translation_group.addButton(button)
                translation_layout.addWidget(button)
            translate_button.setChecked(job.translation_mode == "auto_ko")
            original_button.setChecked(job.translation_mode == "original")
            translate_button.toggled.connect(
                lambda checked, job_id=job.job_id: (
                    self._on_batch_translation_mode_changed(job_id, "auto_ko")
                    if checked
                    else None
                )
            )
            original_button.toggled.connect(
                lambda checked, job_id=job.job_id: (
                    self._on_batch_translation_mode_changed(job_id, "original")
                    if checked
                    else None
                )
            )
            self.batch_table.setCellWidget(row, 6, translation_widget)
            self.batch_table.setItem(
                row, 7, QTableWidgetItem(job.translation_status_label)
            )

            self.batch_table.setItem(
                row, 8, QTableWidgetItem(self._money_text(job.minimum_selling_price))
            )
            self.batch_table.setItem(
                row, 9, QTableWidgetItem(self._money_text(job.return_shipping_fee))
            )
            self.batch_table.setItem(
                row, 10, QTableWidgetItem(job.country_of_origin or "자동 확인 예정")
            )
            self.batch_table.setItem(row, 11, QTableWidgetItem(str(job.attempts)))
            self.batch_table.setItem(
                row, 12, QTableWidgetItem(job.registered_product_id)
            )
            result_text = job.last_error or job.automation_message
            self.batch_table.setItem(row, 13, QTableWidgetItem(result_text))

            if job.job_id == selected_job_id or (
                selected_job_id is None and job.job_id == self.active_batch_job_id
            ):
                selected_row = row

        self._refreshing_batch_selection = False
        self._sync_select_all_batch_checkbox()

        if selected_row >= 0:
            self.batch_table.selectRow(selected_row)

        summary = self.batch_queue.summary()
        self.batch_summary_label.setText(
            "전체 {total}개 중 자동등록 선택 {selected}개 · 배송비 선택 필요 {waiting_shipping}개 · 등록방식 선택 필요 "
            "{waiting_submission}개 · 한국어 이미지 준비/검수 필요 {waiting_translation}개 · "
            "등록 대기 {ready}개 · WING 자동입력 중 {in_progress}개 · "
            "최종 실행 확인 대기 {awaiting_confirmation}개 · 실패 {failed}개 · "
            "완료 항목은 자동 제거".format(**summary)
        )

    def _selected_batch_job_id(self) -> str | None:
        row = self.batch_table.currentRow()
        if row < 0:
            return None
        item = self.batch_table.item(row, 1)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _sync_select_all_batch_checkbox(self) -> None:
        if not hasattr(self, "select_all_batch_checkbox"):
            return
        selectable_jobs = [
            job
            for job in self.batch_queue.jobs
            if job.status not in {STATUS_IN_PROGRESS, STATUS_AWAITING_CONFIRMATION}
        ]
        selected_count = sum(1 for job in selectable_jobs if job.is_selected)
        self.select_all_batch_checkbox.blockSignals(True)
        if not selectable_jobs or selected_count == 0:
            state = Qt.CheckState.Unchecked
        elif selected_count == len(selectable_jobs):
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self.select_all_batch_checkbox.setCheckState(state)
        self.select_all_batch_checkbox.setText(
            f"전체 상품 선택/해제 ({selected_count}/{len(selectable_jobs)})"
        )
        self.select_all_batch_checkbox.blockSignals(False)

    def _toggle_all_batch_selection(self, _checked: bool = False) -> None:
        selectable_jobs = [
            job
            for job in self.batch_queue.jobs
            if job.status not in {STATUS_IN_PROGRESS, STATUS_AWAITING_CONFIRMATION}
        ]
        if not selectable_jobs:
            self._sync_select_all_batch_checkbox()
            return
        select_all = not all(job.is_selected for job in selectable_jobs)
        for job in selectable_jobs:
            self.batch_queue.set_selected(job.job_id, select_all)
        self._persist_batch_queue()
        self._refresh_batch_table(self._selected_batch_job_id())
        action = "선택" if select_all else "해제"
        self.statusBar().showMessage(
            f"{len(selectable_jobs)}개 상품을 전체 {action}했습니다.", 4000
        )

    def _on_batch_selection_changed(self, job_id: str, selected: bool) -> None:
        if self._refreshing_batch_selection:
            return
        self.batch_queue.set_selected(job_id, selected)
        self._persist_batch_queue()
        self._refresh_batch_table(job_id)
        action = "자동등록 대상으로 선택" if selected else "자동등록에서 제외"
        self.statusBar().showMessage(f"상품을 {action}했습니다.", 3500)

    def _has_selected_ready_batch_job(self) -> bool:
        return any(
            job.status == STATUS_READY and job.is_selected
            for job in self.batch_queue.jobs
        )

    def _on_batch_shipping_changed(
        self, job_id: str, shipping_fee_type: str | None
    ) -> None:
        self.batch_queue.set_shipping(job_id, shipping_fee_type)
        self._persist_batch_queue()
        self._refresh_batch_table(job_id)

    def _on_batch_submission_changed(
        self, job_id: str, submission_mode: str | None
    ) -> None:
        self.batch_queue.set_submission_mode(job_id, submission_mode)
        self._persist_batch_queue()
        self._refresh_batch_table(job_id)

    def _on_batch_translation_mode_changed(
        self, job_id: str, translation_mode: str
    ) -> None:
        self.batch_queue.set_translation_mode(job_id, translation_mode)
        self._persist_batch_queue()
        self._refresh_batch_table(job_id)
        self._refresh_translation_previews()

    def _selected_batch_job(self):
        job_id = self._selected_batch_job_id()
        job = self.batch_queue.get(job_id) if job_id else None
        if not job:
            QMessageBox.information(
                self, "상품 선택 필요", "먼저 일괄등록 표에서 상품 행을 선택하세요."
            )
        return job

    @staticmethod
    def _set_image_preview(label: QLabel, path: str | None, empty_text: str) -> None:
        label.clear()
        pixmap = QPixmap(path or "")
        if pixmap.isNull():
            label.setText(empty_text)
            return
        label.setPixmap(
            pixmap.scaled(
                440,
                150,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _refresh_translation_previews(self) -> None:
        if not hasattr(self, "translation_source_preview"):
            return
        job_id = self._selected_batch_job_id() if hasattr(self, "batch_table") else None
        job = self.batch_queue.get(job_id) if job_id else None
        source_path = job.source_detail_image_paths[0] if job and job.source_detail_image_paths else None
        result_path = (
            job.translated_detail_image_paths[0]
            if job and job.translated_detail_image_paths
            else None
        )
        self._set_image_preview(
            self.translation_source_preview,
            source_path,
            "선택 상품의 원본 이미지 없음",
        )
        self._set_image_preview(
            self.translation_result_preview,
            result_path,
            "한국어 결과 이미지 없음",
        )
        if job:
            self.translation_help_label.setText(
                f"선택 상품 {job.product_id or job.search_value} · {job.translation_mode_label} · "
                f"{job.translation_status_label}"
            )
        if hasattr(self, "option_watermark_status"):
            if not job or not job.source_option_image_paths:
                self.option_watermark_status.setText("선택 상품의 옵션 이미지 없음")
            else:
                self.option_watermark_status.setText(
                    f"옵션 원본 {len(job.source_option_image_paths)}장 · "
                    f"워터마크 완료 {len(job.watermarked_option_image_paths)}장"
                )

    def _prepare_selected_translation_job(self) -> None:
        job = self._selected_batch_job()
        if not job:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "상세페이지 원본 이미지 선택",
            str(BASE_DIR),
            "이미지 파일 (*.jpg *.jpeg *.png *.webp)",
        )
        if not paths:
            return
        try:
            manifest = prepare_translation_job(
                job_id=job.job_id,
                source_paths=paths,
                output_root=TRANSLATION_JOB_ROOT,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "한국어 이미지 작업 준비 실패", str(exc))
            return
        self.batch_queue.set_translation_mode(job.job_id, "auto_ko")
        self.batch_queue.set_translation_source_images(
            job.job_id, manifest["source_images"]
        )
        self._persist_batch_queue()
        self._refresh_batch_table(job.job_id)
        self._refresh_translation_previews()
        QApplication.clipboard().setText(
            Path(manifest["prompt_file"]).read_text(encoding="utf-8")
            + "\n\n작업 폴더: "
            + str(TRANSLATION_JOB_ROOT / job.job_id)
        )
        QMessageBox.information(
            self,
            "한국어 이미지 작업 준비 완료",
            "원본을 보존해 작업 폴더를 만들고 Codex 한국어 이미지 요청을 클립보드에 "
            "복사했습니다.",
        )

    def _copy_translation_request(self) -> None:
        job = self._selected_batch_job()
        if not job:
            return
        prompt_path = TRANSLATION_JOB_ROOT / job.job_id / "translation_prompt.txt"
        if not prompt_path.is_file():
            QMessageBox.information(
                self,
                "상세 원본 필요",
                "먼저 '선택 상품 상세 이미지 넣기'를 눌러 작업을 준비하세요.",
            )
            return
        request = (
            prompt_path.read_text(encoding="utf-8")
            + "\n\n이 작업 폴더의 source 이미지를 편집하고 결과를 translated 폴더에 저장해줘: "
            + str(TRANSLATION_JOB_ROOT / job.job_id)
        )
        QApplication.clipboard().setText(request)
        self.statusBar().showMessage("Codex 한국어 이미지 요청을 복사했습니다.", 5000)

    def _open_translation_folder(self) -> None:
        job = self._selected_batch_job()
        if not job:
            return
        folder = TRANSLATION_JOB_ROOT / job.job_id
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _apply_configured_watermark(
        self,
        source_paths: list[str],
        *,
        job_id: str,
        output_folder: str = "watermarked",
    ) -> list[str]:
        settings = self._current_watermark_settings()
        if not settings["enabled"]:
            return source_paths
        return apply_watermark_to_images(
            source_paths=source_paths,
            output_dir=TRANSLATION_JOB_ROOT / job_id / output_folder,
            image_path=settings["image_path"],
            text=settings["text"],
            position=settings["position"],
            opacity_percent=settings["opacity_percent"],
            size_percent=settings["size_percent"],
            angle_degrees=settings["angle_degrees"],
        )

    def _prepare_selected_option_images(self) -> None:
        job = self._selected_batch_job()
        if not job:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "옵션 이미지 선택",
            str(BASE_DIR),
            "이미지 파일 (*.jpg *.jpeg *.png *.webp)",
        )
        if not paths:
            return
        try:
            copied = prepare_option_images(
                job_id=job.job_id,
                source_paths=paths,
                output_root=TRANSLATION_JOB_ROOT,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "옵션 이미지 준비 실패", str(exc))
            return
        self.batch_queue.set_option_images(job.job_id, copied)
        watermarked: list[str] = []
        if self.watermark_enabled.isChecked():
            self._save_watermark_controls()
            try:
                watermarked = self._apply_configured_watermark(
                    copied,
                    job_id=job.job_id,
                    output_folder="option_watermarked",
                )
            except (OSError, ValueError) as exc:
                QMessageBox.warning(
                    self,
                    "옵션 워터마크 적용 실패",
                    "옵션 원본은 정상적으로 보관했지만 워터마크를 적용하지 못했습니다.\n\n"
                    + str(exc),
                )
        self.batch_queue.set_watermarked_option_images(job.job_id, watermarked)
        self._persist_batch_queue()
        self._refresh_batch_table(job.job_id)
        self._refresh_translation_previews()
        QMessageBox.information(
            self,
            "옵션 이미지 준비 완료",
            f"옵션 이미지 {len(copied)}장을 보관하고 워터마크 {len(watermarked)}장을 만들었습니다.",
        )

    def _apply_watermark_to_selected_results(self) -> None:
        job = self._selected_batch_job()
        if not job:
            return
        if not self.watermark_enabled.isChecked():
            QMessageBox.information(
                self, "워터마크 사용 꺼짐", "먼저 '옵션·상세이미지에 워터마크 사용'을 선택하세요."
            )
            return
        translated_dir = TRANSLATION_JOB_ROOT / job.job_id / "translated"
        detail_source_paths = [
            str(path)
            for path in sorted(translated_dir.glob("translated_*"))
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]
        if not detail_source_paths:
            detail_source_paths = [
                path
                for path in job.translated_detail_image_paths
                if "option_watermarked" not in path
            ]
        option_source_paths = list(job.source_option_image_paths)
        if not detail_source_paths and not option_source_paths:
            QMessageBox.information(
                self,
                "적용할 이미지 없음",
                "먼저 옵션 이미지를 넣거나 한국어 교정·번역 결과를 불러오세요.",
            )
            return
        self._save_watermark_controls()
        try:
            detail_watermarked = (
                self._apply_configured_watermark(
                    detail_source_paths, job_id=job.job_id
                )
                if detail_source_paths
                else []
            )
            option_watermarked = (
                self._apply_configured_watermark(
                    option_source_paths,
                    job_id=job.job_id,
                    output_folder="option_watermarked",
                )
                if option_source_paths
                else []
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "워터마크 적용 실패", str(exc))
            return
        if detail_watermarked:
            self.batch_queue.set_translated_images(job.job_id, detail_watermarked)
        self.batch_queue.set_watermarked_option_images(
            job.job_id, option_watermarked
        )
        self._persist_batch_queue()
        self._refresh_batch_table(job.job_id)
        self._refresh_translation_previews()
        QMessageBox.information(
            self,
            "워터마크 적용 완료",
            f"옵션 이미지 {len(option_watermarked)}장 · 상세이미지 {len(detail_watermarked)}장에 "
            "워터마크를 적용했습니다. 미리보기 후 다시 검수 승인하세요.",
        )

    def _import_translation_results(self) -> None:
        job = self._selected_batch_job()
        if not job:
            return
        start_dir = TRANSLATION_JOB_ROOT / job.job_id / "translated"
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "한국어 교정·번역 결과 이미지 선택",
            str(start_dir if start_dir.is_dir() else BASE_DIR),
            "이미지 파일 (*.jpg *.jpeg *.png *.webp)",
        )
        if not paths:
            return
        try:
            imported = import_translation_results(
                job_id=job.job_id,
                result_paths=paths,
                output_root=TRANSLATION_JOB_ROOT,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "한국어 결과 불러오기 실패", str(exc))
            return
        final_paths = imported
        watermark_message = ""
        if self.watermark_enabled.isChecked():
            self._save_watermark_controls()
            try:
                final_paths = self._apply_configured_watermark(
                    imported, job_id=job.job_id
                )
                watermark_message = " 워터마크도 함께 적용했습니다."
            except (OSError, ValueError) as exc:
                QMessageBox.warning(
                    self,
                    "워터마크 적용 실패",
                    "한국어 결과는 정상적으로 불러왔지만 워터마크는 적용하지 못했습니다.\n\n"
                    + str(exc),
                )
        self.batch_queue.set_translation_mode(job.job_id, "auto_ko")
        self.batch_queue.set_translated_images(job.job_id, final_paths)
        self._persist_batch_queue()
        self._refresh_batch_table(job.job_id)
        self._refresh_translation_previews()
        QMessageBox.information(
            self,
            "한국어 결과 불러오기 완료",
            f"한국어 교정·번역 이미지 {len(imported)}장을 불러왔습니다."
            f"{watermark_message} 미리보기 후 검수 승인하세요.",
        )

    def _approve_translation_results(self) -> None:
        job = self._selected_batch_job()
        if not job:
            return
        if not job.translated_detail_image_paths:
            QMessageBox.information(
                self, "한국어 결과 없음", "먼저 한국어 결과 이미지를 불러오세요."
            )
            return
        answer = QMessageBox.question(
            self,
            "한국어 결과 검수 승인",
            "모든 이미지의 한국어 문구, 숫자, 치수, 모델명과 이미지 순서를 확인했나요?\n\n"
            "승인하면 실제 상품등록 대상으로 진행할 수 있습니다.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.batch_queue.approve_translation(job.job_id)
        except ValueError as exc:
            QMessageBox.warning(self, "검수 승인 실패", str(exc))
            return
        self._persist_batch_queue()
        self._refresh_batch_table(job.job_id)
        self._refresh_translation_previews()
        self.statusBar().showMessage("한국어 이미지 사용을 승인했습니다.", 5000)

    def _add_batch_urls(self) -> None:
        text = self.batch_url_input.toPlainText().strip()
        if not text:
            QMessageBox.information(
                self, "URL 입력 필요", "쿠팡 상품 URL을 한 줄에 하나씩 입력하세요."
            )
            return
        result = self.batch_queue.add_text(text)
        self.batch_url_input.clear()
        self._persist_batch_queue()
        self._refresh_batch_table()
        QMessageBox.information(
            self,
            "목록 추가 완료",
            f"추가 {result['added']}개 · 중복 {result['duplicates']}개 · "
            f"잘못된 URL {result['invalid']}개",
        )

    def _save_excel_template_copy(self) -> None:
        if not REGISTRATION_EXCEL_TEMPLATE_PATH.exists():
            QMessageBox.warning(
                self,
                "엑셀 양식 없음",
                "프로그램 폴더에서 엑셀 양식을 찾지 못했습니다. 프로그램을 다시 받아주세요.",
            )
            return
        default_path = Path.home() / "Desktop" / "쿠팡_상품등록_양식.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "쿠팡 상품등록 엑셀 양식 저장",
            str(default_path),
            "Excel 파일 (*.xlsx)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".xlsx":
            target = target.with_suffix(".xlsx")
        try:
            shutil.copy2(REGISTRATION_EXCEL_TEMPLATE_PATH, target)
        except OSError as exc:
            QMessageBox.warning(self, "양식 저장 실패", str(exc))
            return
        QMessageBox.information(
            self,
            "양식 저장 완료",
            f"엑셀 양식을 저장했습니다.\n\n{target}\n\n"
            "URL과 선택값을 입력하고 저장한 뒤 ‘엑셀 파일 불러오기’를 누르세요.",
        )

    def _import_batch_excel(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "작성한 쿠팡 상품등록 엑셀 파일 선택",
            str(Path.home() / "Desktop"),
            "Excel 파일 (*.xlsx)",
        )
        if not path:
            return
        try:
            result = read_registration_workbook(path)
        except ExcelImportError as exc:
            QMessageBox.warning(self, "엑셀 불러오기 실패", str(exc))
            return

        added = updated = invalid = locked = 0
        row_errors: list[str] = []
        locked_statuses = {
            STATUS_DRAFT_SAVED,
            STATUS_SUCCEEDED,
            STATUS_SKIPPED,
            STATUS_IN_PROGRESS,
            STATUS_AWAITING_CONFIRMATION,
        }
        for excel_row in result.rows:
            candidate = BatchJob.from_url(excel_row.source_url)
            if not candidate.search_value:
                invalid += 1
                row_errors.append(f"{excel_row.row_number}행: 쿠팡 상품 ID를 찾지 못함")
                continue
            job = next(
                (
                    item
                    for item in self.batch_queue.jobs
                    if item.identity_key == candidate.identity_key
                ),
                None,
            )
            if job is None:
                self.batch_queue.jobs.append(candidate)
                job = candidate
                added += 1
            elif job.status in locked_statuses:
                locked += 1
                continue
            else:
                updated += 1
            self.batch_queue.set_selected(job.job_id, True)
            self.batch_queue.set_shipping(job.job_id, excel_row.shipping_fee_type)
            self.batch_queue.set_submission_mode(job.job_id, excel_row.submission_mode)
            self.batch_queue.set_translation_mode(job.job_id, excel_row.translation_mode)

        self._persist_batch_queue()
        self._refresh_batch_table()
        summary = (
            f"새로 추가 {added}개 · 기존 설정 갱신 {updated}개 · "
            f"엑셀에서 제외 {result.excluded}개"
        )
        if invalid or locked:
            summary += f" · 잘못된 URL {invalid}개 · 진행/완료 중이라 건너뜀 {locked}개"
        if row_errors:
            summary += "\n\n확인할 행:\n" + "\n".join(row_errors[:8])
        if not self._has_selected_ready_batch_job():
            QMessageBox.information(
                self,
                "엑셀 불러오기 완료",
                summary + "\n\n대기열의 선택값을 확인해주세요.",
            )
            return
        answer = QMessageBox.question(
            self,
            "엑셀 불러오기 완료",
            summary
            + "\n\n선택된 등록 대기 상품을 지금 첫 상품부터 자동등록할까요?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            QTimer.singleShot(0, self._open_next_batch_job)

    def _set_all_batch_shipping(self, shipping_fee_type: str) -> None:
        changed = 0
        for job in self.batch_queue.jobs:
            if job.status in {
                STATUS_DRAFT_SAVED,
                STATUS_SUCCEEDED,
                STATUS_SKIPPED,
                STATUS_IN_PROGRESS,
            }:
                continue
            if job.search_value:
                self.batch_queue.set_shipping(job.job_id, shipping_fee_type)
                changed += 1
        self._persist_batch_queue()
        self._refresh_batch_table()
        self.statusBar().showMessage(f"{changed}개 상품의 배송비 방식을 변경했습니다.", 4000)

    def _persist_batch_queue(self) -> bool:
        try:
            save_batch_queue(self.batch_queue, BATCH_QUEUE_PATH)
            return True
        except OSError as exc:
            self.statusBar().showMessage(f"작업 목록 자동 저장 실패: {exc}", 8000)
            return False

    def _save_batch_queue(self) -> None:
        if self._persist_batch_queue():
            QMessageBox.information(
                self, "저장 완료", f"작업 목록을 저장했습니다.\n\n{BATCH_QUEUE_PATH}"
            )

    def _reload_batch_queue(self) -> None:
        try:
            self.batch_queue = load_batch_queue(BATCH_QUEUE_PATH)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "불러오기 실패", str(exc))
            return
        recovered = self.batch_queue.recover_interrupted_jobs()
        archived = 0
        try:
            archived = len(
                archive_and_remove_completed(
                    self.batch_queue,
                    COMPLETED_REPORT_DIR,
                )
            )
        except OSError as exc:
            QMessageBox.warning(self, "완료 기록 저장 실패", str(exc))
        if recovered or archived:
            self._persist_batch_queue()
        self.active_batch_job_id = next(
            (
                job.job_id
                for job in self.batch_queue.jobs
                if job.status in {STATUS_IN_PROGRESS, STATUS_AWAITING_CONFIRMATION}
            ),
            None,
        )
        self._refresh_batch_table(self.active_batch_job_id)
        if recovered:
            self.statusBar().showMessage(
                f"저장 목록을 불러오고 중단 작업 {recovered}개를 등록 대기로 복구했습니다.",
                5000,
            )
        elif archived:
            self.statusBar().showMessage(
                f"완료 상품 {archived}개를 기록으로 옮기고 작업 목록에서 정리했습니다.",
                5000,
            )
        else:
            self.statusBar().showMessage("저장된 작업 목록을 다시 불러왔습니다.", 4000)

    def _open_next_batch_job(self) -> None:
        if self.automation_process and self.automation_process.poll() is None:
            QMessageBox.information(
                self,
                "자동입력 진행 중",
                "현재 상품의 WING 자동입력이 진행 중입니다. 표의 진행 문구를 확인하세요.",
            )
            return

        previous_active = self.active_batch_job_id
        job = self.batch_queue.peek_next()
        if not job:
            # 체크된 실패 상품은 별도 '실패 상품 재시도' 버튼을 먼저 누르게
            # 하지 않고, 자동입력 시작 버튼 한 번으로 바로 대기열에 복구한다.
            retried = self.batch_queue.retry_failed(selected_only=True)
            if retried:
                self.confirmation_prompted_job_id = None
                self._persist_batch_queue()
                self._refresh_batch_table()
                job = self.batch_queue.peek_next()
        if not job:
            QMessageBox.information(
                self,
                "등록 대기 상품 없음",
                "자동등록할 상품의 맨 앞 체크박스를 선택하고, 배송비와 "
                "임시저장/상품등록 방식을 모두 선택하세요.",
            )
            return

        if job.status == STATUS_AWAITING_CONFIRMATION:
            self._confirm_and_submit(job)
            return

        is_new_job = previous_active != job.job_id
        if is_new_job:
            self.catalog_match_price.setValue(0)
            self.country_of_origin.clear()
        self.url_input.setText(job.normalized_url)
        self._analyze()
        if job.shipping_fee_type == "free":
            self.free_shipping_button.setChecked(True)
        else:
            self.cod_shipping_button.setChecked(True)
        job = self.batch_queue.start_next()
        if not job:
            return
        self.active_batch_job_id = job.job_id
        self._persist_batch_queue()
        self._refresh_batch_table(job.job_id)
        try:
            self.automation_process = self._launch_wing_worker(
                mode="prepare", job_id=job.job_id
            )
        except OSError as exc:
            self.batch_queue.mark_failed(job.job_id, str(exc))
            self.active_batch_job_id = None
            self._persist_batch_queue()
            self._refresh_batch_table(job.job_id)
            QMessageBox.warning(self, "자동입력 시작 실패", str(exc))
            return
        self.automation_action = "run"
        self._set_automation_running(True)
        self._set_browser_status(
            f"상품 ID {job.search_value}의 WING 자동입력을 시작했습니다. "
            "카탈로그 검색 → 옵션 선택 → 판매가·재고·배송 → 상세페이지 순서로 진행합니다.",
            success=True,
        )
        self.statusBar().showMessage(
            f"상품 ID {job.search_value} 자동입력 시작 · 완료 후 {job.submission_mode_label} 자동 실행",
            6000,
        )

    def _worker_python(self) -> Path:
        candidates = (
            BASE_DIR / ".venv" / "Scripts" / "pythonw.exe",
            BASE_DIR / ".venv" / "Scripts" / "python.exe",
            Path(sys.executable),
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise OSError("자동입력을 실행할 Python을 찾지 못했습니다. 설치.bat을 실행하세요.")

    def _launch_wing_worker(self, *, mode: str, job_id: str) -> subprocess.Popen:
        command = [
            str(self._worker_python()),
            "-m",
            "poc.wing_worker",
            "--mode",
            mode,
            "--job-id",
            job_id,
            "--queue",
            str(BATCH_QUEUE_PATH),
            "--profile",
            str(WING_PROFILE_DIR),
            "--log",
            str(WING_AUTOMATION_LOG),
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(
            command,
            cwd=BASE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            close_fds=True,
        )

    def _set_automation_running(self, running: bool) -> None:
        """자동입력 시작/중지 버튼을 현재 실행 상태에 맞춘다."""

        self.open_next_button.setEnabled(not running)
        self.stop_automation_button.setEnabled(running)

    def _stop_wing_automation(self) -> None:
        """현재 자동입력 작업만 멈추고 해당 상품을 안전한 재시도 상태로 남긴다."""

        process = self.automation_process
        if process is None or process.poll() is not None:
            self._set_automation_running(False)
            self._poll_wing_automation()
            self.statusBar().showMessage("현재 실행 중인 자동입력 작업이 없습니다.", 4000)
            return

        job_id = self.active_batch_job_id
        try:
            process.terminate()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            # 종료 직전에 작업이 끝난 경우도 있으므로 실제 실행 상태를 아래에서 다시 확인한다.
            pass

        if process.poll() is None:
            self.statusBar().showMessage(
                "자동입력을 중지하지 못했습니다. 잠시 후 다시 눌러 주세요.", 6000
            )
            return

        self.automation_process = None
        self.automation_action = ""
        self._set_automation_running(False)

        try:
            queue = load_batch_queue(BATCH_QUEUE_PATH)
        except (OSError, ValueError):
            queue = self.batch_queue
        job = queue.get(job_id) if job_id else None
        if job and job.status in {STATUS_IN_PROGRESS, STATUS_AWAITING_CONFIRMATION}:
            queue.mark_failed(
                job.job_id,
                "사용자가 프로그램을 중지했습니다. '다음 상품 자동입력 시작'을 누르면 바로 다시 시작합니다.",
            )
            try:
                save_batch_queue(queue, BATCH_QUEUE_PATH)
            except OSError as exc:
                self.statusBar().showMessage(
                    f"자동입력은 중지했지만 작업 상태 저장에 실패했습니다: {exc}",
                    8000,
                )
        self.batch_queue = queue
        self.active_batch_job_id = None
        if self.confirmation_prompted_job_id == job_id:
            self.confirmation_prompted_job_id = None
        self._refresh_batch_table(job_id)
        self._set_browser_status(
            "사용자가 자동입력을 중지했습니다. WING 로그인 창은 그대로 사용할 수 있습니다.",
            success=False,
        )
        self.statusBar().showMessage(
            "프로그램을 중지했습니다. '다음 상품 자동입력 시작'을 누르면 바로 다시 시작합니다.",
            7000,
        )

    def _poll_wing_automation(self) -> None:
        process = self.automation_process
        if not process:
            # 프로그램 외부에서 시작된 동일 작업(업데이트 직후 이어서 실행 등)도
            # 저장 목록을 통해 화면에 즉시 반영한다.
            try:
                queue = load_batch_queue(BATCH_QUEUE_PATH)
            except (OSError, ValueError):
                return
            active = next(
                (
                    item
                    for item in queue.jobs
                    if item.status
                    in {STATUS_IN_PROGRESS, STATUS_AWAITING_CONFIRMATION}
                ),
                None,
            )
            if active:
                self.batch_queue = queue
                self.active_batch_job_id = active.job_id
                self._refresh_batch_table(active.job_id)
                if active.automation_message:
                    self._set_browser_status(active.automation_message, success=True)
                if (
                    active.status == STATUS_AWAITING_CONFIRMATION
                    and self.confirmation_prompted_job_id != active.job_id
                ):
                    self.confirmation_prompted_job_id = active.job_id
                    QTimer.singleShot(
                        100,
                        lambda selected=active: self._confirm_and_submit(selected),
                    )
            elif self.active_batch_job_id and not queue.get(self.active_batch_job_id):
                self.batch_queue = queue
                self.active_batch_job_id = None
                self.confirmation_prompted_job_id = None
                self._refresh_batch_table()
                if self._has_selected_ready_batch_job():
                    QTimer.singleShot(800, self._open_next_batch_job)
            return
        try:
            queue = load_batch_queue(BATCH_QUEUE_PATH)
        except (OSError, ValueError):
            return
        selected_id = self.active_batch_job_id
        self.batch_queue = queue
        job = queue.get(selected_id) if selected_id else None
        self._refresh_batch_table(selected_id)
        if job and job.automation_message:
            self._set_browser_status(
                job.automation_message,
                success=job.status != STATUS_FAILED,
            )
        if process.poll() is None:
            return

        action = self.automation_action
        self.automation_process = None
        self.automation_action = ""
        self._set_automation_running(False)
        if not job:
            self.active_batch_job_id = None
            self.confirmation_prompted_job_id = None
            self._refresh_batch_table()
            if action in {"run", "submit"}:
                self.statusBar().showMessage(
                    "WING 상품등록이 완료되어 해당 상품을 작업 목록에서 자동 제거했습니다.",
                    6000,
                )
                if self._has_selected_ready_batch_job():
                    QTimer.singleShot(800, self._open_next_batch_job)
            return
        if job.status == STATUS_AWAITING_CONFIRMATION:
            if action == "prepare" and self.confirmation_prompted_job_id != job.job_id:
                self.confirmation_prompted_job_id = job.job_id
                QTimer.singleShot(100, lambda selected=job: self._confirm_and_submit(selected))
            return
        if job.status in {STATUS_SUCCEEDED, STATUS_DRAFT_SAVED}:
            completion_message = job.automation_message or "WING 처리 완료"
            removed = False
            try:
                archive_and_remove_completed(
                    self.batch_queue,
                    COMPLETED_REPORT_DIR,
                    job_ids={job.job_id},
                )
                self._persist_batch_queue()
                self._refresh_batch_table()
                removed = True
            except OSError as exc:
                self.statusBar().showMessage(
                    f"완료 기록 저장 실패로 행을 유지합니다: {exc}", 8000
                )
            self.active_batch_job_id = None
            self.confirmation_prompted_job_id = None
            if removed:
                self.statusBar().showMessage(
                    f"{completion_message} · 작업 목록에서 자동 제거", 6000
                )
            if self._has_selected_ready_batch_job():
                QTimer.singleShot(800, self._open_next_batch_job)
            return
        if job.status == STATUS_FAILED:
            self.active_batch_job_id = None
            if self.confirmation_prompted_job_id == job.job_id:
                self.confirmation_prompted_job_id = None
            self.statusBar().showMessage(
                f"상품 ID {job.search_value} 자동입력 실패 — 다음 대기 상품을 계속합니다.",
                7000,
            )
            if self._has_selected_ready_batch_job():
                QTimer.singleShot(1000, self._open_next_batch_job)
            return
        if process.returncode:
            self.batch_queue.mark_failed(
                job.job_id,
                "자동입력 실행이 예기치 않게 종료되었습니다. 자동입력 로그를 확인하세요.",
            )
            self.active_batch_job_id = None
            if self.confirmation_prompted_job_id == job.job_id:
                self.confirmation_prompted_job_id = None
            self._persist_batch_queue()
            self._refresh_batch_table(job.job_id)

    def _confirm_and_submit(self, job) -> None:
        if job.status != STATUS_AWAITING_CONFIRMATION:
            return
        button_name = "임시 저장" if job.submission_mode == "draft" else "상품등록"
        # 각 상품 행의 '임시저장/상품등록' 선택 자체를 최종 실행 지시로 사용한다.
        # 준비가 끝난 뒤 같은 내용을 다시 묻지 않고 저장 후 다음 대기 상품으로 이어간다.
        try:
            self.automation_process = self._launch_wing_worker(
                mode="submit", job_id=job.job_id
            )
        except OSError as exc:
            QMessageBox.warning(self, "최종 실행 실패", str(exc))
            return
        self.automation_action = "submit"
        self._set_automation_running(True)
        self._set_browser_status(f"WING에서 {button_name}을 실행하고 있습니다.", success=True)

    def _mark_batch_succeeded(self) -> None:
        job_id = self._selected_batch_job_id() or self.active_batch_job_id
        job = self.batch_queue.get(job_id) if job_id else None
        if job and job.status == STATUS_AWAITING_CONFIRMATION:
            self._confirm_and_submit(job)
            return
        if not job or job.status != STATUS_IN_PROGRESS:
            QMessageBox.information(
                self,
                "WING 입력 대기 상품 선택 필요",
                "WING 화면을 열어둔 상품 행을 선택하세요.",
            )
            return
        if job.submission_mode == "draft":
            answer = QMessageBox.question(
                self,
                "임시저장 완료 확인",
                "WING에서 이 상품의 임시저장이 완료됐나요?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            registered_id = ""
        else:
            registered_id, accepted = QInputDialog.getText(
                self,
                "상품등록 완료 기록",
                "WING 등록상품 ID가 보이면 입력하세요. 보이지 않으면 비워도 됩니다.",
                text=job.registered_product_id,
            )
            if not accepted:
                return
        self.batch_queue.mark_succeeded(
            job.job_id,
            registered_product_id=registered_id,
            minimum_selling_price=(self.catalog_match_price.value() or None),
            country_of_origin=self.country_of_origin.text(),
        )
        self.active_batch_job_id = None
        # 성공 상태를 먼저 저장하고, 기록 보관이 성공한 경우에만 행을 제거한다.
        self._persist_batch_queue()
        action_label = job.submission_mode_label
        removed = False
        try:
            archive_and_remove_completed(
                self.batch_queue,
                COMPLETED_REPORT_DIR,
                job_ids={job.job_id},
            )
            self._persist_batch_queue()
            removed = True
        except OSError as exc:
            QMessageBox.warning(
                self,
                "완료 기록 저장 실패",
                f"WING 처리는 완료됐지만 작업 행을 유지합니다.\n\n{exc}",
            )
        self._refresh_batch_table()
        if removed:
            self.statusBar().showMessage(
                f"{action_label} 완료로 기록하고 작업 목록에서 자동 제거했습니다.", 5000
            )
        if self._has_selected_ready_batch_job():
            self._open_next_batch_job()

    def _requeue_completed_batch_job(self) -> None:
        job = self._selected_batch_job()
        if not job:
            return
        if job.status not in {STATUS_DRAFT_SAVED, STATUS_SUCCEEDED}:
            QMessageBox.information(
                self,
                "완료 상품 선택 필요",
                "임시저장 완료 또는 등록 완료된 상품을 선택하세요.",
            )
            return
        answer = QMessageBox.question(
            self,
            "선택 상품 다시 등록",
            "배송비·등록 방식·이미지 설정은 유지하고 완료 표시와 이전 등록 결과값을 "
            "초기화해 다시 등록 대기로 돌릴까요?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.batch_queue.requeue_completed(job.job_id)
        if self.active_batch_job_id == job.job_id:
            self.active_batch_job_id = None
        self._persist_batch_queue()
        self._refresh_batch_table(job.job_id)
        self.statusBar().showMessage(
            f"상품 ID {job.search_value}를 다시 등록 대기로 돌렸습니다.", 5000
        )

    def _mark_batch_failed(self) -> None:
        job_id = self._selected_batch_job_id() or self.active_batch_job_id
        job = self.batch_queue.get(job_id) if job_id else None
        if not job or job.status != STATUS_IN_PROGRESS:
            QMessageBox.information(
                self,
                "WING 입력 대기 상품 선택 필요",
                "WING 화면을 열어둔 상품 행을 선택하세요.",
            )
            return
        error, accepted = QInputDialog.getText(
            self,
            "실패 사유 기록",
            "실패 사유를 입력하세요.",
            text=job.last_error,
        )
        if not accepted:
            return
        self.batch_queue.mark_failed(job.job_id, error)
        self.active_batch_job_id = None
        if self.confirmation_prompted_job_id == job.job_id:
            self.confirmation_prompted_job_id = None
        self._persist_batch_queue()
        self._refresh_batch_table(job.job_id)

    def _retry_failed_batch_jobs(self) -> None:
        count = self.batch_queue.retry_failed()
        # 같은 상품의 이전 최종 실행 기록이 남아 있으면 재준비가 끝난 뒤
        # 제출이 건너뛰어질 수 있으므로 재시도 시작 시 반드시 초기화한다.
        self.confirmation_prompted_job_id = None
        self._persist_batch_queue()
        self._refresh_batch_table()
        QMessageBox.information(
            self, "재시도 준비", f"재시도 가능한 실패 상품 {count}개를 대기열로 돌렸습니다."
        )

    def _skip_batch_job(self) -> None:
        selected_jobs = [job for job in self.batch_queue.jobs if job.is_selected]
        if not selected_jobs:
            QMessageBox.information(
                self,
                "상품 체크 필요",
                "삭제할 상품의 맨 앞 '등록 선택' 체크박스를 먼저 선택하세요.",
            )
            return

        blocked_statuses = {
            STATUS_DRAFT_SAVED,
            STATUS_SUCCEEDED,
            STATUS_IN_PROGRESS,
            STATUS_AWAITING_CONFIRMATION,
        }
        deletable_jobs = [
            job for job in selected_jobs if job.status not in blocked_statuses
        ]
        blocked_count = len(selected_jobs) - len(deletable_jobs)
        if not deletable_jobs:
            QMessageBox.information(
                self,
                "삭제 가능한 상품 없음",
                "체크한 상품이 현재 자동입력 중이거나 이미 처리 완료되어 삭제할 수 없습니다.",
            )
            return

        blocked_message = (
            f"\n현재 처리 중이거나 완료된 {blocked_count}개 상품은 제외됩니다."
            if blocked_count
            else ""
        )
        answer = QMessageBox.question(
            self,
            f"체크 상품 {len(deletable_jobs)}개 완전삭제 확인",
            f"체크한 상품 {len(deletable_jobs)}개를 작업 목록과 저장 목록에서 "
            f"완전히 삭제할까요?{blocked_message}\n\n"
            "해당 상품의 번역·옵션 이미지 작업 폴더도 함께 삭제되며 복구할 수 없습니다.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        deleted_count = 0
        errors: list[str] = []
        for job in deletable_jobs:
            try:
                delete_translation_job(
                    job_id=job.job_id,
                    output_root=TRANSLATION_JOB_ROOT,
                )
            except (OSError, ValueError) as exc:
                errors.append(f"{job.product_id or job.search_value}: {exc}")
                continue
            self.batch_queue.remove(job.job_id)
            deleted_count += 1

        self._persist_batch_queue()
        self._refresh_batch_table()
        self._refresh_translation_previews()
        if errors:
            QMessageBox.warning(
                self,
                "일부 상품 삭제 실패",
                f"{deleted_count}개는 삭제했고 {len(errors)}개는 삭제하지 못했습니다.\n\n"
                + "\n".join(errors[:5]),
            )
        else:
            self.statusBar().showMessage(
                f"체크한 상품 {deleted_count}개를 완전히 삭제했습니다.", 5000
            )

    def _analyze(self) -> None:
        self.result = parse_coupang_url(self.url_input.text())
        self.id_table.setRowCount(len(self.result.identifiers))
        for row, identifier in enumerate(self.result.identifiers):
            for column, value in enumerate(
                (identifier.kind, identifier.value, identifier.source, identifier.meaning)
            ):
                self.id_table.setItem(row, column, QTableWidgetItem(value))
        self.id_table.resizeColumnsToContents()

        self.candidate_combo.clear()
        for item in self.result.search_candidates():
            self.candidate_combo.addItem(f"{item.kind}: {item.value}", item.value)
        if self.result.is_coupang_url and self.result.normalized_url:
            self.candidate_combo.addItem("상품 URL 전체", self.result.normalized_url)

        if not self.result.is_coupang_url:
            self.parse_message.setText("분석 실패: " + " ".join(self.result.warnings))
            self.parse_message.setStyleSheet("color:#b02a37;")
        else:
            message = f"확인 가능한 ID {len(self.result.identifiers)}개를 URL에서 추출했습니다."
            if self.result.warnings:
                message += " 주의: " + " ".join(self.result.warnings)
            self.parse_message.setText(message)
            self.parse_message.setStyleSheet("color:#0f5132;")
        self._refresh_summary()

    def _open_wing(self) -> None:
        target = self.wing_url_input.text().strip() or WING_REGISTRATION_URL
        try:
            parts = urlsplit(target)
        except ValueError:
            parts = None
        if not parts or parts.scheme.lower() != "https" or parts.hostname != "wing.coupang.com":
            QMessageBox.warning(self, "주소 확인", "wing.coupang.com의 HTTPS 주소만 열 수 있습니다.")
            return
        try:
            launch_chrome(target)
        except ChromeLaunchError as exc:
            self._set_browser_status(
                f"Chrome/WING 화면을 열지 못했습니다. {exc}", success=False
            )
            QMessageBox.warning(self, "Chrome/WING 준비 실패", str(exc))
        else:
            self._set_browser_status(
                "Chrome에서 WING 화면을 열었습니다. 자동 입력 중이라는 뜻은 아닙니다.",
                success=True,
            )

    def _copy_candidate(self) -> None:
        value = self.candidate_combo.currentData()
        if not value:
            QMessageBox.information(self, "검색값 없음", "먼저 상품 URL을 분석하세요.")
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(value)
        self.statusBar().showMessage(f"검색값 {value}을(를) 복사했습니다.", 4000)

    def _missing_requirements(self) -> list[str]:
        missing: list[str] = []
        if not self.result or not self.result.is_coupang_url or not self.result.search_candidates():
            missing.append("유효한 쿠팡 URL과 검색 후보")
        missing.extend(label for key, label in CHECKS if not self.checkboxes[key].isChecked())
        if not self.required_fields.toPlainText().strip():
            missing.append("등록폼 필수 항목 메모")
        missing.extend(self._registration_plan()["missing_fields"])
        return missing

    def _selected_shipping_fee_type(self) -> str | None:
        if self.free_shipping_button.isChecked():
            return "free"
        if self.cod_shipping_button.isChecked():
            return "cash_on_delivery"
        return None

    def _select_fixed_page_top_images(self) -> None:
        self._select_fixed_page_images(
            "top", "상단 고정페이지 이미지 삽입/변경"
        )

    def _select_fixed_page_bottom_images(self) -> None:
        self._select_fixed_page_images(
            "bottom", "하단 고정페이지 이미지 삽입/변경"
        )

    def _select_fixed_page_images(self, placement: str, title: str) -> None:
        current_paths = (
            self.fixed_page_top_paths
            if placement == "top"
            else self.fixed_page_bottom_paths
        )
        start_dir = (
            str(Path(current_paths[0]).parent) if current_paths else str(BASE_DIR)
        )
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            title,
            start_dir,
            "이미지 파일 (*.jpg *.jpeg *.png *.webp)",
        )
        if not paths:
            return
        if placement == "top":
            self.fixed_page_top_paths = paths
        else:
            self.fixed_page_bottom_paths = paths
        self._sync_fixed_page_controls()
        if self._save_fixed_page_settings():
            position = "상단" if placement == "top" else "하단"
            self.statusBar().showMessage(
                f"{position} 고정페이지 이미지 {len(paths)}장을 저장했습니다.",
                5000,
            )

    @staticmethod
    def _fixed_page_assets(editor: QTextEdit) -> list[str]:
        return [
            line.strip()
            for line in editor.toPlainText().splitlines()
            if line.strip()
        ]

    def _own_seller_marker_values(self) -> list[str]:
        return [
            marker.strip()
            for marker in self.own_seller_markers.text().split(",")
            if marker.strip()
        ]

    def _registration_plan(self) -> dict:
        batch_job_id = self.active_batch_job_id or (
            self._selected_batch_job_id() if hasattr(self, "batch_table") else None
        )
        batch_job = self.batch_queue.get(batch_job_id) if batch_job_id else None
        return build_registration_plan(
            catalog_match_price=self.catalog_match_price.value(),
            stock_quantity=self.stock_quantity.value(),
            shipping_lead_days=self.shipping_lead_days.value(),
            shipping_fee_type=self._selected_shipping_fee_type(),
            country_of_origin=self.country_of_origin.text(),
            notice_category=self.notice_category.text(),
            notice_detail=self.notice_detail.text(),
            kc_purchase_agent_checked=self.kc_purchase_agent.isChecked(),
            detail_entry_mode=DEFAULT_DETAIL_ENTRY_MODE,
            remove_other_seller_sections=(
                self.remove_other_seller_sections.isChecked()
            ),
            fixed_page_top_assets=self._fixed_page_assets(
                self.fixed_page_top_files
            ),
            fixed_page_bottom_assets=self._fixed_page_assets(
                self.fixed_page_bottom_files
            ),
            own_seller_markers=self._own_seller_marker_values(),
            detail_preview_required=self.detail_preview_required.isChecked(),
            source_detail_assets=(
                batch_job.source_detail_image_paths if batch_job else []
            ),
            translated_detail_assets=(
                batch_job.translated_detail_image_paths if batch_job else []
            ),
            source_option_assets=(
                batch_job.source_option_image_paths if batch_job else []
            ),
            watermarked_option_assets=(
                batch_job.watermarked_option_image_paths if batch_job else []
            ),
            watermark_enabled=self.watermark_enabled.isChecked(),
            translation_mode=(batch_job.translation_mode if batch_job else "auto_ko"),
            translation_status=(
                batch_job.translation_status if batch_job else "not_checked"
            ),
            translation_review_approved=(
                batch_job.translation_review_approved if batch_job else False
            ),
        )

    def _refresh_summary(self) -> None:
        missing = self._missing_requirements()
        if not missing:
            self.summary_label.setText(
                "✅ Phase 0 성공 조건을 모두 표시했습니다. 결과를 저장하면 실제 업무 검증 성공으로 기록됩니다."
            )
            self.summary_label.setStyleSheet("color:#0f5132; font-weight:bold;")
        else:
            self.summary_label.setText(
                f"검증 진행 중 — 성공 판정까지 {len(missing)}개 항목이 남았습니다."
            )
            self.summary_label.setStyleSheet("color:#664d03;")

    def _save(self) -> None:
        if not self.result:
            QMessageBox.information(self, "URL 분석 필요", "먼저 상품 URL을 분석하세요.")
            return

        missing = self._missing_requirements()
        failure_label = self.failure_mode.currentText()
        has_failure = failure_label != "실패 없음/검증 중"
        if not missing:
            outcome = "success"
        elif has_failure:
            outcome = "blocked" if failure_label in {"로그인·권한 차단", "CAPTCHA·2차 인증 대기"} else "failed"
        else:
            outcome = "incomplete"

        checks = {key: checkbox.isChecked() for key, checkbox in self.checkboxes.items()}
        payload = {
            "schema_version": 2,
            "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "outcome": outcome,
            "product": {
                "source_url": self.result.normalized_url,
                "product_id": self.result.value_for("productId"),
                "vendor_item_id": self.result.value_for("vendorItemId"),
                "item_id": self.result.value_for("itemId"),
                "url_analysis": self.result.to_dict(),
            },
            "wing_observation": {
                "screen_url_without_query": strip_url_query(self.wing_url_input.text()),
                "successful_search_id_type": self.used_id_combo.currentText(),
                "catalog_view_count_display": self.catalog_view_count.text(),
                "catalog_rating_display": self.catalog_rating_display.text(),
                "catalog_delivery_type": self.catalog_delivery_type.text(),
                "catalog_comparison_notes": self.catalog_notes.toPlainText(),
                "required_registration_fields": self.required_fields.toPlainText(),
                "failure_type": failure_label,
                "failure_notes": self.failure_notes.toPlainText(),
            },
            "registration_plan": self._registration_plan(),
            "manual_checks": checks,
            "missing_success_requirements": missing,
            "safety": {
                "registration_action_implemented": False,
                "credentials_or_browser_session_stored": False,
                "captcha_or_2fa_bypass_implemented": False,
            },
        }
        path = save_report(payload, REPORT_DIR)
        QMessageBox.information(
            self,
            "저장 완료",
            f"결과: {outcome}\n\n저장 위치:\n{path}",
        )
