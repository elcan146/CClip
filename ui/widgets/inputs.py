#!/usr/bin/env python3
"""Input widgets for CClip."""

from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel

import utils.theme as _t


class ModernComboBox(QComboBox):
    """QComboBox with a stable, theme-painted chevron overlay.
    Popup has NO drop-shadow and slides in with a fade+translate animation.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMinimumHeight(36)
        self.setFrame(False)
        # Remove blue focus line on Windows
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Hide native arrow indicator
        self.setStyleSheet("""
            QComboBox { outline: none; border: none; padding-right: 8px; }
            QComboBox::drop-down { width: 0px; border: none; background: transparent; }
            QComboBox::down-arrow { image: none; width: 0px; height: 0px; border: none; }
        """)

    def showPopup(self):
        # Remove drop-shadow before popup appears
        view = self.view()
        win = view.window()
        win.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        # Fix white background around popup
        win.setStyleSheet(f"background: {_t.CARD}; border: none;")
        super().showPopup()
        # Fade-in animation on the popup
        popup_win = view.window()
        try:
            popup_win.setWindowOpacity(0.0)
            anim = QPropertyAnimation(popup_win, b"windowOpacity", self)
            anim.setDuration(180)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.start()
            self._popup_fade_anim = anim
        except Exception:
            pass

    def hidePopup(self):
        super().hidePopup()


class FontOptionRow(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, family, active=False, available=True, parent=None):
        super().__init__(parent)
        self._family = family
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(40)
        self.setStyleSheet(self._style(active))

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 14, 0)
        h.setSpacing(12)

        name = QLabel(family)
        name_col = "#ffffff" if active else (_t.TEXT if available else _t.SUB_DK)
        name.setStyleSheet(f"background: transparent; border: none; color: {name_col}; font-family: '{family}'; font-size: 13px;")
        h.addWidget(name, 0)

        sample = QLabel("Lorem ipsum")
        sample.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        sample_col = "#ffffff" if active else (_t.TEXT if available else _t.SUB_DK)
        sample.setStyleSheet(f"background: transparent; border: none; color: {sample_col}; font-family: '{family}'; font-size: 18px;")
        if not available:
            sample.setToolTip("Font is not installed on this Windows account, so Qt previews it with a fallback.")
        h.addWidget(sample, 1)

        if not available:
            tag = QLabel("not installed")
            tag.setStyleSheet(
                f"background: {_t.CARD2}; color: {_t.SUB_DK}; border: 1px solid {_t.BOR_SFT}; "
                f"border-radius: 5px; padding: 2px 6px; font-size: 10px;"
            )
            h.addWidget(tag)

    def _style(self, active=False):
        if active:
            return (
                f"QFrame {{ background: {_t.ACC2}; color: #ffffff; border: none; border-radius: 6px; }}"
                f"QLabel {{ color: #ffffff; }}"
            )
        return (
            f"QFrame {{ background: transparent; color: {_t.TEXT}; border: none; border-radius: 6px; }}"
            f"QFrame:hover {{ background: {_t.CARD3}; }}"
            f"QLabel {{ color: {_t.TEXT}; }}"
        )

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._family)
        super().mousePressEvent(e)
