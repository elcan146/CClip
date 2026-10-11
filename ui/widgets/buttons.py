#!/usr/bin/env python3
"""Button widgets for CClip."""

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QAbstractAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QCursor
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

import utils.theme as _t


class NavButton(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, key, icon_text, label, parent=None):
        super().__init__(parent)
        self.key = key
        self._checked = False
        self._hover = False
        self.setFixedHeight(40)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 0, 14, 0)
        h.setSpacing(14)
        self._icon = QLabel(icon_text)
        self._icon.setFixedWidth(22)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl = QLabel(label)
        h.addWidget(self._icon)
        h.addWidget(self._lbl)
        h.addStretch(1)
        self._update_style()

    def setChecked(self, v):
        v = bool(v)
        if self._checked == v:
            return
        self._checked = v
        self._update_style()

    def isChecked(self):
        return self._checked

    def enterEvent(self, e):
        self._hover = True
        self._update_style()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._update_style()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(e)

    def _update_style(self):
        if self._checked:
            bg = _t.CARD3; tc = _t.TEXT; ic = _t.ACC; weight = "600"
        elif self._hover:
            bg = _t.CARD3; tc = _t.TEXT; ic = _t.TEXT; weight = "500"
        else:
            bg = "transparent"; tc = _t.SUB; ic = _t.SUB; weight = "500"
        self.setStyleSheet(f"NavButton {{ background: {bg}; border-radius: 6px; }}")
        self._icon.setStyleSheet(
            f"color: {ic}; font-size: 18px; font-family: 'Segoe UI Symbol','Segoe UI Variable',sans-serif; background: transparent;"
        )
        self._lbl.setStyleSheet(
            f"color: {tc}; font-size: 13px; font-weight: {weight}; background: transparent;"
        )


class ToggleSwitch(QWidget):
    """Modern Fluent-style toggle switch with animated knob.
    Knob is a perfect solid circle — no clipping artifacts."""
    toggled = pyqtSignal(bool)

    _KNOB_OFF  = 4.0    # left resting position (px)
    _KNOB_SIZE = 18     # diameter (px)

    def __init__(self, on_color=None, parent=None):
        if on_color is None:
            on_color = _t.ACC2
        super().__init__(parent)
        self._checked = False
        self._on_color = on_color
        self._knob_pos = self._KNOB_OFF
        self.setFixedSize(50, 26)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._anim = QPropertyAnimation(self, b"knob_pos", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)

    def isChecked(self):
        return self._checked

    def setChecked(self, v):
        v = bool(v)
        if self._checked == v:
            return
        self._checked = v
        if not self.isVisible():
            self._knob_pos = float(self.width() - self._KNOB_SIZE - self._KNOB_OFF) if v else self._KNOB_OFF
            self.update()
        else:
            self._animate()
        self.toggled.emit(v)

    def _animate(self):
        target = float(self.width() - self._KNOB_SIZE - self._KNOB_OFF) if self._checked else self._KNOB_OFF
        if self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()
        self._anim.setStartValue(float(self._knob_pos))
        self._anim.setEndValue(target)
        self._anim.start()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)

    def get_knob(self):
        return self._knob_pos

    def set_knob(self, v):
        self._knob_pos = float(v)
        self.update()

    knob_pos = pyqtProperty(float, get_knob, set_knob)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # ── Track ───────────────────────────────────────────────────
        if self._checked:
            bg_col     = QColor(self._on_color)
            border_col = QColor(self._on_color)
        else:
            bg_col     = QColor(_t.CARD3)
            border_col = QColor(_t.BOR)
        p.setBrush(QBrush(bg_col))
        p.setPen(QPen(border_col, 1))
        # radius=10 ensures the knob (at y=4, h=18) stays within the track
        p.drawRoundedRect(0, 0, self.width() - 1, self.height() - 1, 10, 10)
        # ── Knob shadow (slightly offset, no pen) ───────────────────
        p.setBrush(QBrush(QColor(0, 0, 0, 35)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(self._knob_pos) + 1, 5, self._KNOB_SIZE, self._KNOB_SIZE)
        # ── Knob (perfect circle, fully inside track) ────────────────
        p.setBrush(QBrush(QColor("#ffffff")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(self._knob_pos), 4, self._KNOB_SIZE, self._KNOB_SIZE)
