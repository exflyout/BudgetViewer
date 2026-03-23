from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import pandas as pd
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QFont, QStandardItem, QStandardItemModel

from loader import ANALYSIS_MODE_AUTO, business_columns_for_mode


ROW_KIND_ROLE = Qt.UserRole + 100
ACCENT_COLOR_ROLE = Qt.UserRole + 101
TEXT_FULL_ROLE = Qt.UserRole + 102


def _fmt_money(x: float) -> str:
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "0"


def _appearance_order(values: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for v in values:
        vv = str(v).strip()
        if vv and vv not in seen:
            seen.add(vv)
            out.append(vv)
    return out


def _l1_bg(i: int) -> QColor:
    palette = [
        QColor(234, 244, 255),
        QColor(236, 250, 242),
        QColor(255, 246, 233),
        QColor(244, 238, 255),
        QColor(255, 238, 244),
        QColor(236, 244, 250),
    ]
    return palette[i % len(palette)]


def _l2_bg() -> QColor:
    return QColor(249, 251, 255)


def _code_bg() -> QColor:
    return QColor(236, 242, 249)


def _leaf_bg() -> QColor:
    return QColor(255, 255, 255)


def _accent(i: int) -> QColor:
    palette = [
        QColor(37, 99, 235),
        QColor(16, 185, 129),
        QColor(245, 158, 11),
        QColor(139, 92, 246),
        QColor(244, 63, 94),
        QColor(14, 165, 233),
    ]
    return palette[i % len(palette)]


def _pad_for_level(lvl: int, start_level: int) -> str:
    depth = max(0, lvl - start_level)
    return "  " * depth


@dataclass
class BuildOptions:
    mode: str  # 사업→예산코드 / 예산코드→사업
    analysis_mode: str = ANALYSIS_MODE_AUTO
    selected_l1: Optional[set[str]] = None
    selected_businesses: Optional[set[str]] = None
    selected_codes: Optional[set[str]] = None
    hide_zero_rows: bool = False
    base_font_size: int = 10


class BudgetTreeBuilder:
    def __init__(self, df: pd.DataFrame, money_columns: List[str], l1_totals=None, l2_totals=None):
        self.df = df.copy()
        self.money_columns = money_columns
        self.l1_group_totals = l1_group_totals if l1_totals is None else l1_totals # 호환성 유지
        self.l1_totals = l1_totals or {}
        self.l2_totals = l2_totals or {}
        self.HEADERS = ["항목", "원가통계비목"] + self.money_columns
        self.business_key_col = "_biz_key_auto"
        self.business_display_col = "_biz_display_auto"
        self.business_level_col = "_biz_level_auto"
        self.base_font_size = 10
        self.hide_zero_rows = False 


    def build_model(self, opt: BuildOptions) -> QStandardItemModel:
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(self.HEADERS)
        model.setColumnCount(len(self.HEADERS))

        self.base_font_size = max(9, int(opt.base_font_size))
        self.business_key_col, self.business_display_col, self.business_level_col = business_columns_for_mode(opt.analysis_mode)
        self.hide_zero_rows = opt.hide_zero_rows # 필터링 모드 저장
        data = self._apply_filters(self.df, opt)

        if data.empty:
            return model

        if opt.mode == "예산코드→사업":
            self._build_code_first(model, data)
        else:
            self._build_business_first(model, data)

        # 💡 [유저 요청] 필터링 시 하단에 '소계(현재 화면)' 행 추가
        # 데이터가 필터링된 상태인 경우(전체 row 수와 다를 때) 혹은 항상 표시
        if not data.empty:
            self._append_subtotal_row(model, data)

        return model

    def _apply_filters(self, df: pd.DataFrame, opt: BuildOptions) -> pd.DataFrame:
        out = df.copy()
        if opt.selected_l1 is not None:
            out = out[out["_L1_key"].astype(str).isin(opt.selected_l1)]
        if opt.selected_businesses is not None:
            out = out[out[self.business_key_col].astype(str).isin(opt.selected_businesses)]
        if opt.selected_codes is not None:
            out = out[out["_code"].astype(str).isin(opt.selected_codes)]

        # 💡 [중요] 0원 행 숨기기는 집계에 영향을 주지 않기 위해 여기서 제거하지 않고,
        # 나중에 트리를 그릴 때(_append_block_rows) 노출만 제어합니다.

        return out.sort_values("_row_order").copy()

    def _build_business_first(self, model: QStandardItemModel, df: pd.DataFrame):
        root = model.invisibleRootItem()
        l1_order = _appearance_order(df["_L1_key"].astype(str).tolist())

        for i, l1_key in enumerate(l1_order):
            l1_df = df[df["_L1_key"].astype(str) == l1_key].copy().sort_values("_row_order")
            if l1_df.empty:
                continue

            l1_display = str(l1_df["_L1_display"].iloc[0])
            # 💡 [유저 요청] 필터링 무관 절대 총액 사용
            l1_totals_dict = self.l1_totals.get(l1_key, {})
            l1_totals = [l1_totals_dict.get(c, 0.0) for c in self.money_columns]
            
            l1_item = self._section_item(l1_display, i)

            row_items = [l1_item, self._blank("l1", _l1_bg(i))]
            for val in l1_totals:
                row_items.append(self._money_item(val, kind="l1", bg=_l1_bg(i)))
            root.appendRow(row_items)

            business_order = _appearance_order(l1_df[self.business_key_col].astype(str).tolist())
            for business_key in business_order:
                biz_df = l1_df[l1_df[self.business_key_col].astype(str) == business_key].copy().sort_values("_row_order")
                if biz_df.empty:
                    continue

                biz_display = str(biz_df[self.business_display_col].iloc[0])
                biz_kind = self._business_kind(int(biz_df[self.business_level_col].iloc[0]))
                # 💡 [유저 요청] 필터링 무관 절대 총액 사용
                biz_totals_dict = self.l2_totals.get(business_key, {})
                biz_totals = [biz_totals_dict.get(c, 0.0) for c in self.money_columns]
                
                biz_item = self._mid_item(f"○ {biz_display}", kind=biz_kind)

                identity_to_skip = biz_display.split(" / ")[-1].strip() if " / " in biz_display else biz_display.strip()

                row_items = [biz_item, self._blank(biz_kind)]
                for val in biz_totals:
                    row_items.append(self._money_item(val, kind=biz_kind))
                l1_item.appendRow(row_items)

                for code, code_name, block_df in self._split_code_blocks(biz_df):
                    code_totals = self._block_totals(block_df)
                    code_item = self._code_item(code, code_name)

                    row_items = [code_item, self._blank("code", _code_bg())]
                    for val in code_totals:
                        row_items.append(self._money_item(val, kind="code", bg=_code_bg()))
                    biz_item.appendRow(row_items)

                    self._append_block_rows(code_item, block_df, identity_to_skip=identity_to_skip)

    def _build_code_first(self, model: QStandardItemModel, df: pd.DataFrame):
        root = model.invisibleRootItem()
        code_order = self._code_order(df)

        for i, (code, code_name) in enumerate(code_order):
            code_df = df[df["_code"].astype(str) == code].copy().sort_values("_row_order")
            if code_df.empty:
                continue

            code_totals = self._grouped_totals(code_df, self.business_key_col)
            code_item = self._section_item(f"[{code}] {code_name}".strip(), i)

            row_items = [code_item, self._blank("l1", _l1_bg(i))]
            for val in code_totals:
                row_items.append(self._money_item(val, kind="l1", bg=_l1_bg(i)))
            root.appendRow(row_items)

            l1_order = _appearance_order(code_df["_L1_key"].astype(str).tolist())
            for l1_key in l1_order:
                l1_df = code_df[code_df["_L1_key"].astype(str) == l1_key].copy().sort_values("_row_order")
                if l1_df.empty:
                    continue

                l1_display = str(l1_df["_L1_display"].iloc[0])
                l1_item = self._mid_item(l1_display, kind="l2")

                row_items = [l1_item, self._blank("l2")] + [self._blank("l2") for _ in self.money_columns]
                code_item.appendRow(row_items)

                business_order = _appearance_order(l1_df[self.business_key_col].astype(str).tolist())
                for business_key in business_order:
                    biz_df = l1_df[l1_df[self.business_key_col].astype(str) == business_key].copy().sort_values("_row_order")
                    if biz_df.empty:
                        continue

                    biz_display = str(biz_df[self.business_display_col].iloc[0])
                    biz_kind = self._business_kind(int(biz_df[self.business_level_col].iloc[0]))
                    child_kind = "subbiz" if biz_kind == "subbiz" else "l3"
                    biz_item = self._mid_item(f"○ {biz_display}", kind=child_kind)

                    identity_to_skip = biz_display.split(" / ")[-1].strip() if " / " in biz_display else biz_display.strip()

                    row_items = [biz_item, self._blank(child_kind)] + [self._blank(child_kind) for _ in self.money_columns]
                    l1_item.appendRow(row_items)

                    self._append_block_rows(biz_item, biz_df, identity_to_skip=identity_to_skip)

    def _business_kind(self, business_level: int) -> str:
        return "subbiz" if int(business_level) >= 4 else "l2"

    def _grouped_totals(self, df: pd.DataFrame, key_col: str) -> List[float]:
        total_list = [0.0] * len(self.money_columns)
        for key in _appearance_order(df[key_col].astype(str).tolist()):
            gdf = df[df[key_col].astype(str) == key].copy()
            bgt_list = self._block_totals(gdf)
            for j in range(len(total_list)):
                total_list[j] += bgt_list[j]
        return total_list

    def _code_order(self, df: pd.DataFrame) -> List[Tuple[str, str]]:
        seen = set()
        out: List[Tuple[str, str]] = []
        for _, r in df[["_code", "_code_name"]].astype(str).iterrows():
            code = r["_code"].strip()
            name = r["_code_name"].strip()
            if code and code not in seen:
                seen.add(code)
                out.append((code, name))
        return out

    def _split_code_blocks(self, df: pd.DataFrame) -> List[Tuple[str, str, pd.DataFrame]]:
        out: List[Tuple[str, str, pd.DataFrame]] = []
        current_code = None
        current_name = None
        current_rows = []

        for _, row in df.sort_values("_row_order").iterrows():
            code = str(row["_code"]).strip()
            code_name = str(row["_code_name"]).strip()
            if current_code is None:
                current_code = code
                current_name = code_name
                current_rows = [row.to_dict()]
                continue

            if code != current_code:
                out.append((current_code or "", current_name or "", pd.DataFrame(current_rows)))
                current_code = code
                current_name = code_name
                current_rows = [row.to_dict()]
            else:
                current_rows.append(row.to_dict())

        if current_rows:
            out.append((current_code or "", current_name or "", pd.DataFrame(current_rows)))
        return out

    def _block_totals(self, df: pd.DataFrame) -> List[float]:
        if df.empty:
            return [0.0] * len(self.money_columns)
        # 💡 중복 합산 방지를 위해 해당 블록 내의 말단(Leaf) 데이터만 합산
        leaf_df = df[df.get("_is_agg_leaf", True)].copy()
        return [float(leaf_df.get(f"_money_{c}", pd.Series([0.0])).sum()) for c in self.money_columns]

    def _append_block_rows(self, parent: QStandardItem, df: pd.DataFrame, identity_to_skip: Optional[str] = None):
        if df.empty:
            return
        ordered = df.sort_values("_row_order")
        start_level = int(ordered["_lvl"].astype(int).min())
        stack: List[Tuple[int, QStandardItem]] = []

        for _, r in ordered.iterrows():
            lvl = int(r["_lvl"])
            text = str(r["_item_text"])
            plain = str(r.get("_plain_text", text)).strip()

            if identity_to_skip and lvl == start_level and plain == identity_to_skip:
                continue

            cost = str(r.get("원가통계비목", "") or "").strip()
            if cost.lower() == "nan":
                cost = ""

            # 0원 행 숨기기 처리
            if self.hide_zero_rows:
                spent_col = next((c for c in self.money_columns if "지출액" in c), None)
                bal_col = next((c for c in self.money_columns if "잔액" in d), None)

                spent = float(r.get(f"_money_{spent_col}", 0.0)) if spent_col else 0.0
                bal = float(r.get(f"_money_{bal_col}", 0.0)) if bal_col else 0.0
                if spent == 0 and bal == 0:
                    continue

            label = f"{_pad_for_level(lvl, start_level)}{text}"
            kind = "l3" if lvl == start_level else "leaf"
            bg = _leaf_bg()

            c0 = QStandardItem(label)
            c1 = QStandardItem(cost)

            self._apply_item(c0, kind=kind, bg=bg, tooltip=text)
            self._apply_item(c1, kind=kind, bg=bg, tooltip=cost)

            if kind == "l3":
                f = QFont()
                f.setBold(True)
                f.setPointSize(self.base_font_size + 1)
                c0.setFont(f)
            else:
                f = QFont()
                f.setPointSize(self.base_font_size)
                c0.setFont(f)

            row_items = [c0, c1]
            for c in self.money_columns:
                val = float(r.get(f"_money_{c}", 0.0))
                row_items.append(self._money_item(val, kind=kind, bg=bg))

            while stack and stack[-1][0] >= lvl:
                stack.pop()
            if stack:
                stack[-1][1].appendRow(row_items)
            else:
                parent.appendRow(row_items)
            stack.append((lvl, c0))


    def _apply_item(
        self,
        item: QStandardItem,
        kind: str,
        bg: Optional[QColor] = None,
        accent: Optional[QColor] = None,
        tooltip: Optional[str] = None,
    ):
        item.setEditable(False)
        item.setData(kind, ROW_KIND_ROLE)
        if bg is not None:
            item.setData(bg, Qt.BackgroundRole)
        if accent is not None:
            item.setData(accent, ACCENT_COLOR_ROLE)
        if tooltip:
            item.setToolTip(tooltip)
            item.setData(tooltip, TEXT_FULL_ROLE)

    def _section_item(self, text: str, idx: int) -> QStandardItem:
        bg = _l1_bg(idx)
        it = QStandardItem(text)
        f = QFont()
        f.setBold(True)
        f.setPointSize(self.base_font_size + 3)
        it.setFont(f)
        it.setForeground(QColor(15, 23, 42))
        it.setSizeHint(QSize(10, max(40, self.base_font_size * 3 + 8)))
        self._apply_item(it, kind="l1", bg=bg, accent=_accent(idx), tooltip=text)
        return it

    def _mid_item(self, text: str, kind: str) -> QStandardItem:
        it = QStandardItem(text)
        f = QFont()
        f.setBold(True)
        if kind == "l2":
            f.setPointSize(self.base_font_size + 2)
            bg = _l2_bg()
        elif kind in {"l3", "subbiz"}:
            f.setPointSize(self.base_font_size + 1)
            bg = _leaf_bg()
        else:
            f.setPointSize(self.base_font_size)
            bg = _leaf_bg()
        it.setFont(f)
        it.setForeground(QColor(30, 41, 59))
        it.setSizeHint(QSize(10, max(30, self.base_font_size * 2 + 10)))
        self._apply_item(it, kind=kind, bg=bg, tooltip=text)
        return it

    def _code_item(self, code: str, name: str) -> QStandardItem:
        text = f"[{code}] {name}".strip()
        it = QStandardItem(text)
        f = QFont()
        f.setBold(True)
        f.setPointSize(self.base_font_size)
        it.setFont(f)
        it.setForeground(QColor(15, 23, 42))
        it.setSizeHint(QSize(10, max(28, self.base_font_size * 2 + 8)))
        self._apply_item(it, kind="code", bg=_code_bg(), tooltip=text)
        return it

    def _money_item(self, value: float, kind: str, bg: Optional[QColor] = None) -> QStandardItem:
        it = QStandardItem(_fmt_money(value))
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        f = QFont()
        if kind in {"l3", "subbiz", "code"}:
            f.setBold(True)
        f.setPointSize(self.base_font_size)
        it.setFont(f)
        default_bg = _code_bg() if kind == "code" else (_l2_bg() if kind == "l2" else _leaf_bg())
        self._apply_item(it, kind=kind, bg=bg or default_bg)
        return it

    def _blank(self, kind: str, bg: Optional[QColor] = None) -> QStandardItem:
        it = QStandardItem("")
        default_bg = _l2_bg() if kind == "l2" else _leaf_bg()
        self._apply_item(it, kind=kind, bg=bg or default_bg)
        return it

    def _append_subtotal_row(self, model: QStandardItemModel, df: pd.DataFrame):
        """ 하단에 현재 화면(필터링 결과)의 합계를 표시하는 특별 행 추가 """
        root = model.invisibleRootItem()
        
        # 빈 줄 하나 삽입
        root.appendRow([self._blank("leaf") for _ in range(model.columnCount())])
        
        subtotal_item = QStandardItem("∑ 소계 (현재 필터 결과)")
        f = QFont()
        f.setBold(True)
        f.setPointSize(self.base_font_size + 1)
        subtotal_item.setFont(f)
        subtotal_item.setForeground(QColor(37, 99, 235)) # 파란색 강조
        
        bg = QColor(241, 245, 249) # 연한 회색 배경
        self._apply_item(subtotal_item, kind="l1", bg=bg)
        
        row_items = [subtotal_item, self._blank("l1", bg)]
        
        # 현재 화면에 보이는 데이터들의 합계 계산
        # 💡 중복 합산 방지를 위해 _is_agg_leaf 플래그가 True인 말단 데이터만 합산
        leaf_df = df[df.get("_is_agg_leaf", True)].copy()
        
        for col in self.money_columns:
            val = float(leaf_df[f"_money_{col}"].sum())
            row_items.append(self._money_item(val, kind="l1", bg=bg))
            
        root.appendRow(row_items)
