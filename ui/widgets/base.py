#!/usr/bin/env python3
"""Base UI widgets with custom animations for CClip."""

from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QPushButton, QStyleOptionButton


_OrigQPushButton = QPushButton


class CustomQPushButton(_OrigQPushButton):
    """Drop-in QPushButton with macOS spring-press scale animation on every click."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bscale = 1.0
        self._bpa = QPropertyAnimation(self, b"_bscale_prop", self)
        self._bpa.setDuration(70)
        self._bpa.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._bra = QPropertyAnimation(self, b"_bscale_prop", self)
        self._bra.setDuration(420)
        self._bra.setEasingCurve(QEasingCurve.Type.OutBack)

    def _get_bscale(self): return self._bscale
    def _set_bscale(self, v):
        self._bscale = float(v)
        self.update()
    _bscale_prop = pyqtProperty(float, _get_bscale, _set_bscale)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._bra.stop()
            self._bpa.setStartValue(self._bscale)
            self._bpa.setEndValue(0.91)
            self._bpa.start()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._bpa.stop()
        self._bra.setStartValue(self._bscale)
        self._bra.setEndValue(1.0)
        self._bra.start()
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        if abs(self._bscale - 1.0) > 0.003:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            cx, cy = self.width() / 2.0, self.height() / 2.0
            p.translate(cx, cy)
            p.scale(self._bscale, self._bscale)
            p.translate(-cx, -cy)
            opt = QStyleOptionButton()
            self.initStyleOption(opt)
            self.style().drawControl(
                self.style().ControlElement.CE_PushButton, opt, p, self)
        else:
            super().paintEvent(e)
