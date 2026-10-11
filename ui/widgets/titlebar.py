#!/usr/bin/env python3
"""Custom title bar widgets for CClip."""

from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QColor, QPainter, QBrush, QMouseEvent
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QFrame

import utils.theme as _t


class _MacDot(QWidget):
    """Circular macOS-style window button. Symbol appears only on group hover."""
    clicked = pyqtSignal()

    _D = 12  # diameter

    def __init__(self, idle_color: str, hover_color: str, symbol: str, title_bar, parent=None):
        super().__init__(parent)
        self._idle  = QColor(idle_color)
        self._hover = QColor(hover_color)
        self._sym   = symbol
        self._bar   = title_bar
        self._pressed = False
        d = self._D + 4
        self.setFixedSize(d, d)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._idle)
        if self._pressed:
            color = color.darker(150)
        elif self.underMouse():
            color = color.darker(125)
        cx = self.width() / 2
        cy = self.height() / 2
        r  = self._D / 2
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(cx - r), int(cy - r), self._D, self._D)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(e.position().toPoint()):
                self.clicked.emit()

    def enterEvent(self, e):
        self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self.update(); super().leaveEvent(e)


class CustomTitleBar(QWidget):
    """Frameless-compatible title bar: drag, minimize, maximize/restore, close.
    Attach to the top of the main window's central widget layout."""

    _RESIZE_MARGIN = 6

    def __init__(self, window: QWidget, parent=None):
        super().__init__(parent)
        self._win = window
        self._drag_pos = None
        self._resize_edge = None
        self._hovering_controls = False

        self.setObjectName("titleBar")
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 12, 0)
        h.setSpacing(0)

        self._min_btn = _MacDot("#febc2e", "#febc2e", "\u2212", self)
        self._min_btn.setToolTip("Minimize")
        self._min_btn.clicked.connect(self._win.showMinimized)

        self._max_btn = _MacDot("#28c840", "#28c840", "\u2b0c", self)
        self._max_btn.setToolTip("Maximize")
        self._max_btn.clicked.connect(self._toggle_max)

        self._close_btn = _MacDot("#ff5f57", "#ff5f57", "\u00d7", self)
        self._close_btn.setToolTip("Close")
        self._close_btn.clicked.connect(self._win.close)

        h.addStretch(1)

        self._title_lbl = QLabel("")
        self._title_lbl.setObjectName("tbTitle")
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(self._title_lbl)

        h.addStretch(1)

        dots_layout = QHBoxLayout()
        dots_layout.setContentsMargins(0, 0, 8, 0)
        dots_layout.setSpacing(8)
        dots_layout.addWidget(self._min_btn)
        dots_layout.addWidget(self._max_btn)
        dots_layout.addWidget(self._close_btn)
        h.addLayout(dots_layout)

        self.setMouseTracking(True)

    def refresh_style(self):
        self._title_lbl.setStyleSheet(f"color: {_t.SUB};")
        self.setStyleSheet(f"QWidget#titleBar {{ background: {_t.BG}; border-bottom: none; }}")
        for dot in (self._close_btn, self._min_btn, self._max_btn):
            dot.update()

    def _toggle_max(self):
        if self._win.isMaximized():
            self._win.showNormal()
            self._max_btn.setToolTip("Maximize")
        else:
            self._win.showMaximized()
            self._max_btn.setToolTip("Restore")

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            if self._win.isMaximized():
                self._win.showNormal()
                self._max_btn.setToolTip("Maximize")
                self._drag_pos = QPoint(self._win.width() // 2, self.height() // 2)
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
