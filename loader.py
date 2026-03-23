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
    l1_group_totals: Dict[str, Dict[str, float]]
    l2_group_totals: Dict[str, Dict[str, float]]
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

    # 💡 [Note] 예산코드([210]) 패턴은 레벨 10으로 취급
    m_code = _RE_CODE_ROW.match(t)
    if m_code:
        plain = m_code.group(2).strip()
        display = f"[{m_code.group(1)}] {plain}"
        return ParsedLevel(10, display, plain)

    # 💡 소계/합계 등이 포함된 행도 10으로 취급
    return ParsedLevel(10, t, t)


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


def _is_hierarchical(lvl: int) -> bool:
    return 1 <= lvl <= 6


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
    # 유저 요청: D(예산현액), I(배부액), J(교부액), K(원인행위액), L(지출액), R(배부잔액), M(교부잔액)
    # 💡 괄호 안의 알파벳을 파싱하여 우선순위 부여
    target_map = {
        "D": "예산현액",
        "I": "배부액",
        "J": "교부액",
        "K": "원인행위액",
        "L": "지출액",
        "R": "배부잔액",
        "M": "교부잔액"
    }
    
    money_cols_info = []
    found_letters = {}
    
    for c in columns:
        cc = str(c)
        # "일상경비교부액_예산현액(D)" 와 같은 형태에서 (D) 추출
        m = re.search(r"\(([A-Z])\)", cc)
        if m:
            letter = m.group(1)
            if letter in target_map:
                disp = target_map[letter]
                money_cols_info.append((c, disp))
                found_letters[letter] = True

    # 알파벳으로 못 찾은 경우 키워드 매칭 (Fallback)
    if len(money_cols_info) < len(target_map):
        keywords = ["예산현액", "배부액", "교부액", "원인행위액", "지출액", "배부잔액", "교부잔액"]
        for c in columns:
            cc = str(c)
            # 이미 찾은 알파벳은 제외
            m = re.search(r"\(([A-Z])\)", cc)
            if m and m.group(1) in found_letters:
                continue
                
            for k in keywords:
                if k in cc:
                    # 중복 방지
                    if not any(info[1] == k for info in money_cols_info):
                        money_cols_info.append((c, k))
                    break

    # 순서 유지하며 "잔액" 추가 (유저 요청: J - L)
    money_columns = [info[1] for info in money_cols_info]
    money_columns.append("잔액")

    out = data.copy()
    out["_raw_item"] = out[item_col].fillna("").astype(str).str.strip()

    if cost_col and cost_col in out.columns:
        out["원가통계비목"] = out[cost_col].fillna("").astype(str).replace("nan", "").str.strip()
    else:
        out["원가통계비목"] = ""

    # 식별된 동적 금액 컬럼들을 _money_ 접두사를 붙여 저장
    for orig_c, disp in money_cols_info:
        out[f"_money_{disp}"] = _to_money(out[orig_c])

    # 💡 [유저 요청] 잔액 재계산: 잔액 = 교부액 - 지출액
    grant_key = next((disp for _, disp in money_cols_info if "교부액" in disp), None)
    spent_key = next((disp for _, disp in money_cols_info if "지출액" in disp), None)
    
    if grant_key and spent_key:
        out["_money_잔액"] = out[f"_money_{grant_key}"] - out[f"_money_{spent_key}"]
    else:
        out["_money_잔액"] = 0.0

    # --- 💡 계층별 절대 합계(Grand Totals) 사전 계산 ---
    # 필터링과 무관하게 원본 데이터 기준의 합계를 미리 구함
    
    # 💡 [중요] 중복 계산 방지를 위해 실제 데이터 행(Leaf) 식별 플래그 생성
    # 소계/합계 행 및 예산코드 행은 계산에서 제외
    raw_items = out["_raw_item"].tolist()
    lvls = [_parse_level(t).lvl for t in raw_items]
    is_leaf_mask = []
    for i in range(len(lvls)):
        lvl = lvls[i]
        item = str(raw_items[i])
        
        # 1. 명시적 제외 (소계, 합계, 총계 등)
        if any(k in item for k in ["소계", "합계", "총계"]):
            is_leaf_mask.append(False)
            continue
            
        # 2. 비계층 행 제외 (예산코드 등)
        if not _is_hierarchical(lvl):
            is_leaf_mask.append(False)
            continue
            
        # 3. 자식 여부 판단 (다음 계층 행의 레벨이 더 깊으면 부모임)
        if i + 1 < len(lvls):
            next_h_lvl = None
            for j in range(i + 1, len(lvls)):
                if _is_hierarchical(lvls[j]):
                    next_h_lvl = lvls[j]
                    break
            
            if next_h_lvl is not None and next_h_lvl > lvl:
                is_leaf_mask.append(False)
            else:
                is_leaf_mask.append(True)
        else:
            is_leaf_mask.append(True)
    
    out["_is_agg_leaf"] = is_leaf_mask
    leaf_only_df = out[out["_is_agg_leaf"]].copy()

    # 1. 전체 합계 (Totals)
    is_total = out["_raw_item"].astype(str).str.strip().eq("소계")
    if is_total.any():
        tot_row = out.loc[is_total].iloc[-1]
        totals = {disp: float(tot_row.get(f"_money_{disp}", 0.0)) for disp in money_columns}
    else:
        # 요약 행을 제외한 말단 데이터만 합산
        totals = {disp: float(leaf_only_df[f"_money_{disp}"].sum()) for disp in money_columns}



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

    # --- 💡 계층별 절대 합계(Grand Totals) 사전 계산 (계층 컬럼 생성 후 수행) ---
    l1_group_totals = {}
    l2_group_totals = {}
    
    # 중복 합산 방지를 위해 말단 데이터만 사용하여 그룹 합계 계산
    leaf_only_final = out[out["_is_agg_leaf"]].copy()

    for l1_key in _appearance_order(out["_L1_key"].astype(str).tolist()):
        l1_df = leaf_only_final[leaf_only_final["_L1_key"] == l1_key]
        l1_group_totals[l1_key] = {col: float(l1_df[f"_money_{col}"].sum()) for col in money_columns}
        
    # 💡 [중요] L2와 L3 합계를 통합하여 모든 분석 모드(3세부/4세부)에서 합계가 보이도록 함
    for l2_key in _appearance_order(out["_L2_key"].astype(str).tolist()):
        l2_df = leaf_only_final[leaf_only_final["_L2_key"] == l2_key]
        l2_group_totals[l2_key] = {col: float(l2_df[f"_money_{col}"].sum()) for col in money_columns}

    for l3_key in _appearance_order(out["_L3_key"].astype(str).tolist()):
        if not l3_key: continue
        l3_df = leaf_only_final[leaf_only_final["_L3_key"] == l3_key]
        l2_group_totals[l3_key] = {col: float(l3_df[f"_money_{col}"].sum()) for col in money_columns}
    out["_row_order"] = list(range(len(out)))

    mask_code_row = out["_raw_item"].astype(str).str.match(_RE_CODE_ROW)
    out = out[~is_total & ~mask_code_row].copy()
    out = out[~out["_lvl"].isin([1, 2])].copy()
    out = out[out["_item_text"].astype(str).str.strip().ne("")].copy()
    
    # 💡 [중요] 기존 분류 데이터(외국인평화캠프 등) 보존을 위해 행을 삭제하지 않음
    # 대신 _is_agg_leaf 플래그를 통해 합계 로직에서만 선별적으로 사용
    
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
        l1_group_totals=l1_group_totals,
        l2_group_totals=l2_group_totals,
        analysis_summary=analysis_summary,
        money_columns=money_columns,
    )
