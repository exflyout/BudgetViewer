from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from delegate import GroupBoxDelegate
from exporter import export_current_view_to_excel
from loader import (
    ANALYSIS_MODE_3,
    ANALYSIS_MODE_4,
    ANALYSIS_MODE_AUTO,
    ParseResult,
    business_columns_for_mode,
    load_and_parse_excel,
)
from tree_model import BudgetTreeBuilder, BuildOptions

warnings.filterwarnings(
    "ignore",
    message="Workbook contains no default style, apply openpyxl's default",
    category=UserWarning,
)

APP_VERSION = "1.0"
APP_TITLE = f"예산현액 뷰어 (v{APP_VERSION}) — 일상경비교부액"


MODE_LABELS = {
    ANALYSIS_MODE_AUTO: "사업명 기준 자동분류(추천)",
    ANALYSIS_MODE_3: "3세부 기준",
    ANALYSIS_MODE_4: "4세부 기준",
}


def resource_path(relative_path):
    """ PyInstaller의 --onefile 모드에서 리소스를 찾기 위한 경로 변환 함수 """
    try:
        import sys
        import os
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def check_for_updates(current_version: str, parent=None):
    """ 
    서버의 최신 버전을 확인합니다. 
    지금은 구조만 잡혀 있으며, 실제 URL이 결정되면 활성화됩니다.
    """
    import urllib.request
    import json
    
    # 예시 URL (추후 사용자의 GitHub gist 또는 raw content 주소로 변경)
    UPDATE_URL = "https://raw.githubusercontent.com/username/repo/main/version.json"
    
    try:
        # 💡 실제 네트워크 연결 시 타임아웃을 짧게 설정하여 사용자 대기 최소화
        with urllib.request.urlopen(UPDATE_URL, timeout=2) as response:
            data = json.loads(response.read().decode())
            latest_version = data.get("version", current_version)
            download_url = data.get("url", "")
            
            if latest_version > current_version:
                msg = f"새로운 버전({latest_version})이 출시되었습니다.\n\n업데이트 하시겠습니까?"
                if QMessageBox.question(parent, "업데이트 알림", msg) == QMessageBox.Yes:
                    import webbrowser
                    webbrowser.open(download_url)
    except Exception:
        # 네트워크 오류 등은 사용자에게 알리지 않고 조용히 넘어갑니다.
        pass


def _msg_error(parent, title: str, text: str):
    QMessageBox.critical(parent, title, text)


def _fmt_money(x: float) -> str:
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "0"


class FilterList(QFrame):
    def __init__(self, title: str, search_placeholder: str = "검색…"):
        super().__init__()
        self.setObjectName("FilterCard")
        self._suppress = False
        self._items: List[Tuple[str, str]] = []
        self._changed_cb: Optional[Callable[[], None]] = None
        self._checks: List[QCheckBox] = []
        self._base_min_height = 220

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("FilterTitle")
        f = QFont()
        f.setBold(True)
        f.setPointSize(10)
        self.title_label.setFont(f)
        root.addWidget(self.title_label)

        self.search = QLineEdit()
        self.search.setPlaceholderText(search_placeholder)
        root.addWidget(self.search)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4) # 간격 축소
        self.btn_all = QPushButton("전체 선택")
        self.btn_none = QPushButton("전체 해제")
        self.btn_search_only = QPushButton("검색결과만")
        
        for btn in (self.btn_all, self.btn_none, self.btn_search_only):
            # 글자 크기를 약간 줄여서 3개가 잘 보이게 함
            btn.setStyleSheet("font-size: 11px; padding: 4px;")
            btn_layout.addWidget(btn)
            
        root.addLayout(btn_layout)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scroll.setMinimumHeight(220)
        self.scroll.setObjectName("FilterListScroll")

        self._inner = QWidget()
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setContentsMargins(0, 0, 0, 12)
        self._inner_layout.setSpacing(6)
        self._inner_layout.addStretch(1)
        self.scroll.setWidget(self._inner)
        root.addWidget(self.scroll, 1)

        self.search.textChanged.connect(self._apply_search)
        self.btn_all.clicked.connect(self._on_all_clicked)
        self.btn_none.clicked.connect(self._on_none_clicked)
        self.btn_search_only.clicked.connect(self._on_search_only_clicked)

    def on_changed(self, cb: Callable[[], None]):
        self._changed_cb = cb

    def set_title(self, text: str):
        self.title_label.setText(text)

    def set_search_placeholder(self, text: str):
        self.search.setPlaceholderText(text)

    def set_base_min_height(self, h: int):
        self._base_min_height = h
        self.scroll.setMinimumHeight(h)

    def set_items(
        self,
        items: List[Tuple[str, str]],
        checked_keys: Optional[Set[str]] = None,
        default_checked: bool = True,
    ):
        self._items = list(items)
        self._suppress = True
        try:
            self._clear_checks()
            for display, key in self._items:
                cb = QCheckBox(display, self._inner)
                cb.setProperty("filterKey", key)
                cb.setProperty("rawText", display)
                cb.setChecked(default_checked if checked_keys is None else (key in checked_keys))
                cb.stateChanged.connect(self._on_checkbox_changed)
                cb.setMinimumHeight(36)
                cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                self._checks.append(cb)
                self._inner_layout.insertWidget(self._inner_layout.count() - 1, cb)
                self._update_checkbox_style(cb)
        finally:
            self._suppress = False
        self._apply_search()
        self._adjust_scroll_height()

    def selected_keys(self) -> Set[str]:
        return {str(cb.property("filterKey")) for cb in self._checks if cb.isChecked()}

    def all_keys(self) -> Set[str]:
        return {key for _, key in self._items}

    def display_texts_for_keys(self, keys: Set[str]) -> List[str]:
        return [display for display, key in self._items if key in keys]

    def _on_all_clicked(self):
        self._suppress = True
        try:
            for cb in self._checks:
                cb.setChecked(True)
                self._update_checkbox_style(cb)
        finally:
            self._suppress = False
        self._emit_changed()

    def _on_none_clicked(self):
        self._suppress = True
        try:
            for cb in self._checks:
                cb.setChecked(False)
                self._update_checkbox_style(cb)
        finally:
            self._suppress = False
        self._emit_changed()

    def _on_search_only_clicked(self):
        self._suppress = True
        try:
            for cb in self._checks:
                if cb.isVisible():
                    cb.setChecked(True)
                else:
                    cb.setChecked(False)
                self._update_checkbox_style(cb)
        finally:
            self._suppress = False
        self._emit_changed()

    def _clear_checks(self):
        while self._inner_layout.count() > 1:
            item = self._inner_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._checks.clear()

    def _apply_search(self):
        q = (self.search.text() or "").strip().lower()
        visible_count = 0
        for cb in self._checks:
            raw = str(cb.property("rawText") or cb.text()).lower()
            hidden = bool(q) and (q not in raw)
            cb.setVisible(not hidden)
            if not hidden:
                visible_count += 1
        self._adjust_scroll_height(visible_count)

    def _adjust_scroll_height(self, visible_count: Optional[int] = None):
        if visible_count is None:
            visible_count = sum(1 for cb in self._checks if cb.isVisible())
        pref = min(max(self._base_min_height, visible_count * 42 + 10), 420)
        self.scroll.setMinimumHeight(pref)

    def _update_checkbox_style(self, cb: QCheckBox):
        if cb.isChecked():
            cb.setStyleSheet(
                """
                QCheckBox {
                    background: #dbeafe;
                    border: 1px solid #93c5fd;
                    border-radius: 8px;
                    padding: 6px 8px;
                    font-weight: 700;
                    color: #0f172a;
                    spacing: 10px;
                }
                QCheckBox::indicator { width: 18px; height: 18px; }
                """
            )
        else:
            cb.setStyleSheet(
                """
                QCheckBox {
                    background: transparent;
                    border: 1px solid transparent;
                    border-radius: 8px;
                    padding: 6px 8px;
                    font-weight: 500;
                    color: #334155;
                    spacing: 10px;
                }
                QCheckBox::indicator { width: 18px; height: 18px; }
                """
            )

    def _on_checkbox_changed(self, _state: int):
        cb = self.sender()
        if isinstance(cb, QCheckBox):
            self._update_checkbox_style(cb)
        if self._suppress:
            return
        self._emit_changed()

    def _emit_changed(self):
        if self._changed_cb:
            self._changed_cb()


class ManualDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("이용방법")
        self.resize(1000, 650)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4) # 제목과 소개글 사이 간격을 최소화 (8 -> 4)
        
        title = QLabel("예산현액 파일 다운로드 및 불러오기 안내")
        f = QFont()
        f.setPointSize(17)
        f.setBold(True)
        title.setFont(f)
        header_layout.addWidget(title)

        intro = QLabel(
            "k에듀파인에서 예산현액 파일을 내려받아, 이 프로그램으로 일상경비교부액 기준 "
            "교부액 / 지출액 / 잔액을 확인하는 절차입니다."
        )
        iff = QFont()
        iff.setPointSize(13)
        intro.setFont(iff)
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#475569;")
        header_layout.addWidget(intro)
        root.addLayout(header_layout)

        steps_card = QFrame()
        steps_card.setObjectName("FilterCard")
        steps_layout = QVBoxLayout(steps_card)
        steps_layout.setContentsMargins(16, 14, 16, 14)
        steps_layout.setSpacing(8) # 2. 이용 순서 카드 내부 간격 8px로 통일 (이미 8px)

        steps_title = QLabel("이용 순서")
        tf = QFont()
        tf.setBold(True)
        tf.setPointSize(15)
        steps_title.setFont(tf)
        steps_layout.addWidget(steps_title)

        steps = [
            "1. [k에듀파인] 접속",
            "2. [재정사업관리] 메뉴 이동",
            "3. [사업관리카드] 선택",
            "4. [예산현액] 탭 선택",
            "5. 상단 옵션에서 <b style='color:#2563eb;'>[일상경비 포함] 체크</b>",
            "6. 우측의 [파일 다운] 버튼으로 엑셀 저장",
            "7. 본 프로그램에서 [엑셀 열기]로 저장한 파일 불러오기",
        ]
        sf = QFont()
        sf.setPointSize(13) # 12 -> 13
        
        for step in steps:
            lb = QLabel(step)
            lb.setFont(sf)
            lb.setTextFormat(Qt.RichText) # 💡 HTML 태그 해석 활성화
            lb.setWordWrap(True)
            lb.setStyleSheet("border:none; color:#0f172a;")
            steps_layout.addWidget(lb)

        tip = QLabel(
            "팁: 왼쪽의 분석 모드에서 자동분류 / 3세부 기준 / 4세부 기준을 바꿔가며 "
            "같은 사업명을 예산코드별로 비교할 수 있습니다."
        )
        tip.setStyleSheet("border:none; color:#64748b;")
        steps_layout.addWidget(tip)
        # 카드 내부는 촘촘하게 (addStretch 제거)
        root.addWidget(steps_card)

        image_card = QFrame()
        image_card.setObjectName("FilterCard")
        image_layout = QVBoxLayout(image_card)
        image_layout.setContentsMargins(10, 10, 10, 10)
        image_layout.setSpacing(6)

        image_title = QLabel("참고 화면")
        image_title.setStyleSheet("border:none; font-weight:700; color:#334155;")
        image_layout.addWidget(image_title)

        image_label = QLabel()
        image_label.setAlignment(Qt.AlignCenter)
        image_label.setStyleSheet("border:none; background:#ffffff;")
        
        # 💡 PyInstaller 리소스 경로 대응
        actual_img_path = resource_path("kedu_manual.png")
        
        if Path(actual_img_path).exists():
            pix = QPixmap(str(actual_img_path))
            if not pix.isNull():
                pix = pix.scaled(920, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                image_label.setPixmap(pix)
                image_label.setFixedHeight(pix.height() + 4)
            else:
                image_label.setText("안내 이미지를 불러오지 못했습니다.")
                image_label.setFixedHeight(120)
        else:
            image_label.setText("안내 이미지 파일(kedu_manual.png)을 찾지 못했습니다.")
            image_label.setFixedHeight(120)
        image_layout.addWidget(image_label)
        # 카드 내부는 촘큼하게 (addStretch 제거)
        root.addWidget(image_card)
        
        # 💡 [핵심] 하단에 Stretch를 추가하여 위의 모든 요소가 위로 밀집되게 함
        root.addStretch(1)

        # 💡 버전 정보 표시
        version_label = QLabel(f"Version {APP_VERSION}")
        version_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        root.addWidget(version_label, alignment=Qt.AlignLeft)

        btn_close = QPushButton("닫기")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, alignment=Qt.AlignRight)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1750, 960)

        self.parsed: Optional[ParseResult] = None
        self._refresh_pending = False

        self._build_ui()
        self._apply_style()
        self._update_analysis_mode_ui()

        # 🚀 시작 2초 후 업데이트 확인 (UX 레이턴시 고려)
        QTimer.singleShot(2000, lambda: check_for_updates(APP_VERSION, self))

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("예산현액 뷰어")
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        title.setFont(f)
        top.addWidget(title)
        top.addStretch(1)
        self.btn_help = QPushButton("이용방법")
        self.btn_open = QPushButton("엑셀 열기")
        self.btn_export = QPushButton("현재 화면 엑셀 저장")
        self.btn_export.setEnabled(False)
        top.addWidget(self.btn_help)
        top.addWidget(self.btn_open)
        top.addWidget(self.btn_export)
        outer.addLayout(top)

        subtitle = QLabel("일상경비교부액 기준으로 교부액 / 지출액 / 잔액을 조회합니다.")
        subtitle.setStyleSheet("color:#64748b;")
        outer.addWidget(subtitle)

        kpi_row = QHBoxLayout()
        self.kpi_budget = self._kpi_card("총 예산현액", "0")
        self.kpi_grant = self._kpi_card("교부액", "0")
        self.kpi_spent = self._kpi_card("지출액", "0")
        self.kpi_bal = self._kpi_card("잔액", "0")
        self.kpi_basis = QLabel("기준: -")
        self.kpi_basis.setStyleSheet("color:#64748b;")
        kpi_row.addWidget(self.kpi_budget)
        kpi_row.addWidget(self.kpi_grant)
        kpi_row.addWidget(self.kpi_spent)
        kpi_row.addWidget(self.kpi_bal)
        kpi_row.addStretch(1)
        kpi_row.addWidget(self.kpi_basis)
        outer.addLayout(kpi_row)

        self.filter_chips = QLabel("")
        self.filter_chips.setWordWrap(True)
        self.filter_chips.setStyleSheet("color:#334155;")
        outer.addWidget(self.filter_chips)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        left_scroll.setMinimumWidth(420)
        left_scroll.setMaximumWidth(500)

        left_content = QWidget()
        left_content.setObjectName("LeftPanel")
        left_layout = QVBoxLayout(left_content)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(16)

        self.tabs = QTabWidget()
        self.tabs.addTab(QWidget(), "사업 기준 보기")
        self.tabs.addTab(QWidget(), "예산코드 기준 보기")
        self.tabs.currentChanged.connect(self._schedule_refresh)
        left_layout.addWidget(self.tabs)

        mode_card = QFrame()
        mode_card.setObjectName("FilterCard")
        mode_layout = QVBoxLayout(mode_card)
        mode_layout.setContentsMargins(12, 12, 12, 12)
        mode_layout.setSpacing(8)

        mode_title = QLabel("분석 모드")
        mode_title.setObjectName("FilterTitle")
        mode_layout.addWidget(mode_title)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)

        self.btn_mode_auto = QPushButton("1. 사업명 기준 자동분류(추천)")
        self.btn_mode_3 = QPushButton("2. 3세부 기준")
        self.btn_mode_4 = QPushButton("3. 4세부 기준")
        for btn, mode in (
            (self.btn_mode_auto, ANALYSIS_MODE_AUTO),
            (self.btn_mode_3, ANALYSIS_MODE_3),
            (self.btn_mode_4, ANALYSIS_MODE_4),
        ):
            btn.setCheckable(True)
            btn.setProperty("analysisMode", mode)
            btn.clicked.connect(self._on_analysis_mode_changed)
            self.mode_group.addButton(btn)
            mode_layout.addWidget(btn)
        self.btn_mode_auto.setChecked(True)

        self.mode_hint = QLabel("자동 구조 분석으로 3세부/4세부를 추천합니다.")
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setStyleSheet("color:#64748b;")
        mode_layout.addWidget(self.mode_hint)
        left_layout.addWidget(mode_card)

        self.l1_list = FilterList("세세부사업 선택", "세세부사업 검색…")
        self.l1_list.set_base_min_height(220)
        self.l1_list.on_changed(self._on_l1_changed)
        left_layout.addWidget(self.l1_list)

        self.biz_list = FilterList("사업명 선택", "사업명 검색…")
        self.biz_list.set_base_min_height(360)
        self.biz_list.on_changed(self._schedule_refresh)
        left_layout.addWidget(self.biz_list)

        self.code_list = FilterList("예산코드 선택", "예산코드 검색…")
        self.code_list.set_base_min_height(240)
        self.code_list.on_changed(self._schedule_refresh)
        left_layout.addWidget(self.code_list)

        left_layout.addStretch(1)

        left_scroll.setWidget(left_content)
        splitter.addWidget(left_scroll)
        splitter.setStretchFactor(0, 0)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)
        self.lbl_font = QLabel("본문 글자 크기")
        self.spin_font = QSpinBox()
        self.spin_font.setRange(9, 14)
        self.spin_font.setValue(10)
        self.spin_font.setToolTip("우측 본문 트리의 글자 크기를 조절합니다.")
        self.btn_font_default = QPushButton("기본값")
        self.btn_font_default.setFixedWidth(74)
        ctrl_row.addWidget(self.lbl_font)
        ctrl_row.addWidget(self.spin_font)
        ctrl_row.addWidget(self.btn_font_default)
        ctrl_row.addStretch(1)
        
        self.chk_hide_zero = QCheckBox("0원 행 숨기기")
        self.chk_hide_zero.setToolTip("지출액과 잔액이 모두 0원인 항목을 숨깁니다.")
        self.chk_hide_zero.stateChanged.connect(self._schedule_refresh)
        ctrl_row.addWidget(self.chk_hide_zero)
        
        right_layout.addLayout(ctrl_row)

        self.tree = QTreeView()
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setItemDelegate(GroupBoxDelegate(self.tree))

        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setStretchLastSection(False)
        # 폰트 변경 대응

        right_layout.addWidget(self.tree, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([450, 1250])

        self.btn_help.clicked.connect(self.open_manual)
        self.btn_open.clicked.connect(self.open_excel)
        self.btn_export.clicked.connect(self.export_current)
        self.spin_font.valueChanged.connect(self._schedule_refresh)
        self.btn_font_default.clicked.connect(lambda: self.spin_font.setValue(10))

    def _kpi_card(self, label: str, value: str) -> QFrame:
        card = QFrame()
        card.setObjectName("kpiCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        lbl = QLabel(label)
        lbl.setStyleSheet("color:#64748b; font-size:10pt;")
        val = QLabel(value)
        val.setObjectName("kpiValue")
        lay.addWidget(lbl)
        lay.addWidget(val)
        return card

    def _apply_style(self):
        self.setStyleSheet(
            """
            QWidget { font-family: 'Malgun Gothic'; font-size: 10pt; }
            QMainWindow { background: #ffffff; }

            QTabWidget::pane { border: 1px solid #e2e8f0; border-radius: 10px; }
            QTabBar::tab {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                padding: 8px 12px;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                margin-right: 6px;
            }
            QTabBar::tab:selected { background: #ffffff; font-weight: 700; }

            QPushButton {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                padding: 8px 12px;
                border-radius: 10px;
            }
            QPushButton:hover { background: #f8fafc; }
            QPushButton:checked {
                background: #dbeafe;
                border-color: #93c5fd;
                font-weight: 700;
                color: #0f172a;
            }
            QPushButton:disabled { color:#94a3b8; border-color:#e2e8f0; }

            QSpinBox {
                border: 1px solid #cbd5e1;
                border-radius: 10px;
                padding: 4px 8px;
                background: #ffffff;
                min-width: 64px;
            }

            QLineEdit {
                border: 1px solid #cbd5e1;
                border-radius: 10px;
                padding: 8px 10px;
                background: #ffffff;
            }

            QScrollArea {
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                background: #ffffff;
            }
            QScrollArea > QWidget > QWidget {
                background: #ffffff;
            }

            QWidget#LeftPanel { background: #ffffff; }

            QFrame#FilterCard {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 14px;
            }
            QLabel#FilterTitle {
                color: #0f172a;
                font-weight: 700;
            }

            QFrame#kpiCard {
                border: 1px solid #e2e8f0;
                border-radius: 14px;
                background: #ffffff;
                min-width: 160px;
            }
            QLabel#kpiValue { font-size: 14pt; font-weight: 700; color:#0f172a; }

            QTreeView {
                border: 1px solid #d7dee8;
                border-radius: 12px;
                padding: 6px;
                background: #ffffff;
                gridline-color: #dce4ee;
            }
            QTreeView::item {
                background: transparent;
                outline: 0;
            }
            QTreeView::item:hover { background: transparent; }
            QTreeView::item:selected { background: transparent; color:#0f172a; }

            /* 트리 왼쪽 '들여쓰기/가지' 영역 하이라이트 동기화 */
            QTreeView::branch:hover { background: #fff7c2; }
            QTreeView::branch:selected { background: #dbeafe; }

            QHeaderView::section {
                background: #f8fafc;
                border: none;
                border-bottom: 1px solid #d7dee8;
                padding: 8px 10px;
                font-weight: 700;
                color: #334155;
            }
            """
        )

    def _current_analysis_mode(self) -> str:
        if self.btn_mode_4.isChecked():
            return ANALYSIS_MODE_4
        if self.btn_mode_3.isChecked():
            return ANALYSIS_MODE_3
        return ANALYSIS_MODE_AUTO

    def _current_analysis_mode_label(self) -> str:
        return MODE_LABELS.get(self._current_analysis_mode(), MODE_LABELS[ANALYSIS_MODE_AUTO])

    def _business_filter_title(self) -> str:
        mode = self._current_analysis_mode()
        if mode == ANALYSIS_MODE_3:
            return "사업명 선택 (3세부 기준)"
        if mode == ANALYSIS_MODE_4:
            return "사업명 선택 (4세부 기준)"
        return "사업명 선택 (자동분류)"

    def _business_filter_placeholder(self) -> str:
        mode = self._current_analysis_mode()
        if mode == ANALYSIS_MODE_3:
            return "3세부 사업명 검색…"
        if mode == ANALYSIS_MODE_4:
            return "4세부 사업명 검색…"
        return "자동분류 사업명 검색…"

    def _update_analysis_mode_ui(self):
        self.biz_list.set_title(self._business_filter_title())
        self.biz_list.set_search_placeholder(self._business_filter_placeholder())

        if not self.parsed:
            if self._current_analysis_mode() == ANALYSIS_MODE_AUTO:
                self.mode_hint.setText("자동 구조 분석으로 3세부/4세부를 추천합니다.")
            elif self._current_analysis_mode() == ANALYSIS_MODE_3:
                self.mode_hint.setText("세세세부(3세부) 단위로 사업을 묶어 예산코드별로 비교합니다.")
            else:
                self.mode_hint.setText("세세세세부(4세부) 단위로 더 하위 계층까지 내려가 비교합니다.")
            return

        summary = self.parsed.analysis_summary
        mode = self._current_analysis_mode()
        if mode == ANALYSIS_MODE_AUTO:
            self.mode_hint.setText(
                f"자동분류 결과: 3세부 {summary.get('auto_3', 0)}개 · 4세부 {summary.get('auto_4', 0)}개"
            )
        elif mode == ANALYSIS_MODE_3:
            self.mode_hint.setText("3세부 기준: 세세세부사업 단위로 같은 사업명을 예산코드별로 비교합니다.")
        else:
            self.mode_hint.setText("4세부 기준: 세세세세부사업 단위로 더 하위 계층까지 내려가 예산코드를 비교합니다.")

    def _on_analysis_mode_changed(self):
        self._update_analysis_mode_ui()
        self._rebuild_business_from_l1(reset_selection=True)
        self._schedule_refresh()

    def load_file(self, path: str):
        try:
            parsed = load_and_parse_excel(path)
        except Exception as e:
            _msg_error(self, "엑셀 불러오기 실패", str(e))
            return

        self.parsed = parsed
        self.btn_export.setEnabled(True)

        self._set_kpi(
            parsed.totals.get("budget_total", 0.0),
            parsed.totals["grant"],
            parsed.totals["spent"],
            parsed.totals["balance"],
        )
        self.kpi_basis.setText("기준: 소계(전체)")

        self.l1_list.set_items(parsed.l1_items, default_checked=True)
        self.code_list.set_items([(f"[{c}] {n}".strip(), c) for c, n in parsed.budget_codes], default_checked=True)
        self._update_analysis_mode_ui()
        self._rebuild_business_from_l1(reset_selection=True)
        self._refresh_view()

    def open_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "엑셀 파일 선택", str(Path.home()), "Excel Files (*.xlsx)")
        if not path:
            return
        self.load_file(path)

    def open_manual(self):
        dlg = ManualDialog(self)
        dlg.exec()

    def export_current(self):
        if self.tree.model() is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "엑셀로 저장",
            str(Path.home() / "budget_view.xlsx"),
            "Excel Files (*.xlsx)",
        )
        if not path:
            return
        try:
            export_current_view_to_excel(self.tree, path)
            QMessageBox.information(self, "저장 완료", f"저장했습니다:\n{path}")
        except Exception as e:
            _msg_error(self, "저장 실패", str(e))

    def _set_kpi(self, bgt: float, g: float, s: float, b: float):
        self.kpi_budget.findChild(QLabel, "kpiValue").setText(_fmt_money(bgt))
        self.kpi_grant.findChild(QLabel, "kpiValue").setText(_fmt_money(g))
        self.kpi_spent.findChild(QLabel, "kpiValue").setText(_fmt_money(s))
        self.kpi_bal.findChild(QLabel, "kpiValue").setText(_fmt_money(b))

    def _selected_l1(self) -> Optional[Set[str]]:
        if not self.parsed:
            return None
        sel = self.l1_list.selected_keys()
        all_keys = self.l1_list.all_keys()
        return None if sel == all_keys else sel

    def _selected_businesses(self) -> Optional[Set[str]]:
        if not self.parsed:
            return None
        sel = self.biz_list.selected_keys()
        all_keys = self.biz_list.all_keys()
        return None if sel == all_keys else sel

    def _selected_codes(self) -> Optional[Set[str]]:
        if not self.parsed:
            return None
        sel = self.code_list.selected_keys()
        all_keys = self.code_list.all_keys()
        return None if sel == all_keys else sel

    def _on_l1_changed(self):
        self._rebuild_business_from_l1(reset_selection=True)
        self._schedule_refresh()

    def _rebuild_business_from_l1(self, reset_selection: bool):
        if not self.parsed:
            return

        df = self.parsed.df.sort_values("_row_order").copy()
        selected_l1 = self._selected_l1()
        if selected_l1 is not None:
            df = df[df["_L1_key"].astype(str).isin(selected_l1)]

        key_col, display_col, _ = business_columns_for_mode(self._current_analysis_mode())
        single_l1 = selected_l1 is not None and len(selected_l1) == 1

        items: List[Tuple[str, str]] = []
        seen = set()
        for _, r in df[["_L1_display", key_col, display_col]].iterrows():
            key = str(r[key_col]).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            display = str(r[display_col]).strip()
            if not single_l1:
                display = f"{r['_L1_display']} / {display}"
            items.append((display, key))

        checked_keys = None if reset_selection else self.biz_list.selected_keys()
        if checked_keys is not None:
            checked_keys = {k for k in checked_keys if any(item_key == k for _, item_key in items)}
        self.biz_list.set_items(items, checked_keys=checked_keys, default_checked=True)

    def _update_filter_chips(self):
        if not self.parsed:
            self.filter_chips.setText("")
            return

        def summarize(sel: Optional[Set[str]], label: str, source: Optional[FilterList] = None):
            if sel is None:
                return f"{label}: 전체"
            if len(sel) == 0:
                return f"{label}: 선택 없음"
            if source is not None:
                items = source.display_texts_for_keys(sel)
            else:
                items = sorted(sel)
            if not items:
                return f"{label}: 선택 없음"
            return f"{label}: {items[0]}" + (f" 외 {len(items) - 1}개" if len(items) > 1 else "")

        mode_txt = f"분석 모드: {self._current_analysis_mode_label()}"
        l1_txt = summarize(self._selected_l1(), "세세부사업", self.l1_list)
        biz_txt = summarize(self._selected_businesses(), "사업명", self.biz_list)
        code_txt = summarize(self._selected_codes(), "예산코드", self.code_list)
        zero_txt = "0원 행 숨김: ON" if self.chk_hide_zero.isChecked() else "0원 행 숨김: OFF"
        self.filter_chips.setText(
            f"필터 · {mode_txt}   |   {l1_txt}   |   {biz_txt}   |   {code_txt}   |   {zero_txt}"
        )

    def _schedule_refresh(self):
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self._refresh_view)

    def _refresh_view(self):
        self._refresh_pending = False
        if not self.parsed:
            return

        self._update_analysis_mode_ui()
        self._update_filter_chips()

        opt = BuildOptions(
            mode="예산코드→사업" if self.tabs.currentIndex() == 1 else "사업→예산코드",
            analysis_mode=self._current_analysis_mode(),
            selected_l1=self._selected_l1(),
            selected_businesses=self._selected_businesses(),
            selected_codes=self._selected_codes(),
            hide_zero_rows=self.chk_hide_zero.isChecked(),
            base_font_size=self.spin_font.value(),
        )

        builder = BudgetTreeBuilder(self.parsed.df)
        model = builder.build_model(opt)
        self.tree.setModel(model)

        base = self.spin_font.value()
        # 전체 1250 공간 중 안전 반경 확보
        self.tree.setColumnWidth(0, 480 if base <= 10 else 530)
        self.tree.setColumnWidth(1, 240 if base <= 10 else 260)
        
        # ✅ 수치 데이터 컬럼(2,3,4,5)을 균등 배분으로 설정
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        
        self.tree.expandToDepth(2)
        # ✅ 시각적 깔끔함을 위해 그리드 비활성화 (델리게이트에서 직접 처리)
        self.tree.setIndentation(20)
        # 윈도우 크기에 맞게 헤더 레이아웃 트리거
        self.tree.header().update()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
