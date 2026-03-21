# exporter.py
from __future__ import annotations

from typing import List, Any

from PySide6.QtWidgets import QTreeView
from PySide6.QtCore import QModelIndex
import pandas as pd


def _collect_rows(view: QTreeView) -> List[List[Any]]:
    model = view.model()
    if model is None:
        return []

    headers = [model.headerData(i, view.header().orientation(), role=0) for i in range(model.columnCount())]
    rows: List[List[Any]] = [headers]

    def walk(parent: QModelIndex, depth: int):
        for r in range(model.rowCount(parent)):
            idx0 = model.index(r, 0, parent)
            row_vals = []
            for c in range(model.columnCount(parent)):
                idx = model.index(r, c, parent)
                row_vals.append(model.data(idx))
            # depth 기반 들여쓰기(엑셀에서도 보기 좋게)
            row_vals[0] = ("  " * max(0, depth)) + (row_vals[0] or "")
            rows.append(row_vals)
            walk(idx0, depth + 1)

    walk(QModelIndex(), 0)
    return rows


def export_current_view_to_excel(view: QTreeView, path: str) -> None:
    rows = _collect_rows(view)
    if not rows:
        raise ValueError("내보낼 데이터가 없습니다.")
    df = pd.DataFrame(rows[1:], columns=rows[0])
    df.to_excel(path, index=False)
