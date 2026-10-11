#!/usr/bin/env python3
"""Picker widgets for CClip."""

from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal, QPoint, QTimer
from PyQt6.QtGui import QFont, QFontDatabase, QCursor
from PyQt6.QtWidgets import QWidget, QPushButton, QLabel, QDialog, QFrame, QVBoxLayout, QHBoxLayout, QScrollArea, QApplication

from config import S
from ui.widgets.inputs import FontOptionRow
import utils.theme as _t


class AnimatedComboPicker(QWidget):
    """Premium animated dropdown picker (like FontPicker but for text options).
    Shows a pill-shaped button with animated fade+slide popup menu."""
    optionChanged = pyqtSignal(str)

    def __init__(self, options, current=None, width=160, parent=None):
        super().__init__(parent)
        self._options = list(options)
        self._current = current or (self._options[0] if self._options else "")
        self._popup = None
        self._open = False
        self._width = width
        self.setFixedSize(width, 36)

        self._btn = QPushButton(self)
        self._btn.setGeometry(0, 0, width, 36)
        self._btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn.setFlat(True)
        self._btn.clicked.connect(self._toggle_popup)

        self._arrow = QLabel("\u25BE", self)
        self._arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._arrow.setFont(QFont(S.get("ui_font", "Segoe UI Variable"), 11))
        self._arrow.setGeometry(width - 26, 1, 22, 34)

        self._refresh_btn()

    def current(self):
        return self._current

    def setCurrent(self, option):
        if option in self._options:
            self._current = option
            self._refresh_btn()
            self.optionChanged.emit(option)

    def currentText(self):
        return self._current

    def _refresh_btn(self):
        self._btn.setText(f"  {self._current}")
        self._btn.setFont(QFont(S.get("ui_font", "Segoe UI Variable"), 12))
        self._btn.setStyleSheet(
            f"QPushButton {{ background: {_t.CARD2}; color: {_t.TEXT}; "
            f"border: 1px solid {_t.BOR}; border-radius: 10px; "
            f"padding: 6px 28px 6px 12px; text-align: left; }}"
            f"QPushButton:hover {{ border-color: {_t.ACC}; background: {_t.CARD3}; }}"
        )
        arrow_col = _t.ACC if self._open else _t.SUB
        self._arrow.setStyleSheet(
            f"background: transparent; border: none; color: {arrow_col}; font-weight: 700;"
        )
        self._arrow.raise_()

    def _toggle_popup(self):
        if self._popup is not None and self._popup.isVisible():
            self._close_popup()
            return
        self._show_popup()

    def _close_popup(self):
        if self._popup:
            old = self._popup
            self._popup = None
            self._open = False
            self._arrow.setText("\u25BE")
            self._refresh_btn()
            old.close()
            old.deleteLater()

    def _show_popup(self):
        win = self.window()
        pop = QDialog(win)
        pop.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        # No white background - use app's color scheme
        pop.setStyleSheet(f"background: {_t.CARD}; border: none;")
        self._popup = pop
        self._open = True
        self._arrow.setText("\u25B4")
        self._refresh_btn()
        pop.destroyed.connect(lambda: (setattr(self, "_popup", None),
                                       setattr(self, "_open", False)))

        # Styled frame inside
        outer = QFrame(pop)
        outer.setStyleSheet(
            f"QFrame {{ background: {_t.CARD2}; border: 1px solid {_t.BOR}; border-radius: 10px; }}"
        )
        wrap = QVBoxLayout(pop)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(outer)

        v = QVBoxLayout(outer)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(2)

        # Scrollable options
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(2, 2, 2, 2)
        iv.setSpacing(2)

        for opt in self._options:
            is_active = (opt == self._current)
            btn = QPushButton(opt)
            btn.setFlat(True)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setFixedHeight(34)
            if is_active:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {_t.ACC}; color: #ffffff; "
                    f"border-radius: 8px; text-align: left; padding-left: 12px; "
                    f"font-weight: 600; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {_t.TEXT}; "
                    f"border-radius: 8px; text-align: left; padding-left: 12px; }}"
                    f"QPushButton:hover {{ background: {_t.CARD3}; }}"
                )

            def mk(choice):
                def _pick():
                    self._current = choice
                    self._open = False
                    self._arrow.setText("\u25BE")
                    self._refresh_btn()
                    if self._popup:
                        pop_to_close = self._popup
                        self._popup = None
                        pop_to_close.close()
                        pop_to_close.deleteLater()
                    QTimer.singleShot(0, lambda: self.optionChanged.emit(choice))
                return _pick

            btn.clicked.connect(mk(opt))
            iv.addWidget(btn)

        iv.addStretch(1)
        scroll.setWidget(inner)
        v.addWidget(scroll)

        # Size and position
        pop.resize(self.width() + 10, min(200, len(self._options) * 38 + 20))
        gp = self.mapToGlobal(QPoint(0, self.height() + 4))

        screen = QApplication.primaryScreen().availableGeometry()
        x = max(screen.x(), min(gp.x(), screen.x() + screen.width() - pop.width()))
        y = gp.y()
        if y + pop.height() > screen.y() + screen.height():
            y = self.mapToGlobal(QPoint(0, -pop.height() - 4)).y()
        y = max(screen.y(), y)

        # Slide-in + fade animation
        start_pos = QPoint(x, y + 10)
        end_pos = QPoint(x, y)
        pop.move(start_pos)
        pop.setWindowOpacity(0.0)
        pop.show()

        fade = QPropertyAnimation(pop, b"windowOpacity", pop)
        fade.setDuration(180)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)

        slide = QPropertyAnimation(pop, b"pos", pop)
        slide.setDuration(180)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide.setStartValue(start_pos)
        slide.setEndValue(end_pos)

        fade.start()
        slide.start()


class FontPicker(QWidget):
    """Compact pill-shaped button that reveals an animated fade+slide popup
    listing fonts rendered in themselves.  Arrow flips on open/close."""
    fontChanged = pyqtSignal(str)

    def __init__(self, fonts, current, width=220, parent=None):
        super().__init__(parent)
        self._fonts = [f for f in fonts]
        if current and current not in self._fonts:
            self._fonts.insert(0, current)
        self._current = current or (self._fonts[0] if self._fonts else "Segoe UI")
        self._popup = None
        self._installed_fonts = set(QFontDatabase.families())
        self._open = False
        self.setFixedSize(width, 36)

        self._btn = QPushButton(self)
        self._btn.setGeometry(0, 0, width, 36)
        self._btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn.setFlat(True)
        self._btn.clicked.connect(self._toggle_popup)
        self._arrow = QLabel("\u25BE", self)
        self._arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._arrow.setFont(QFont(S.get("ui_font", "Segoe UI Variable"), 11))
        self._arrow.setGeometry(width - 26, 1, 22, 34)
        self._refresh_btn()

    def current(self):
        return self._current

    def setCurrent(self, fam):
        self._current = fam
        if fam not in self._fonts:
            self._fonts.insert(0, fam)
        self._refresh_btn()

    def _refresh_btn(self):
        self._btn.setText(f"  {self._current}")
        if self._current in self._installed_fonts:
            self._btn.setFont(QFont(self._current, 11))
        else:
            self._btn.setFont(QFont(S.get("ui_font", "Segoe UI Variable"), 11))
        self._btn.setStyleSheet(
            f"QPushButton {{ background: {_t.CARD2}; color: {_t.TEXT}; "
            f"border: none; border-radius: 10px; "
            f"padding: 6px 28px 6px 10px; text-align: left; }}"
            f"QPushButton:hover {{ border-color: {_t.ACC}; background: {_t.CARD3}; }}"
        )
        arrow_col = _t.ACC if self._open else _t.SUB
        self._arrow.setStyleSheet(
            f"background: transparent; border: none; color: {arrow_col}; font-weight: 700;"
        )
        self._arrow.raise_()

    def _toggle_popup(self):
        if self._popup is not None and self._popup.isVisible():
            self._close_popup()
            return
        self._show_popup()

    def _close_popup(self):
        if self._popup:
            old = self._popup
            self._popup = None
            self._open = False
            self._arrow.setText("\u25BE")
            self._refresh_btn()
            old.close()
            old.deleteLater()

    def _show_popup(self):
        win = self.window()
        pop = QDialog(win)
        pop.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        # Fix white background around popup
        pop.setStyleSheet(f"background: {_t.CARD}; border: none;")
        self._popup = pop
        self._open = True
        self._arrow.setText("\u25B4")
        self._refresh_btn()
        pop.destroyed.connect(lambda: (setattr(self, "_popup", None),
                                       setattr(self, "_open", False)))

        outer = QFrame(pop); outer.setObjectName("fpFrame")
        outer.setStyleSheet(
            f"QFrame#fpFrame {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 10px; }}"
        )

        wrap = QVBoxLayout(pop); wrap.setContentsMargins(0, 0, 0, 0); wrap.addWidget(outer)

        v = QVBoxLayout(outer); v.setContentsMargins(6, 6, 6, 6); v.setSpacing(4)

        scroll = QScrollArea(); scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}")
        inner = QWidget(); inner.setStyleSheet("background: transparent;")
        iv = QVBoxLayout(inner); iv.setContentsMargins(2, 2, 2, 2); iv.setSpacing(2)

        for f in self._fonts:
            is_active = (f == self._current)
            btn = FontOptionRow(f, active=is_active, available=f in self._installed_fonts)

            def mk(ff):
                def _pick(_ff=None):
                    self._current = ff
                    self._open = False
                    self._arrow.setText("\u25BE")
                    self._refresh_btn()
                    if self._popup:
                        pop_to_close = self._popup
                        self._popup = None
                        pop_to_close.close()
                        pop_to_close.deleteLater()
                    QTimer.singleShot(0, lambda: self.fontChanged.emit(ff))
                return _pick
            btn.clicked.connect(mk(f))
            iv.addWidget(btn)

        iv.addStretch(1)
        scroll.setWidget(inner)
        v.addWidget(scroll)

        pop.resize(self.width() + 10, min(304, len(self._fonts) * 42 + 20))
        gp = self.mapToGlobal(QPoint(0, self.height() + 2))

        # Screen boundary clamping
        screen = QApplication.primaryScreen().availableGeometry()
        x = max(screen.x(), min(gp.x(), screen.x() + screen.width() - pop.width()))
        y = gp.y()
        if y + pop.height() > screen.y() + screen.height():
            y = self.mapToGlobal(QPoint(0, -pop.height() - 2)).y()
        y = max(screen.y(), y)

        # Slide-in animation from slightly below
        start_pos = QPoint(x, y + 10)
        end_pos   = QPoint(x, y)
        pop.move(start_pos)
        pop.setWindowOpacity(0.0)
        pop.show()

        fade = QPropertyAnimation(pop, b"windowOpacity", pop)
        fade.setDuration(200); fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(0.0); fade.setEndValue(1.0)

        slide = QPropertyAnimation(pop, b"pos", pop)
        slide.setDuration(200); slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide.setStartValue(start_pos); slide.setEndValue(end_pos)

        fade.start(); slide.start()
        pop._fp_fade = fade; pop._fp_slide = slide
