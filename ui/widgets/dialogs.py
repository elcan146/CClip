#!/usr/bin/env python3
"""Dialog widgets for CClip."""

from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal, QPoint
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget, QDialog, QVBoxLayout, QGraphicsDropShadowEffect

import utils.theme as _t


class ToastPopup(QFrame):
    def __init__(self, parent, on_save, on_discard):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(
            f"QFrame#card {{ background: {_t.CARD}; border: 1px solid {_t.ACC}; border-radius: 10px; }}"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 8, 10, 8); h.setSpacing(10)
        dot = QLabel("●"); dot.setStyleSheet(f"color: {_t.YEL}; font-size: 16px; border: none;")
        h.addWidget(dot)
        lbl = QLabel("Unsaved changes"); lbl.setObjectName("h3")
        lbl.setStyleSheet("border: none;")
        h.addWidget(lbl)
        h.addSpacing(8)
        discard_btn = QPushButton("Discard"); discard_btn.setFixedHeight(28)
        discard_btn.clicked.connect(on_discard)
        h.addWidget(discard_btn)
        save_btn = QPushButton("Save"); save_btn.setObjectName("primary"); save_btn.setFixedHeight(28)
        save_btn.clicked.connect(on_save)
        h.addWidget(save_btn)
        self.adjustSize()
        self._shown = False
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(260)
        self.hide()

    def slide_in(self):
        parent = self.parentWidget()
        if not parent:
            print("Toast: No parent widget!")
            return
        self.adjustSize()
        # Bottom-right of the main content area, left of the right sidebar clock panel.
        RIGHT_SIDEBAR_W = 266  # matches self.right_bar.setFixedWidth(266)
        right_edge = parent.width() - RIGHT_SIDEBAR_W - 20
        end_pos = QPoint(max(24, right_edge - self.width()), parent.height() - self.height() - 24)
        print(f"Toast: parent={parent.width()}x{parent.height()}, toast={self.width()}x{self.height()}, end_pos={end_pos}")
        if self._shown:
            self.move(end_pos); return
        self._shown = True
        start_pos = QPoint(end_pos.x(), parent.height() + 20)
        self.move(start_pos)
        self.show(); self.raise_()
        self._anim.stop()
        try: self._anim.finished.disconnect()
        except TypeError: pass
        self._anim.setStartValue(start_pos)
        self._anim.setEndValue(end_pos)
        self._anim.start()
        print(f"Toast slide_in called, moving from {start_pos} to {end_pos}")

    def slide_out(self):
        if not self._shown:
            return
        parent = self.parentWidget()
        if not parent:
            self.hide(); self._shown = False; return
        start_pos = self.pos()
        end_pos = QPoint(start_pos.x(), parent.height() + 20)
        self._anim.stop()
        try: self._anim.finished.disconnect()
        except TypeError: pass
        self._anim.setStartValue(start_pos)
        self._anim.setEndValue(end_pos)
        def done():
            self.hide(); self._shown = False
        self._anim.finished.connect(done)
        self._anim.start()


class _OverlayShade(QWidget):
    """Semi-opaque backdrop drawn behind modal dialogs. Lives in the main window."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: rgba(0, 0, 0, 130);")
        self.hide()


class ModernDialog(QDialog):
    """Frameless, rounded, in-app modal. Returns the role string of the clicked button."""

    def __init__(self, parent, title, message, icon_char="\u26A0",
                 icon_color=None, buttons=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self._clicked = None

        outer = QFrame(self)
        outer.setObjectName("modernDialogFrame")
        outer.setStyleSheet(
            f"QFrame#modernDialogFrame {{ background: {_t.CARD}; "
            f"border: 1px solid {_t.BOR}; border-radius: 14px; }}"
            f"QLabel {{ background: transparent; border: none; color: {_t.TEXT}; }}"
        )
        shadow = QGraphicsDropShadowEffect(outer)
        shadow.setBlurRadius(48); shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 200))
        outer.setGraphicsEffect(shadow)

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 30, 30, 30)
        root.addWidget(outer)

        v = QVBoxLayout(outer)
        v.setContentsMargins(26, 22, 26, 18); v.setSpacing(14)

        head = QHBoxLayout(); head.setSpacing(14)
        ic = icon_color or _t.YEL
        icon_lbl = QLabel("!" if icon_char == "\u26A0" else icon_char)
        icon_lbl.setFixedSize(50, 50)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(
            f"background: {ic}22; color: {ic}; font-size: 24px; "
            f"font-weight: 700; border: 1px solid {ic}55; border-radius: 25px;"
        )
        head.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        tcol = QVBoxLayout(); tcol.setSpacing(4)
        t = QLabel(title)
        t.setStyleSheet(f"color: {_t.TEXT}; font-size: 17px; font-weight: 600; "
                        f"background: transparent; border: none;")
        tcol.addWidget(t)
        if message:
            m = QLabel(message); m.setWordWrap(True)
            m.setStyleSheet(f"color: {_t.SUB}; font-size: 13px; "
                            f"background: transparent; border: none;")
            tcol.addWidget(m)
        head.addLayout(tcol, 1)
        v.addLayout(head)

        bh = QHBoxLayout(); bh.setSpacing(8); bh.addStretch(1)
        for (label, role, is_primary) in (buttons or [("OK", "ok", True)]):
            b = QPushButton(label)
            if is_primary:
                b.setObjectName("primary")
            b.setFixedHeight(34); b.setMinimumWidth(110)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.clicked.connect(lambda _=False, r=role: self._on_pick(r))
            bh.addWidget(b)
        v.addLayout(bh)

        # fade-in
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(160); self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.setStartValue(0.0); self._fade.setEndValue(1.0)

    def _on_pick(self, role):
        self._clicked = role
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        # centre on parent
        p = self.parent()
        if p is not None:
            pw = p.window()
            if pw is not None:
                geo = pw.geometry()
                self.adjustSize()
                cx = geo.x() + (geo.width() - self.width()) // 2
                cy = geo.y() + (geo.height() - self.height()) // 2
                self.move(cx, cy)
        self._fade.start()

    @staticmethod
    def ask(parent, title, message, buttons,
            icon_char="\u26A0", icon_color=None):
        dlg = ModernDialog(parent, title, message, icon_char, icon_color, buttons)
        dlg.exec()
        return dlg._clicked
