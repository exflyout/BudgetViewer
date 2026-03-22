from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd
import re


ANALYSIS_MODE_AUTO = "auto"
ANALYSIS_MODE_3 = "3"
ANALYSIS_MODE_4 = "4"


_RE_L1 = re.compile(r"^\s*(\d+)\.\s*(.+?)\s*$")
_RE_L2 = re.compile(r"^\s*([가-힣])\.\s*(.+?)\s*$")
_RE_L3 = re.compile(r"^\s*(\d+)\)\s*(.+?)\s*$")
_RE_L4 = re.compile(r"^\s*([가-힣])\)\s*(.+?)\s*$")
_RE_L5 = re.compile(r"^\s*\((\d+)\)\s*(.+?)\s*$")
_RE_L6 = re.compile(r"^\s*\(([가-힣])\)\s*(.+?)\s*$")
_RE_CODE_ROW = re.compile(r"^\s*\[(\d{3})\]\s*(.+?)\s*$")


@dataclass
class ParseResult:
    df: pd.DataFrame
    l1_items: List[Tuple[str, str]]  # (display, key)
    l2_items: List[Tuple[str, str]]  # legacy: 3세부 기준 기본 목록
    budget_codes: List[Tuple[str, str]]
    totals: Dict[str, float]
    analysis_summary: Dict[str, int]
    money_columns: List[str] = None


@dataclass
class ParsedLevel:
    lvl: int
    display: str
    plain: str
    l1_display: Optional[str] = None
    l1_key: Optional[str] = None
    l2_name: Optional[str] = None


BUSINESS_COLUMNS = {
    ANALYSIS_MODE_AUTO: ("_biz_key_auto", "_biz_display_auto", "_biz_level_auto"),
    ANALYSIS_MODE_3: ("_biz_key_3", "_biz_display_3", "_biz_level_3"),
    ANALYSIS_MODE_4: ("_biz_key_4", "_biz_display_4", "_biz_level_4"),
}


def business_columns_for_mode(mode: str) -> Tuple[str, str, str]:
    return BUSINESS_COLUMNS.get(str(mode or ANALYSIS_MODE_AUTO), BUSINESS_COLUMNS[ANALYSIS_MODE_AUTO])


def _norm(x: Any) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    return "" if s.lower() == "nan" else s


def _to_money(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.replace(",", "", regex=False).str.strip()
    s = s.replace({"": "0", "nan": "0", "None": "0"})
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def _find_col_loose(columns: List[str], includes_all: List[str]) -> Optional[str]:
    tokens = [t.replace(" ", "").lower() for t in includes_all]
    for c in columns:
        cc = str(c).replace(" ", "").lower()
        if all(tok in cc for tok in tokens):
            return c
    return None


def _detect_two_header_rows(preview: pd.DataFrame) -> Tuple[int, int]:
    top = None
    for r in range(min(15, len(preview))):
        row = " ".join(_norm(x) for x in preview.iloc[r].tolist())
        if "일상경비교부액" in row.replace(" ", ""):
            top = r
            break

    if top is None:
        sub = None
        for r in range(min(20, len(preview))):
            row = " ".join(_norm(x) for x in preview.iloc[r].tolist())
            s = row.replace(" ", "")
            if "교부액" in s and "지출액" in s:
                sub = r
                break
        if sub is None:
            return 0, 0
        return max(0, sub - 1), sub

    return top, min(top + 1, len(preview) - 1)


def _build_columns(top_row: List[Any], sub_row: List[Any]) -> List[str]:
    top = [_norm(x) for x in top_row]
    sub = [_norm(x) for x in sub_row]

    for i in range(1, len(top)):
        if top[i] == "":
            top[i] = top[i - 1]

    cols: List[str] = []
    for i in range(len(sub)):
        t = top[i] if i < len(top) else ""
        s = sub[i] if i < len(sub) else ""
        if t and s and t != s:
            cols.append(f"{t}_{s}")
        elif s:
            cols.append(s)
        elif t:
            cols.append(t)
        else:
            cols.append(f"col_{i + 1}")
    return cols


def _parse_level(text: str) -> ParsedLevel:
    t = (text or "").strip()

    m1 = _RE_L1.match(t)
    if m1:
        plain = m1.group(2).strip()
        full = f"{m1.group(1)}. {plain}"
        return ParsedLevel(1, full, plain, l1_display=full, l1_key=plain)

    m2 = _RE_L2.match(t)
    if m2:
        plain = m2.group(2).strip()
        display = f"{m2.group(1)}. {plain}"
        return ParsedLevel(2, display, plain, l2_name=plain)

    m3 = _RE_L3.match(t)
    if m3:
        plain = m3.group(2).strip()
        return ParsedLevel(3, f"{m3.group(1)}) {plain}", plain)

    m4 = _RE_L4.match(t)
    if m4:
        plain = m4.group(2).strip()
        return ParsedLevel(4, f"{m4.group(1)}) {plain}", plain)

    m5 = _RE_L5.match(t)
    if m5:
        plain = m5.group(2).strip()
        return ParsedLevel(5, f"({m5.group(1)}) {plain}", plain)

    m6 = _RE_L6.match(t)
    if m6:
        plain = m6.group(2).strip()
        return ParsedLevel(6, f"({m6.group(1)}) {plain}", plain)

    return ParsedLevel(9, t, t)


def _appearance_order(values: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for v in values:
        vv = str(v).strip()
        if vv and vv not in seen:
            seen.add(vv)
            out.append(vv)
    return out


def _has_cost(value: Any) -> bool:
    text = _norm(value)
    return bool(text)


def _should_use_fourth_detail(l2_df: pd.DataFrame) -> bool:
    """
    자동 추천 규칙(보수적):
    - 3세부(L2) 아래 L3 항목들 중,
      "하위 세부가 있고 + L3 자체는 원가비목이 비어 있는" 항목을 4세부 후보로 본다.
    - L3가 1~2개면 모두 후보일 때만 4세부로 본다.
    - L3가 3개 이상이면 절반 이상이 후보일 때 4세부로 본다.
    """
    ordered_l3 = _appearance_order(l2_df["_L3_name"].astype(str).tolist())
    if not ordered_l3:
        return False

    candidate_count = 0
    total_count = 0

    for l3_name in ordered_l3:
        sg = l2_df[l2_df["_L3_name"].astype(str) == l3_name].sort_values("_row_order")
        l3_rows = sg[sg["_lvl"].astype(int) == 3]
        if l3_rows.empty:
            continue

        total_count += 1
        first_l3 = l3_rows.iloc[0]
        has_deeper = bool((sg["_lvl"].astype(int) > 3).any())
        if has_deeper and not _has_cost(first_l3.get("원가통계비목", "")):
            candidate_count += 1

    if total_count == 0:
        return False
    if total_count <= 2:
        return candidate_count == total_count and candidate_count > 0
    return candidate_count * 2 >= total_count


def _build_business_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, int]]:
    out = df.copy()

    out["_biz_key_3"] = out["_L2_key"].astype(str)
    out["_biz_display_3"] = out["_L2_name"].astype(str)
    out["_biz_level_3"] = 3

    has_l3 = out["_L3_name"].astype(str).str.strip().ne("")
    out["_biz_key_4"] = out["_biz_key_3"]
    out["_biz_display_4"] = out["_biz_display_3"]
    out["_biz_level_4"] = 3
    out.loc[has_l3, "_biz_key_4"] = out.loc[has_l3, "_L3_key"].astype(str)
    out.loc[has_l3, "_biz_display_4"] = (
        out.loc[has_l3, "_L2_name"].astype(str).str.strip()
        + " / "
        + out.loc[has_l3, "_L3_name"].astype(str).str.strip()
    )
    out.loc[has_l3, "_biz_level_4"] = 4

    branch_mode_map: Dict[str, int] = {}
    for l2_key in _appearance_order(out["_L2_key"].astype(str).tolist()):
        l2_df = out[out["_L2_key"].astype(str) == l2_key].copy()
        branch_mode_map[l2_key] = 4 if _should_use_fourth_detail(l2_df) else 3

    out["_auto_branch_mode"] = out["_L2_key"].astype(str).map(branch_mode_map).fillna(3).astype(int)
    use_four = out["_auto_branch_mode"].eq(4) & has_l3

    out["_biz_key_auto"] = out["_biz_key_3"]
    out["_biz_display_auto"] = out["_biz_display_3"]
    out["_biz_level_auto"] = 3
    out.loc[use_four, "_biz_key_auto"] = out.loc[use_four, "_biz_key_4"]
    out.loc[use_four, "_biz_display_auto"] = out.loc[use_four, "_biz_display_4"]
    out.loc[use_four, "_biz_level_auto"] = 4

    auto_3 = sum(1 for mode in branch_mode_map.values() if mode == 3)
    auto_4 = sum(1 for mode in branch_mode_map.values() if mode == 4)
    return out, {"auto_3": auto_3, "auto_4": auto_4}


def load_and_parse_excel(path: str) -> ParseResult:
    xlsx = Path(path)
    if not xlsx.exists():
        raise FileNotFoundError(path)

    preview = pd.read_excel(xlsx, engine="openpyxl", header=None, nrows=20)
    top_r, sub_r = _detect_two_header_rows(preview)

    top_row = preview.iloc[top_r].tolist()
    sub_row = preview.iloc[sub_r].tolist()
    cols = _build_columns(top_row, sub_row)

    raw = pd.read_excel(xlsx, engine="openpyxl", header=None)
    data = raw.iloc[sub_r + 1 :].copy()
    data.columns = cols
    data = data.dropna(how="all")
    if data.empty:
        raise ValueError("엑셀 데이터가 비어 있습니다(헤더 아래 내용 없음).")

    columns = list(map(str, data.columns))

    item_col = (
        _find_col_loose(columns, ["사업항목"])
        or _find_col_loose(columns, ["항목"])
        or _find_col_loose(columns, ["예산부서"])
    )
    if not item_col:
        for c in data.columns:
            if data[c].dtype == "object":
                item_col = c
                break
    if not item_col:
        raise ValueError("이 파일에서 '사업항목' 컬럼을 찾지 못했습니다.")

    cost_col = _find_col_loose(columns, ["원가통계비목"]) or _find_col_loose(columns, ["원가", "비목"])

    # --- 일상경비교부액 기준 동적 컬럼 식별 ---
    money_cols_info = []
    for c in columns:
        if "일상경비교부액" in c:
            disp = c.split("_")[-1]
            disp = re.sub(r'\(.*?\)', '', disp).strip() # 괄호가 포함된 부가 설명 삭제 (예: 배부잔액(M=J-K) -> 배부잔액)
            if disp == "교부잔액": 
                disp = "잔액"
            money_cols_info.append((c, disp))
            
    # 일상경비교부액이라는 상위 헤더가 없거나 텍스트가 깨져 못 찾은 경우 Fallback
    if not money_cols_info:
        for c in columns:
            if any(k in c for k in ["원인행위액", "지출결의액", "지출결의잔액", "교부액", "지출액", "잔액", "채무확정액"]):
                disp = c.split("_")[-1]
                disp = re.sub(r'\(.*?\)', '', disp).strip()
                if disp == "교부잔액": disp = "잔액"
                money_cols_info.append((c, disp))

    # 순서 유지하며 중복 제거
    seen_disp = set()
    final_money_cols = []
    for orig_c, disp in money_cols_info:
        if disp not in seen_disp:
            seen_disp.add(disp)
            final_money_cols.append((orig_c, disp))
            
    money_columns = [disp for _, disp in final_money_cols]
    if not money_columns:
        raise ValueError("일상경비교부액 관련 예상 열(원인행위액, 교부액, 지출액, 잔액 등)을 찾을 수 없습니다. 엑셀 형식을 확인해주세요.")

    out = data.copy()
    out["_raw_item"] = out[item_col].fillna("").astype(str).str.strip()

    if cost_col and cost_col in out.columns:
        out["원가통계비목"] = out[cost_col].fillna("").astype(str).replace("nan", "").str.strip()
    else:
        out["원가통계비목"] = ""

    # 식별된 동적 금액 컬럼들을 _money_ 접두사를 붙여 저장
    for orig_c, disp in final_money_cols:
        out[f"_money_{disp}"] = _to_money(out[orig_c])

    # 0원 행 숨김 등의 처리를 위해 임시로 기존 로직과 호환성 유지용 변수 배정
    grant_disp = next((d for d in money_columns if "교부액" in d), None)
    spent_disp = next((d for d in money_columns if "지출액" in d), None)
    bal_disp = next((d for d in money_columns if "잔액" in d), None)
    
    out["_grant"] = out[f"_money_{grant_disp}"] if grant_disp else 0.0
    out["_spent"] = out[f"_money_{spent_disp}"] if spent_disp else 0.0
    out["_balance"] = out[f"_money_{bal_disp}"] if bal_disp else 0.0

    is_total = out["_raw_item"].astype(str).str.strip().eq("소계")
    if is_total.any():
        tot_row = out.loc[is_total].iloc[-1]
        totals = {disp: float(tot_row[f"_money_{disp}"]) for disp in money_columns}
    else:
        totals = {disp: float(out[f"_money_{disp}"].sum()) for disp in money_columns}


    code_col = _find_col_loose(columns, ["예산코드"])
    code_name_col = _find_col_loose(columns, ["예산코드명"])
    if code_col:
        out["_code"] = out[code_col].astype(str).str.extract(r"(\d{3})", expand=False).fillna("").str.strip()
        out["_code_name"] = out[code_name_col].fillna("").astype(str).str.strip() if code_name_col else ""
    else:
        code_list: List[str] = []
        code_name_list: List[str] = []
        current_code = ""
        current_code_name = ""
        for t in out["_raw_item"].tolist():
            m = _RE_CODE_ROW.match(str(t).strip())
            if m:
                current_code = m.group(1)
                current_code_name = m.group(2).strip()
            code_list.append(current_code)
            code_name_list.append(current_code_name)
        out["_code"] = code_list
        out["_code_name"] = code_name_list

    l1_display_list: List[str] = []
    l1_key_list: List[str] = []
    l2_list: List[str] = []
    l2_key_list: List[str] = []
    l3_display_list: List[str] = []
    l3_plain_list: List[str] = []
    l3_key_list: List[str] = []
    lvl_list: List[int] = []
    item_texts: List[str] = []
    plain_texts: List[str] = []

    current_l1_display = ""
    current_l1_key = ""
    current_l2 = ""
    current_l3_display = ""
    current_l3_plain = ""
    l1_display_map: Dict[str, str] = {}

    for t in out["_raw_item"].tolist():
        parsed = _parse_level(t)

        if parsed.lvl == 1 and parsed.l1_display and parsed.l1_key:
            current_l1_display = parsed.l1_display
            current_l1_key = parsed.l1_key
            l1_display_map.setdefault(current_l1_key, current_l1_display)
            current_l2 = ""
            current_l3_display = ""
            current_l3_plain = ""
        elif parsed.lvl == 2 and parsed.l2_name:
            current_l2 = parsed.l2_name
            current_l3_display = ""
            current_l3_plain = ""
        elif parsed.lvl == 3:
            current_l3_display = parsed.display
            current_l3_plain = parsed.plain

        lvl_list.append(parsed.lvl)
        item_texts.append(parsed.display)
        plain_texts.append(parsed.plain)

        l1_key_val = current_l1_key or "대과제 미기재"
        l1_display_val = l1_display_map.get(l1_key_val, current_l1_display or "미분류(대과제 미기재)")
        l2_name_val = current_l2 or "기타(세부사업 미기재)"
        l2_key_val = f"{l1_key_val} ▸ {l2_name_val}"
        l3_name_val = current_l3_plain or ""
        l3_display_val = current_l3_display or ""
        l3_key_val = f"{l2_key_val} ▸ {l3_name_val}" if l3_name_val else ""

        l1_display_list.append(l1_display_val)
        l1_key_list.append(l1_key_val)
        l2_list.append(l2_name_val)
        l2_key_list.append(l2_key_val)
        l3_display_list.append(l3_display_val)
        l3_plain_list.append(l3_name_val)
        l3_key_list.append(l3_key_val)

    out["_lvl"] = lvl_list
    out["_item_text"] = item_texts
    out["_plain_text"] = plain_texts
    out["_L1_display"] = l1_display_list
    out["_L1_key"] = l1_key_list
    out["_L2_name"] = l2_list
    out["_L2_key"] = l2_key_list
    out["_L3_display"] = l3_display_list
    out["_L3_name"] = l3_plain_list
    out["_L3_key"] = l3_key_list
    out["_row_order"] = list(range(len(out)))

    mask_code_row = out["_raw_item"].astype(str).str.match(_RE_CODE_ROW)
    out = out[~is_total & ~mask_code_row].copy()
    out = out[~out["_lvl"].isin([1, 2])].copy()
    out = out[out["_item_text"].astype(str).str.strip().ne("")].copy()
    out = out.reset_index(drop=True)

    out, analysis_summary = _build_business_columns(out)

    l1_key_order = _appearance_order(out["_L1_key"].astype(str).tolist())
    l1_items: List[Tuple[str, str]] = []
    for key in l1_key_order:
        display = l1_display_map.get(key, key)
        l1_items.append((display, key))

    l2_seen = set()
    l2_items: List[Tuple[str, str]] = []
    for _, r in out.sort_values("_row_order")[["_L1_display", "_L2_name", "_L2_key"]].drop_duplicates().iterrows():
        key = str(r["_L2_key"])
        if key not in l2_seen:
            l2_seen.add(key)
            display = f"{r['_L1_display']} / {r['_L2_name']}"
            l2_items.append((display, key))

    budget_codes: List[Tuple[str, str]] = []
    code_seen = set()
    for _, r in out[["_code", "_code_name"]].drop_duplicates().iterrows():
        code = str(r["_code"]).strip()
        name = str(r["_code_name"]).strip()
        if code and code not in code_seen:
            code_seen.add(code)
            budget_codes.append((code, name))

    return ParseResult(
        df=out,
        l1_items=l1_items,
        l2_items=l2_items,
        budget_codes=budget_codes,
        totals=totals,
        analysis_summary=analysis_summary,
        money_columns=money_columns,
    )
