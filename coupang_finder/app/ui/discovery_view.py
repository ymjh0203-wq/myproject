import json

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.config.settings import PARTNERS_SEARCH_HOURLY_LIMIT
from app.core.analyzers.pipeline import run_filters
from app.core.collector.partners_api_client import (
    PartnersApiCredentialsMissing,
    PartnersApiError,
    search_products,
)
from app.core.collector.product_url import parse_product_url
from app.db import repository

SEARCH_ENDPOINT = "products/search"
PENDING_KEYWORDS_SETTING = "pending_keywords"


class DiscoveryView(QWidget):
    """1. 상품발굴 화면.

    Phase 2: 키워드 하나로 수동 검색.
    Phase 5: 여러 키워드를 큐에 넣고 자동으로 순회(하루 최대 분석 수 /
    시간당 API 호출 제한을 지키며 일정 간격으로 실행), 중단해도 남은
    큐는 저장되어 있어 다음에 이어서 진행할 수 있다.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        repository.close_stale_running_discovery_runs()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._process_next_auto_keyword)
        self._pending_keywords: list[str] = []
        self._current_run_id: int | None = None

        # ---- 수동 검색 (키워드 1개) ----
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("검색할 키워드 (예: 접이식 캠핑 테이블)")
        self.keyword_input.returnPressed.connect(self.run_manual_search)

        self.limit_input = QSpinBox()
        self.limit_input.setRange(1, 50)
        self.limit_input.setValue(20)

        self.manual_search_button = QPushButton("이 키워드로 검색")
        self.manual_search_button.clicked.connect(self.run_manual_search)

        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("키워드"))
        input_row.addWidget(self.keyword_input, stretch=1)
        input_row.addWidget(QLabel("최대 개수"))
        input_row.addWidget(self.limit_input)
        input_row.addWidget(self.manual_search_button)

        # ---- 자동 발굴 (키워드 큐 순회) ----
        self.auto_keyword_input = QTextEdit()
        self.auto_keyword_input.setPlaceholderText("자동으로 순회할 키워드를 한 줄에 하나씩 입력하세요")
        self.auto_keyword_input.setFixedHeight(80)

        self.start_button = QPushButton("발굴 시작 (자동 순회)")
        self.start_button.clicked.connect(self.start_auto_discovery)
        self.stop_button = QPushButton("발굴 중지")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(lambda: self.stop_auto_discovery("사용자 중지"))

        auto_box = QGroupBox("자동 발굴 (키워드 큐를 설정한 간격으로 순차 검색)")
        auto_layout = QVBoxLayout(auto_box)
        auto_layout.addWidget(self.auto_keyword_input)
        auto_button_row = QHBoxLayout()
        auto_button_row.addWidget(self.start_button)
        auto_button_row.addWidget(self.stop_button)
        auto_button_row.addStretch()
        auto_layout.addLayout(auto_button_row)

        # ---- 진행 상태 ----
        self.status_label = QLabel("대기 중")
        self.current_product_label = QLabel("-")
        self.found_count_label = QLabel("0")
        self.excluded_count_label = QLabel("0")
        self.s_grade_count_label = QLabel("0")
        self.rate_limit_label = QLabel(f"0 / {PARTNERS_SEARCH_HOURLY_LIMIT} (이번 시간 사용)")
        self.daily_count_label = QLabel("0 / 0 (오늘 분석)")

        stats_box = QGroupBox("진행 상태")
        stats_layout = QGridLayout(stats_box)
        stats_layout.addWidget(QLabel("상태"), 0, 0)
        stats_layout.addWidget(self.status_label, 0, 1)
        stats_layout.addWidget(QLabel("마지막/현재 키워드"), 1, 0)
        stats_layout.addWidget(self.current_product_label, 1, 1)
        stats_layout.addWidget(QLabel("전체 발견 상품 수"), 2, 0)
        stats_layout.addWidget(self.found_count_label, 2, 1)
        stats_layout.addWidget(QLabel("제외 상품 수"), 3, 0)
        stats_layout.addWidget(self.excluded_count_label, 3, 1)
        stats_layout.addWidget(QLabel("S등급 수"), 4, 0)
        stats_layout.addWidget(self.s_grade_count_label, 4, 1)
        stats_layout.addWidget(QLabel("API 호출량(시간당)"), 5, 0)
        stats_layout.addWidget(self.rate_limit_label, 5, 1)
        stats_layout.addWidget(QLabel("오늘 분석 상품 수"), 6, 0)
        stats_layout.addWidget(self.daily_count_label, 6, 1)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("발굴 로그가 여기에 표시됩니다.")

        layout = QVBoxLayout(self)
        layout.addLayout(input_row)
        layout.addWidget(auto_box)
        layout.addWidget(stats_box)
        layout.addWidget(QLabel("로그"))
        layout.addWidget(self.log_view)

        self._load_pending_keywords()
        self.refresh()

    # ---------- 공통 ----------

    def refresh(self):
        self.found_count_label.setText(str(repository.count_products()))
        self.excluded_count_label.setText(str(repository.count_products(status="제외")))
        self.s_grade_count_label.setText(str(repository.count_products(grade="S등급")))
        used = repository.count_api_calls_last_hour(SEARCH_ENDPOINT)
        self.rate_limit_label.setText(f"{used} / {PARTNERS_SEARCH_HOURLY_LIMIT} (이번 시간 사용)")
        today = repository.count_products_discovered_today()
        daily_max = int(repository.get_setting("daily_max_analyze", "150"))
        self.daily_count_label.setText(f"{today} / {daily_max} (오늘 분석)")

    def _log(self, message: str) -> None:
        self.log_view.append(message)

    def _daily_cap_reached(self) -> bool:
        daily_max = int(repository.get_setting("daily_max_analyze", "150"))
        return repository.count_products_discovered_today() >= daily_max

    def _search_and_process(self, keyword: str, limit: int, run_id: int | None = None) -> int:
        """키워드 하나를 검색해 저장·필터링하고, 신규로 추가된 상품 수를 반환한다."""
        results = search_products(keyword, limit=limit)
        repository.log_api_call(SEARCH_ENDPOINT, keyword, len(results))

        new_count = 0
        excluded_count = 0
        for item in results:
            product_id = item.get("productId")
            if not product_id:
                continue
            url_info = parse_product_url(item.get("productUrl", ""))
            row_id, is_new = repository.upsert_product_from_search_result(
                coupang_product_id=str(product_id),
                product_name=item.get("productName", ""),
                product_url=item.get("productUrl", ""),
                price=item.get("productPrice"),
                vendor_item_id=url_info["vendor_item_id"],
                search_rank=item.get("rank"),
                is_rocket=item.get("isRocket"),
            )
            name = item.get("productName", "(이름 없음)")
            if is_new:
                new_count += 1
                result = run_filters(row_id)
                if result["status"] == "제외":
                    excluded_count += 1
                self._log(
                    f"  - {name} / {item.get('productPrice', '-')}원 "
                    f"→ 브랜드:{result['brand_risk_level']} 효자상품:{result['hyoja_status']} "
                    f"옵션:{result['option_quality']} 상태:{result['status']}"
                    + (f" ({result['exclusion_reason']})" if result["exclusion_reason"] else "")
                )
            else:
                self._log(f"  - {name} (이미 있는 상품, 확인일만 갱신)")

        if run_id is not None:
            repository.update_discovery_run_counts(
                run_id, analyzed_delta=len(results), found_delta=new_count, excluded_delta=excluded_count
            )

        self._log(f"[검색 완료] '{keyword}' — {len(results)}건 응답, 신규 {new_count}건 추가")
        self.refresh()
        return new_count

    # ---------- 수동 검색 ----------

    def run_manual_search(self):
        keyword = self.keyword_input.text().strip()
        if not keyword:
            QMessageBox.information(self, "안내", "검색할 키워드를 입력해주세요.")
            return
        if self._daily_cap_reached():
            QMessageBox.warning(self, "일일 한도", "오늘 최대 분석 상품 수에 도달했습니다. 내일 다시 시도해주세요.")
            return
        if repository.count_api_calls_last_hour(SEARCH_ENDPOINT) >= PARTNERS_SEARCH_HOURLY_LIMIT:
            QMessageBox.warning(
                self,
                "호출 제한",
                f"쿠팡파트너스 검색 API는 시간당 최대 {PARTNERS_SEARCH_HOURLY_LIMIT}회만 호출할 수 있습니다.\n잠시 후 다시 시도해주세요.",
            )
            return

        self.status_label.setText("검색 중...")
        self.current_product_label.setText(keyword)
        self._log(f"[수동 검색] 키워드='{keyword}', 최대 {self.limit_input.value()}개")
        try:
            self._search_and_process(keyword, self.limit_input.value())
        except PartnersApiCredentialsMissing as exc:
            self._log(f"[오류] {exc}")
            QMessageBox.warning(self, "API 키 없음", str(exc))
        except PartnersApiError as exc:
            self._log(f"[오류] {exc}")
            QMessageBox.warning(self, "API 오류", str(exc))
        finally:
            self.status_label.setText("대기 중")

    # ---------- 자동 발굴 (키워드 큐) ----------

    def _load_pending_keywords(self):
        saved = repository.get_setting(PENDING_KEYWORDS_SETTING, "")
        if not saved:
            return
        try:
            keywords = json.loads(saved)
        except ValueError:
            keywords = []
        if keywords:
            self._pending_keywords = keywords
            self.auto_keyword_input.setPlainText("\n".join(keywords))
            self._log(f"[알림] 이전에 중단된 자동 발굴 큐가 {len(keywords)}개 남아있습니다. '발굴 시작'을 누르면 이어서 진행합니다.")

    def _save_pending_keywords(self):
        repository.set_setting(PENDING_KEYWORDS_SETTING, json.dumps(self._pending_keywords, ensure_ascii=False))

    def start_auto_discovery(self):
        if not self._pending_keywords:
            text = self.auto_keyword_input.toPlainText().strip()
            keywords = [line.strip() for line in text.splitlines() if line.strip()]
            if not keywords:
                QMessageBox.information(self, "안내", "자동으로 검색할 키워드를 한 줄에 하나씩 입력해주세요.")
                return
            self._pending_keywords = keywords
            self._save_pending_keywords()

        if self._daily_cap_reached():
            QMessageBox.warning(self, "일일 한도", "오늘 최대 분석 상품 수에 이미 도달했습니다. 내일 다시 시도해주세요.")
            return

        interval_sec = int(repository.get_setting("api_request_interval_sec", "450"))
        self._current_run_id = repository.start_discovery_run("키워드 자동 순회")
        self.status_label.setText("자동 발굴 중")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._log(f"[자동 발굴 시작] 키워드 {len(self._pending_keywords)}개, 요청 간격 {interval_sec}초")

        self._process_next_auto_keyword()
        self._timer.start(interval_sec * 1000)

    def stop_auto_discovery(self, reason: str):
        self._timer.stop()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_label.setText("대기 중")
        if self._current_run_id is not None:
            repository.finish_discovery_run(self._current_run_id, "중단", reason)
            self._current_run_id = None
        self._log(f"[자동 발굴 중지] {reason}")

    def _process_next_auto_keyword(self):
        if self._daily_cap_reached():
            self._log("[자동 발굴 일시중지] 오늘 최대 분석 상품 수에 도달했습니다. 내일 이어서 진행하세요.")
            self.stop_auto_discovery("일일 분석 한도 도달")
            return

        if repository.count_api_calls_last_hour(SEARCH_ENDPOINT) >= PARTNERS_SEARCH_HOURLY_LIMIT:
            self._log("[대기] 이번 시간 API 호출 한도에 도달해 다음 주기에 다시 시도합니다.")
            return

        if not self._pending_keywords:
            self._log("[자동 발굴 완료] 큐에 있던 키워드를 모두 처리했습니다.")
            self.stop_auto_discovery("완료")
            return

        keyword = self._pending_keywords.pop(0)
        self._save_pending_keywords()
        self.current_product_label.setText(f"{keyword} (남은 큐 {len(self._pending_keywords)}개)")

        try:
            self._search_and_process(keyword, 20, run_id=self._current_run_id)
        except PartnersApiCredentialsMissing as exc:
            self._log(f"[오류] {exc}")
            self.stop_auto_discovery("API 키 오류")
        except PartnersApiError as exc:
            self._log(f"[오류] {exc}")
