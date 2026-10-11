#!/usr/bin/env python3
"""Card and progress widgets for CClip."""

from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QAbstractAnimation, QVariantAnimation
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QProgressBar

import utils.theme as _t


class Card(QFrame):
    def __init__(self, title="", subtitle="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)
        if title:
            t = QLabel(title); t.setObjectName("h3"); v.addWidget(t)
        if subtitle:
            s = QLabel(subtitle); s.setObjectName("subSm"); v.addWidget(s)
        if title or subtitle:
            line = QFrame(); line.setObjectName("hairline")
            v.addWidget(line)
        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 4, 0, 0)
        self._body.setSpacing(2)
        v.addLayout(self._body)

    def body(self):
        return self._body


class AnimatedProgressBar(QProgressBar):
    def __init__(self, parent=None, color=None, height=8):
        if color is None:
            color = _t.ACC
        super().__init__(parent)
        self.setRange(0, 1000)
        self.setValue(0)
        self.setTextVisible(False)
        self.setFixedHeight(height)
        radius = max(2, height // 2)
        self.setStyleSheet(f"""
            QProgressBar {{ background: {_t.CARD2}; border: none; border-radius: {radius}px; }}
            QProgressBar::chunk {{ background: {color}; border-radius: {radius}px; }}
        """)
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def animate_to(self, fraction):
        f = max(0.0, min(1.0, float(fraction)))
        target = int(f * 1000)
        if self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()
        self._anim.setStartValue(self.value())
        self._anim.setEndValue(target)
        self._anim.start()


class CountUpLabel(QLabel):
    def __init__(self, value=0, color=None, parent=None):
        if color is None:
            color = _t.ACC
        super().__init__("0", parent)
        self.setObjectName("counterValue")
        self.setStyleSheet(f"color: {color};")
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(900)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.setText(str(int(v))))
        self.set_value_animated(value)

    def set_value_animated(self, value):
        try:
            cur = int(self.text())
        except Exception:
            cur = 0
        target = int(value or 0)
        if cur == target:
            self.setText(str(target)); return
        if self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()
        self._anim.setStartValue(cur)
        self._anim.setEndValue(target)
        self._anim.start()
