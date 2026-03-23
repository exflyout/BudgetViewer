from __future__ import annotations

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem

from tree_model import ROW_KIND_ROLE, ACCENT_COLOR_ROLE


class GroupBoxDelegate(QStyledItemDelegate):
    """
    문단 중심 렌더링 + 숫자열 우측 패딩 보정.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.select_overlay = QColor(219, 234, 254, 150)
        self.line_strong = QColor(203, 213, 225) # 보다 부드러운 회색으로 조정
        self.line_soft = QColor(226, 232, 240)
        self.white = QColor(255, 255, 255)
        self.hover_overlay = QColor(255, 247, 194) # 불투명 파스텔 노란색으로 통일

    def paint(self, painter: QPainter, option, index):
        kind = index.data(ROW_KIND_ROLE) or "leaf"
        bg = index.data(Qt.BackgroundRole)
        if not isinstance(bg, QColor):
            bg = self.white

        # --- 1. 상태 및 스타일 정보 추출 ---
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        
        # --- 2. 배경 및 선택/하이라이트 렌더링 ---
        full_rect = option.rect
        painter.fillRect(full_rect, bg)
        
        if is_selected:
            painter.fillRect(full_rect, self.select_overlay)
        elif is_hover:
            painter.fillRect(full_rect, self.hover_overlay)

        # --- 3. 구분선 렌더링 ---
        if kind == "l1":
            p = QPen(self.line_strong, 1)
            painter.setPen(p)
            painter.drawLine(full_rect.topLeft(), full_rect.topRight())
            painter.drawLine(full_rect.bottomLeft(), full_rect.bottomRight())
            if index.column() == 0:
                accent = QColor(37, 99, 235)
                bar_rect = QRect(full_rect.left() + 2, full_rect.top() + 6, 3, full_rect.height() - 12)
                painter.fillRect(bar_rect, accent)
        elif kind == "l2":
            p = QPen(self.line_soft, 1)
            painter.setPen(p)
            painter.drawLine(full_rect.topLeft(), full_rect.topRight())
        elif kind in {"l3", "subbiz"}:
            p = QPen(QColor(235, 240, 245), 1)
            painter.setPen(p)
            painter.drawLine(full_rect.topLeft(), full_rect.topRight())
        elif kind == "code":
            p = QPen(self.line_soft, 1)
            painter.setPen(p)
            painter.drawLine(full_rect.bottomLeft(), full_rect.bottomRight())
            
        painter.restore()

        # --- 4. 텍스트 렌더링 (super 호출) ---
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_Selected
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        
        # 텍스트 패딩 (v1.5 스타일)
        if index.column() >= 2:
            # 수치 데이터 우측 패딩
            opt.rect = opt.rect.adjusted(0, 0, -12, 0)
        elif index.column() == 0:
            # 항목명 상하좌우 패딩
            opt.rect = opt.rect.adjusted(6, 4, -4, -4)

        super().paint(painter, opt, index)
