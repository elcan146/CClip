#!/usr/bin/env python3
"""Color picker widgets for CClip."""

from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal, QPoint
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QLinearGradient, QPainterPath, QCursor, QFont
from PyQt6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QPushButton, QDialog, QGraphicsDropShadowEffect

from config import S


class HSVPlane(QWidget):
    """Saturation / Value square used inside the custom color picker."""
    colorChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(338, 338)
        self._hue = 0
        self._sat = 255
        self._val = 255
        self._dragging = False
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def color(self):
        return QColor.fromHsv(max(0, self._hue), self._sat, self._val)

    def setHue(self, h):
        self._hue = max(0, int(h)); self.update(); self.colorChanged.emit()

    def setColor(self, col: QColor):
        h, s, v, _ = col.getHsv()
        self._hue = max(0, h); self._sat = s; self._val = v
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width(); h = self.height()
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 8, 8)
        p.setClipPath(path)
        p.fillPath(path, QBrush(QColor.fromHsv(max(0, self._hue), 255, 255)))
        white = QLinearGradient(0, 0, w, 0)
        white.setColorAt(0, QColor(255, 255, 255, 255))
        white.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillPath(path, QBrush(white))
        black = QLinearGradient(0, 0, 0, h)
        black.setColorAt(0, QColor(0, 0, 0, 0))
        black.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillPath(path, QBrush(black))
        p.setClipping(False)
        p.setPen(QPen(QColor("#2f2f2f"), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        # marker
        mx = int(self._sat / 255 * (w - 1))
        my = int((1 - self._val / 255) * (h - 1))
        mx = max(8, min(w - 9, mx))
        my = max(8, min(h - 9, my))
        p.setPen(QPen(QColor("black"), 1))
        p.drawEllipse(mx - 7, my - 7, 14, 14)
        p.setPen(QPen(QColor("white"), 2))
        p.drawEllipse(mx - 6, my - 6, 12, 12)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_pos(e.pos())

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._update_pos(e.pos())

    def mouseReleaseEvent(self, _e):
        self._dragging = False

    def _update_pos(self, pt):
        x = max(0, min(self.width() - 1, pt.x()))
        y = max(0, min(self.height() - 1, pt.y()))
        self._sat = int(255 * x / max(1, self.width() - 1))
        self._val = int(255 * (1 - y / max(1, self.height() - 1)))
        self.update(); self.colorChanged.emit()


class HueBar(QWidget):
    """Vertical hue strip."""
    hueChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 338)
        self._hue = 0
        self._dragging = False
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def setHue(self, h):
        self._hue = max(0, int(h)); self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width(); h = self.height()
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 8, 8)
        grad = QLinearGradient(0, 0, 0, h)
        for stop, hue in (
            (0.0, 0), (1/6, 60), (2/6, 120),
            (3/6, 180), (4/6, 240), (5/6, 300), (1.0, 359)
        ):
            grad.setColorAt(stop, QColor.fromHsv(hue, 255, 255))
        p.fillPath(path, QBrush(grad))
        p.setPen(QPen(QColor("#2f2f2f"), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        py = int(self._hue / 359 * (h - 1)) if h > 1 else 0
        py = max(4, min(h - 5, py))
        p.setPen(QPen(QColor("black"), 1))
        p.drawRect(0, py - 3, w - 1, 6)
        p.setPen(QPen(QColor("white"), 2))
        p.drawRect(1, py - 2, w - 3, 4)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True; self._update_pos(e.pos().y())

    def mouseMoveEvent(self, e):
        if self._dragging: self._update_pos(e.pos().y())

    def mouseReleaseEvent(self, _e):
        self._dragging = False

    def _update_pos(self, y):
        y = max(0, min(self.height() - 1, y))
        self._hue = int(359 * y / max(1, self.height() - 1))
        self.update(); self.hueChanged.emit(self._hue)


class ModernColorPicker(QDialog):
    """Beautiful, modern in-app color picker.
    Layout: HSV square | Hue bar | Right panel (preview + inputs + presets).
    Dark glass-morphism style with subtle gradients and clean typography.
    """

    # Preset swatches shown at the bottom for quick access
    PRESETS = [
        "#FF6B6B", "#FF9F43", "#FBBF24", "#6CCB5F", "#4FD1C5",
        "#60CDFF", "#4CC2FF", "#0078D4", "#A855F7", "#EC4899",
        "#FFFFFF", "#B0B0B0", "#5C5C5C", "#1A1A1A", "#000000",
    ]

    def __init__(self, initial, parent=None, title="Pick a Color"):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self._result = None

        start_col = QColor(initial) if QColor(initial).isValid() else QColor("#ffffff")
        self._initial_hex = start_col.name().upper()

        # ── Outer frame ─────────────────────────────────────────────
        outer = QFrame(self)
        outer.setObjectName("mcpOuter")
        outer.setStyleSheet("""
            QFrame#mcpOuter {
                background: qlineargradient(x1:0,y1:0, x2:0,y2:1,
                    stop:0 #3a3a3a, stop:1 #2b2b2b);
                border: 1px solid #555555;
                border-radius: 14px;
            }
            QLabel {
                background: transparent; border: none;
                color: #e8e8e8; font-size: 12px;
            }
            QLineEdit {
                background: #252525;
                color: #f0f0f0;
                border: 1px solid #505050;
                border-radius: 6px;
                padding: 4px 8px;
                font-family: 'Cascadia Mono', Consolas, monospace;
                font-size: 12px;
            }
            QLineEdit:focus { border-color: #60cdff; }
            QPushButton {
                background: #444444;
                color: #f0f0f0;
                border: 1px solid #606060;
                border-radius: 8px;
                padding: 7px 20px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover { background: #555555; border-color: #80cbff; }
            QPushButton#mcpOK {
                background: #0078d4;
                border-color: #0078d4;
                color: white;
            }
            QPushButton#mcpOK:hover { background: #005a9e; }
        """)
        shadow = QGraphicsDropShadowEffect(outer)
        shadow.setBlurRadius(60); shadow.setOffset(0, 16)
        shadow.setColor(QColor(0, 0, 0, 180))
        outer.setGraphicsEffect(shadow)

        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(30, 30, 30, 30)
        root_lay.addWidget(outer)

        main = QVBoxLayout(outer)
        main.setContentsMargins(18, 16, 18, 16)
        main.setSpacing(14)

        # ── Title bar ───────────────────────────────────────────────
        title_row = QHBoxLayout(); title_row.setSpacing(8)
        ic = QLabel("🎨")
        ic.setStyleSheet("font-size: 16px; background: transparent; border: none;")
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "font-size: 15px; font-weight: 700; color: #ffffff; "
            "background: transparent; border: none;"
        )
        title_row.addWidget(ic)
        title_row.addWidget(title_lbl, 1)
        main.addLayout(title_row)

        # ── Thin separator ──────────────────────────────────────────
        sep = QFrame(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: #505050; border: none;")
        main.addWidget(sep)

        # ── Color picker body ────────────────────────────────────────
        body = QHBoxLayout(); body.setSpacing(14)

        # HSV plane + hue bar (left side)
        left_col = QVBoxLayout(); left_col.setSpacing(10)
        picker_row = QHBoxLayout(); picker_row.setSpacing(8)
        self.plane = HSVPlane()
        self.bar   = HueBar()
        picker_row.addWidget(self.plane)
        picker_row.addWidget(self.bar)
        left_col.addLayout(picker_row)
        body.addLayout(left_col)

        # Right panel
        right = QVBoxLayout(); right.setSpacing(10)
        right.setContentsMargins(0, 0, 0, 0)

        # ── Color preview (new / current) ───────────────────────────
        preview_frame = QFrame()
        preview_frame.setFixedHeight(62)
        preview_frame.setStyleSheet(
            "QFrame { border-radius: 10px; border: 1px solid #555; }"
        )
        pv_lay = QVBoxLayout(preview_frame)
        pv_lay.setContentsMargins(0, 0, 0, 0); pv_lay.setSpacing(0)
        self._new_sw  = QFrame(); self._new_sw.setFixedHeight(31)
        self._old_sw  = QFrame(); self._old_sw.setFixedHeight(31)
        self._new_sw.setStyleSheet(
            "border-radius: 9px 9px 0 0; border-bottom: 1px solid #555;"
        )
        self._old_sw.setStyleSheet("border-radius: 0 0 9px 9px;")
        pv_lay.addWidget(self._new_sw)
        pv_lay.addWidget(self._old_sw)

        # Labels row below preview
        lbl_row = QHBoxLayout()
        nl = QLabel("New"); nl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        nl.setStyleSheet("color: #909090; font-size: 10px; background: transparent; border:none;")
        cl = QLabel("Current"); cl.setAlignment(Qt.AlignmentFlag.AlignRight)
        cl.setStyleSheet("color: #909090; font-size: 10px; background: transparent; border:none;")
        lbl_row.addWidget(nl); lbl_row.addWidget(cl)

        right.addWidget(preview_frame)
        right.addLayout(lbl_row)

        # ── Hex input ────────────────────────────────────────────────
        hex_frame = QFrame()
        hex_frame.setStyleSheet(
            "QFrame { background: #252525; border: 1px solid #505050; border-radius: 8px; }"
            "QLabel { color: #909090; font-size: 12px; }"
        )
        hex_h = QHBoxLayout(hex_frame)
        hex_h.setContentsMargins(10, 6, 10, 6); hex_h.setSpacing(4)
        hash_lbl = QLabel("#")
        hash_lbl.setStyleSheet("color: #909090; font-size: 13px; font-weight: 600; background: transparent; border: none;")
        hash_lbl.setFixedWidth(14)
        self._hex_in = QLineEdit(start_col.name().upper().lstrip("#"))
        self._hex_in.setStyleSheet(
            "background: transparent; border: none; color: #f0f0f0; "
            "font-family: 'Cascadia Mono', Consolas, monospace; font-size: 14px; font-weight: 600;"
        )
        hex_h.addWidget(hash_lbl)
        hex_h.addWidget(self._hex_in, 1)
        right.addWidget(hex_frame)

        # ── HSV + RGB inputs in a 2-column grid ─────────────────────
        inputs_frame = QFrame()
        inputs_frame.setStyleSheet(
            "QFrame { background: #2a2a2a; border: 1px solid #484848; border-radius: 8px; }"
        )
        grid_lay = QHBoxLayout(inputs_frame)
        grid_lay.setContentsMargins(10, 8, 10, 8); grid_lay.setSpacing(14)

        def input_col(labels_vals):
            col = QVBoxLayout(); col.setSpacing(5)
            widgets = []
            for lbl_txt, ed in labels_vals:
                row = QHBoxLayout(); row.setSpacing(5)
                L = QLabel(lbl_txt)
                L.setFixedWidth(18)
                L.setStyleSheet(
                    "color: #888888; font-size: 11px; font-weight: 600; "
                    "background: transparent; border: none;"
                )
                ed.setFixedWidth(52)
                ed.setFixedHeight(26)
                row.addWidget(L); row.addWidget(ed)
                col.addLayout(row)
                widgets.append(ed)
            return col, widgets

        self._h_in = QLineEdit(); self._s_in = QLineEdit(); self._v_in = QLineEdit()
        self._r_in = QLineEdit(); self._g_in = QLineEdit(); self._b_in = QLineEdit()

        hsv_col, _ = input_col([("H", self._h_in), ("S", self._s_in), ("V", self._v_in)])
        rgb_col, _ = input_col([("R", self._r_in), ("G", self._g_in), ("B", self._b_in)])

        div = QFrame(); div.setFixedWidth(1)
        div.setStyleSheet("background: #484848; border: none;")

        grid_lay.addLayout(hsv_col)
        grid_lay.addWidget(div)
        grid_lay.addLayout(rgb_col)

        right.addWidget(inputs_frame)

        # ── Preset swatches ──────────────────────────────────────────
        sw_title = QLabel("Presets")
        sw_title.setStyleSheet(
            "color: #888888; font-size: 10px; font-weight: 600; letter-spacing: 0.8px; "
            "background: transparent; border: none;"
        )
        right.addWidget(sw_title)

        sw_grid = QWidget()
        sw_grid_lay = QHBoxLayout(sw_grid)
        sw_grid_lay.setContentsMargins(0, 0, 0, 0); sw_grid_lay.setSpacing(4)
        for hex_c in self.PRESETS[:8]:   # first row
            sw = QFrame(); sw.setFixedSize(22, 22)
            sw.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            sw.setStyleSheet(
                f"QFrame {{ background: {hex_c}; border-radius: 5px; border: 1px solid rgba(255,255,255,20); }}"
                f"QFrame:hover {{ border: 2px solid #ffffff; }}"
            )
            def make_click(hc):
                def on_click(e=None):
                    c = QColor(hc)
                    if c.isValid():
                        self.plane.setColor(c)
                        self.bar.setHue(c.hue() if c.hue() >= 0 else 0)
                        self._sync(c)
                sw.mousePressEvent = lambda e, h=hc: on_click()
                return on_click
            make_click(hex_c)
            sw_grid_lay.addWidget(sw)
        sw_grid_lay.addStretch(1)
        right.addWidget(sw_grid)

        sw_grid2 = QWidget()
        sw_grid2_lay = QHBoxLayout(sw_grid2)
        sw_grid2_lay.setContentsMargins(0, 0, 0, 0); sw_grid2_lay.setSpacing(4)
        for hex_c in self.PRESETS[8:]:   # second row
            sw = QFrame(); sw.setFixedSize(22, 22)
            sw.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            sw.setStyleSheet(
                f"QFrame {{ background: {hex_c}; border-radius: 5px; border: 1px solid rgba(255,255,255,20); }}"
                f"QFrame:hover {{ border: 2px solid #ffffff; }}"
            )
            def make_click2(hc):
                def on_click(e=None):
                    c = QColor(hc)
                    if c.isValid():
                        self.plane.setColor(c)
                        self.bar.setHue(c.hue() if c.hue() >= 0 else 0)
                        self._sync(c)
                sw.mousePressEvent = lambda e, h=hc: on_click()
                return on_click
            make_click2(hex_c)
            sw_grid2_lay.addWidget(sw)
        sw_grid2_lay.addStretch(1)
        right.addWidget(sw_grid2)

        right.addStretch(1)

        # ── OK / Cancel buttons ──────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.setFixedSize(100, 36)
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply"); ok.setObjectName("mcpOK"); ok.setFixedSize(100, 36)
        ok.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ok.clicked.connect(self._on_ok)
        btn_row.addWidget(cancel); btn_row.addWidget(ok)
        right.addLayout(btn_row)

        body.addLayout(right)
        main.addLayout(body)

        # ── Wire signals ─────────────────────────────────────────────
        self.plane.colorChanged.connect(lambda: self._sync(self.plane.color()))
        self.bar.hueChanged.connect(lambda h: (self.plane.setHue(h), self._sync(self.plane.color())))
        self._hex_in.editingFinished.connect(self._on_hex)
        for ed in (self._h_in, self._s_in, self._v_in):
            ed.editingFinished.connect(self._on_hsv)
        for ed in (self._r_in, self._g_in, self._b_in):
            ed.editingFinished.connect(self._on_rgb)

        self.plane.setColor(start_col)
        self.bar.setHue(start_col.hue() if start_col.hue() >= 0 else 0)
        self._sync(start_col)

        # Fade-in on open
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(180); self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.setStartValue(0.0); self._fade.setEndValue(1.0)

    def showEvent(self, e):
        super().showEvent(e)
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

    def _sync(self, col: QColor, skip_inputs=False):
        hex_ = col.name().upper()
        self._new_sw.setStyleSheet(
            f"background: {hex_}; border-radius: 9px 9px 0 0; "
            f"border: 1px solid rgba(255,255,255,15); border-bottom: 1px solid #444;"
        )
        self._old_sw.setStyleSheet(
            f"background: {self._initial_hex}; border-radius: 0 0 9px 9px; "
            f"border: 1px solid rgba(255,255,255,15); border-top: none;"
        )
        if not skip_inputs:
            h, s, v, _ = col.getHsv()
            for ed, val in ((self._hex_in, hex_),
                            (self._h_in, str(max(0, h))),
                            (self._s_in, str(round(s / 255 * 100))),
                            (self._v_in, str(round(v / 255 * 100))),
                            (self._r_in, str(col.red())),
                            (self._g_in, str(col.green())),
                            (self._b_in, str(col.blue()))):
                ed.blockSignals(True); ed.setText(val.lstrip("#")); ed.blockSignals(False)

    def _on_hex(self):
        raw = self._hex_in.text().strip()
        c = QColor(raw if raw.startswith("#") else f"#{raw}")
        if c.isValid():
            self.plane.setColor(c)
            self.bar.setHue(c.hue() if c.hue() >= 0 else 0)
            self._sync(c)

    def _on_hsv(self):
        try:
            h = max(0, min(359, int(self._h_in.text().strip())))
            s = max(0, min(100, int(self._s_in.text().strip())))
            v = max(0, min(100, int(self._v_in.text().strip())))
        except ValueError:
            return
        c = QColor.fromHsv(h, round(s / 100 * 255), round(v / 100 * 255))
        self.plane.setColor(c)
        self.bar.setHue(h)
        self._sync(c)

    def _on_rgb(self):
        try:
            r = max(0, min(255, int(self._r_in.text().strip())))
            g = max(0, min(255, int(self._g_in.text().strip())))
            b = max(0, min(255, int(self._b_in.text().strip())))
        except ValueError:
            return
        c = QColor(r, g, b)
        self.plane.setColor(c)
        self.bar.setHue(c.hue() if c.hue() >= 0 else 0)
        self._sync(c)

    def _on_ok(self):
        self._result = self.plane.color()
        self.accept()

    @staticmethod
    def pick(parent, initial_hex, title="Pick a Color"):
        dlg = ModernColorPicker(initial_hex, parent, title)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg._result is not None:
            return dlg._result
        return None
