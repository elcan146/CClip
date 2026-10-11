#!/usr/bin/env python3
"""Calendar widget for CClip."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

import utils.theme as _t


class ModernCalendar(QWidget):
    """Themed, minimal monthly calendar used inside the clock popup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from datetime import date as _date
        today = _date.today()
        self._today = today
        self._selected = today
        self._view_month = today.replace(day=1)
        self._build()

    def _build(self):
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)

        head = QWidget(); hh = QHBoxLayout(head); hh.setContentsMargins(0, 0, 0, 0); hh.setSpacing(8)
        nav_style = (f"QPushButton {{ background: {_t.CARD2}; color: {_t.TEXT}; "
                     f"border: 1px solid {_t.BOR_SFT}; border-radius: 16px; font-size: 15px; "
                     f"font-weight: 600; }}"
                     f"QPushButton:hover {{ background: {_t.CARD3}; border-color: {_t.ACC}; }}")
        self._prev_btn = QPushButton("\u2039"); self._prev_btn.setFixedSize(32, 32)
        self._prev_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._prev_btn.setStyleSheet(nav_style)
        self._prev_btn.clicked.connect(lambda: self._nav(-1))

        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setStyleSheet(
            f"color: {_t.TEXT}; font-size: 14px; font-weight: 600; "
            f"background: transparent; border: none;"
        )

        self._next_btn = QPushButton("\u203A"); self._next_btn.setFixedSize(32, 32)
        self._next_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._next_btn.setStyleSheet(nav_style)
        self._next_btn.clicked.connect(lambda: self._nav(1))

        hh.addWidget(self._prev_btn)
        hh.addWidget(self._title_lbl, 1)
        hh.addWidget(self._next_btn)
        v.addWidget(head)

        wd = QWidget(); wh = QHBoxLayout(wd); wh.setContentsMargins(0, 0, 0, 0); wh.setSpacing(4)
        for name in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
            L = QLabel(name); L.setAlignment(Qt.AlignmentFlag.AlignCenter)
            L.setStyleSheet(
                f"color: {_t.SUB}; font-size: 11px; font-weight: 600; "
                f"background: transparent; border: none;"
            )
            wh.addWidget(L, 1)
        v.addWidget(wd)

        self._grid_host = QWidget()
        self._grid_v = QVBoxLayout(self._grid_host)
        self._grid_v.setContentsMargins(0, 0, 0, 0); self._grid_v.setSpacing(4)
        v.addWidget(self._grid_host)

        self._render()

    def _nav(self, delta):
        from datetime import date as _date
        y, m = self._view_month.year, self._view_month.month
        m += delta
        if m < 1:
            m = 12; y -= 1
        elif m > 12:
            m = 1; y += 1
        self._view_month = _date(y, m, 1)
        self._render()

    def _render(self):
        from calendar import monthrange
        from datetime import date as _date

        while self._grid_v.count():
            item = self._grid_v.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        names = ["January", "February", "March", "April", "May", "June",
                 "July", "August", "September", "October", "November", "December"]
        y, m = self._view_month.year, self._view_month.month
        self._title_lbl.setText(f"{names[m-1]} {y}")

        first_wd, ndays = monthrange(y, m)
        day = 1
        for row in range(6):
            rw = QWidget(); rh = QHBoxLayout(rw)
            rh.setContentsMargins(0, 0, 0, 0); rh.setSpacing(4)
            for col in range(7):
                if row == 0 and col < first_wd:
                    spacer = QLabel(""); spacer.setFixedHeight(34)
                    spacer.setStyleSheet("background: transparent; border: none;")
                    rh.addWidget(spacer, 1)
                elif day > ndays:
                    spacer = QLabel(""); spacer.setFixedHeight(34)
                    spacer.setStyleSheet("background: transparent; border: none;")
                    rh.addWidget(spacer, 1)
                else:
                    d = _date(y, m, day)
                    is_today = (d == self._today)
                    is_sel = (d == self._selected)
                    b = QPushButton(str(day))
                    b.setFixedHeight(34)
                    b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                    if is_today:
                        base = (f"background: {_t.ACC2}; color: #ffffff; border: none; "
                                f"border-radius: 17px; font-weight: 600;")
                        hover = f"background: {_t.ACC_DK};"
                    elif is_sel:
                        base = (f"background: {_t.CARD3}; color: {_t.TEXT}; border: 2px solid {_t.ACC}; "
                                f"border-radius: 17px; font-weight: 600;")
                        hover = f"background: {_t.CARD3};"
                    else:
                        base = (f"background: transparent; color: {_t.TEXT}; border: none; "
                                f"border-radius: 17px;")
                        hover = f"background: {_t.CARD3};"
                    b.setStyleSheet(
                        f"QPushButton {{ {base} }}"
                        f"QPushButton:hover {{ {hover} }}"
                    )

                    def mk(dd):
                        def _pick():
                            self._selected = dd
                            self._render()
                        return _pick
                    b.clicked.connect(mk(d))
                    rh.addWidget(b, 1)
                    day += 1
            self._grid_v.addWidget(rw)
