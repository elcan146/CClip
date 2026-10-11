#!/usr/bin/env python3
"""Main window for CClip application."""

import os
import sys
import json
import copy
import time
import queue
import webbrowser
from datetime import datetime, timedelta

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QFrame, QLabel, QPushButton, QSlider, QLineEdit, QPlainTextEdit,
    QFileDialog, QMessageBox, QScrollArea, QSizePolicy, QDialog,
    QStyleFactory, QGraphicsOpacityEffect, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import (
    Qt, QUrl, pyqtSignal, QPoint, QRect, QSize,
    QPropertyAnimation, QEasingCurve, QAbstractAnimation, QTimer
)
from PyQt6.QtGui import (
    QFont, QIcon, QPixmap, QCursor, QColor, QPalette
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

# Import settings and apply theme FIRST before importing color constants
from utils.settings import S, DEFAULT_SETTINGS, DEFAULT_PROMPT, save_settings, update_s
from utils.theme import apply_theme

# Apply theme immediately so colors are correct before any imports grab them
apply_theme(S)

# Now import color constants and everything else
from config import (
    CANCEL, LOG_Q, YT_LOG_Q, STATS_Q,
    APP_INSTANCE, UI_FONTS, SUB_FONTS, GPU_LIB,
    BG, SURFACE, CARD, CARD2, CARD3, SIDEBAR, ACC, ACC2, ACC_DK,
    GREEN, RED, YEL, BLUE, CYAN, TEXT, SUB, SUB_DK, BOR, BOR_SFT,
    build_qss, log, yt_log
)
from ui.widgets.base import CustomQPushButton
from ui.widgets.titlebar import CustomTitleBar
from ui.widgets.card import Card, AnimatedProgressBar, CountUpLabel
from ui.widgets.buttons import NavButton, ToggleSwitch
from ui.widgets.dialogs import ToastPopup, ModernDialog
from ui.widgets.inputs import ModernComboBox, FontOptionRow
from ui.widgets.color_picker import ModernColorPicker
from ui.widgets.pickers import AnimatedComboPicker, FontPicker
from ui.widgets.calendar import ModernCalendar
from threads.stats_thread import StatsThread
from threads.batch_thread import BatchThread
from threads.upload_thread import UploadThread
from threads.auth_thread import AuthThread
from threads.scan_thread import ScanThread
from threads.analytics_thread import AnalyticsFetchThread


class ClipMasterApp(QMainWindow):
    NAV_ITEMS = [
        ("config",    "\u2699",  "Configuration"),   # gear
        ("prompt",    "\u2726",  "Prompt Editor"),        # 4-pointed star
        ("audio",     "\u266A",  "Audio Mixer"),     # eighth note
        ("video",     "\u25A6",  "Video & Subs"),    # square w/ horizontal lines
        ("engine",    "\u25B6",  "Create"),     # play triangle
        ("youtube",   "\u25B7",  "YouTube Panel"),   # outlined play
        ("analytics", "\u25C9",  "Analytics"),       # fisheye
        ("uiconfig",  "\u2630",  "UI Settings"),     # trigram lines
    ]

    def __init__(self, skip_music_setup=False):
        super().__init__()
        global APP_INSTANCE
        APP_INSTANCE = self
        # Also update the utils.settings module's APP_INSTANCE for update_s function
        import utils.settings as _settings
        _settings.APP_INSTANCE = self

        self.setWindowTitle("CClip v1.0")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setMouseTracking(True)
        self.resize(1480, 900)
        self.setMinimumSize(1240, 760)

        self._running = False
        self._batch_thread = None
        self._upload_thread = None
        self._scan_thread = None
        self._auth_thread = None

        self.hist_cpu = [0]*60
        self.hist_gpu = [0]*60
        self.hist_ram = [0]*60
        self.hist_dsk = [0]*60

        self._current_page = None
        self._page_snapshot = {}
        self._dirty_keys = set()
        self._suppress_dirty = False

        # Setup logging
        self._setup_logging()

        self._builders = {
            "config":    self._build_config_page,
            "prompt":    self._build_prompt_page,
            "audio":     self._build_audio_page,
            "video":     self._build_video_subs_page,
            "engine":    self._build_engine_page,
            "analytics": self._build_analytics_page,
            "youtube":   self._build_youtube_page,
            "uiconfig":  self._build_uiconfig_page,
        }

        self._setup_ui(initial_page="config")

        self._stats_thread = StatsThread(); self._stats_thread.start()

        self._log_timer = QTimer(self); self._log_timer.timeout.connect(self._poll_log); self._log_timer.start(60)
        self._yt_timer  = QTimer(self); self._yt_timer.timeout.connect(self._poll_yt_log); self._yt_timer.start(100)
        self._stats_timer = QTimer(self); self._stats_timer.timeout.connect(self._poll_stats); self._stats_timer.start(120)
        self._clock_timer = QTimer(self); self._clock_timer.timeout.connect(self._tick_clock); self._clock_timer.start(1000)
        self._tick_clock()

        if not skip_music_setup:
            self._setup_music()

    def _setup_music(self):
        """Initialize menu music player."""
        self._music_muted = False
        self._music_player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._music_player.setAudioOutput(self._audio_output)
        self._audio_output.setVolume(0.5)
        music_path = os.path.abspath("MenuMusic.mp3")
        if os.path.exists(music_path):
            import contextlib
            with contextlib.redirect_stderr(open(os.devnull, 'w')):
                self._music_player.setSource(QUrl.fromLocalFile(music_path))
            self._music_player.mediaStatusChanged.connect(self._on_music_status_changed)
            self._music_player.play()

    def _setup_logging(self):
        """Setup session logging to Logs folder."""
        self._logs_folder = os.path.abspath("Logs")
        os.makedirs(self._logs_folder, exist_ok=True)
        
        # Create log file with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_file = os.path.join(self._logs_folder, f"cclip_{timestamp}.log")
        
        # Write session header
        with open(self._log_file, "w", encoding="utf-8") as f:
            f.write(f"CClip Session Log\n")
            f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*60}\n\n")

    def _write_log(self, msg):
        """Write a message to the log file."""
        if hasattr(self, '_log_file') and self._log_file:
            try:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    ts = datetime.now().strftime("%H:%M:%S")
                    f.write(f"[{ts}] {msg}\n")
            except Exception:
                pass  # Silently fail if logging fails

    def _setup_ui(self, initial_page="config"):
        """Build the whole chrome (title bar, sidebar, center stack, right sidebar, toast). Re-callable for live theme/scale changes."""
        outer = QWidget(); outer.setObjectName("root")
        self.setCentralWidget(outer)
        outer_v = QVBoxLayout(outer)
        outer_v.setContentsMargins(0, 0, 0, 0)
        outer_v.setSpacing(0)

        self.title_bar = CustomTitleBar(self, outer)
        outer_v.addWidget(self.title_bar)

        self.root = QWidget(); self.root.setObjectName("root")
        outer_v.addWidget(self.root, 1)

        root_layout = QHBoxLayout(self.root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._build_sidebar()
        root_layout.addWidget(self.sidebar)
        sep = QFrame(); sep.setObjectName("vhairline"); root_layout.addWidget(sep)

        self.center = QWidget(); self.center.setObjectName("pageBg")
        center_v = QVBoxLayout(self.center)
        center_v.setContentsMargins(0, 0, 0, 0); center_v.setSpacing(0)
        self.stack = QStackedWidget()
        center_v.addWidget(self.stack, 1)
        root_layout.addWidget(self.center, 1)

        sep2 = QFrame(); sep2.setObjectName("vhairline"); root_layout.addWidget(sep2)
        self._build_right_sidebar()
        root_layout.addWidget(self.right_bar)

        self._pages = {}
        for key, fn in self._builders.items():
            fn()

        self._toast = ToastPopup(self.root, self._on_toast_save, self._on_toast_discard)

        self._indicator = QFrame(self.nav_container)
        import utils.theme as _t
        self._indicator.setStyleSheet(f"background: {_t.ACC}; border-radius: 2px;")
        self._indicator.setFixedSize(3, 22)
        self._indicator.show()
        self._indicator.raise_()
        self._ind_anim = QPropertyAnimation(self._indicator, b"pos", self)
        self._ind_anim.setDuration(260)
        self._ind_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        QTimer.singleShot(0, lambda k=initial_page: self.show_page(k, animate=False))

    # ---------------- EDGE RESIZE (frameless window) ----------------
    _EDGE = 6

    def _edge_at(self, pos):
        r = self.rect()
        x, y, w, h, m = pos.x(), pos.y(), r.width(), r.height(), self._EDGE
        left   = x < m
        right  = x > w - m
        top    = y < m
        bottom = y > h - m
        if top and left:      return Qt.CursorShape.SizeFDiagCursor,  "tl"
        if top and right:     return Qt.CursorShape.SizeBDiagCursor,  "tr"
        if bottom and left:   return Qt.CursorShape.SizeBDiagCursor,  "bl"
        if bottom and right:  return Qt.CursorShape.SizeFDiagCursor,  "br"
        if top:               return Qt.CursorShape.SizeVerCursor,    "t"
        if bottom:            return Qt.CursorShape.SizeVerCursor,    "b"
        if left:              return Qt.CursorShape.SizeHorCursor,    "l"
        if right:             return Qt.CursorShape.SizeHorCursor,    "r"
        return None, None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            cursor, edge = self._edge_at(event.position().toPoint())
            if edge:
                self._resize_edge = edge
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geo = self.geometry()
                event.accept()
                return
        self._resize_edge = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if hasattr(self, '_resize_edge') and self._resize_edge and (event.buttons() & Qt.MouseButton.LeftButton):
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            geo = QRect(self._resize_start_geo)
            edge = self._resize_edge
            dx, dy = delta.x(), delta.y()
            if 'r' in edge: geo.setRight(geo.right() + dx)
            if 'b' in edge: geo.setBottom(geo.bottom() + dy)
            if 'l' in edge: geo.setLeft(geo.left() + dx)
            if 't' in edge: geo.setTop(geo.top() + dy)
            min_w, min_h = self.minimumWidth(), self.minimumHeight()
            if geo.width() >= min_w and geo.height() >= min_h:
                self.setGeometry(geo)
            event.accept()
            return
        cursor, _ = self._edge_at(event.position().toPoint())
        self.setCursor(cursor or Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_edge = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    # ---------------- LIVE THEME / SCALE ----------------
    def apply_live_theme(self):
        """Rebuild the entire UI live with the new theme/scale/font."""
        apply_theme(S)
        scale = float(S.get("ui_scale", 1.0))
        fam = S.get("ui_font", "Segoe UI Variable")
        app = QApplication.instance()
        if app:
            app.setFont(QFont(fam, max(7, int(round(9 * scale)))))
            app.setStyleSheet(build_qss(S, scale))
        cur = self._current_page or "config"
        self._current_page = None
        self._suppress_dirty = True
        try:
            self._setup_ui(initial_page=cur)
        finally:
            self._suppress_dirty = False
        if hasattr(self, "title_bar"):
            self.title_bar.refresh_style()

    # ---------------- DIRTY TRACKING ----------------
    def _mark_dirty(self, key):
        if getattr(self, "_suppress_dirty", False):
            return
        self._dirty_keys.add(key)
        print(f"_mark_dirty: key={key}, dirty_keys={self._dirty_keys}")

    def _rebuild_page(self, key):
        """Destroy and rebuild a single page (used by discard)."""
        w = self._pages.get(key)
        if not w:
            return
        idx = self.stack.indexOf(w)
        self.stack.removeWidget(w)
        w.deleteLater()
        del self._pages[key]
        builder = self._builders.get(key)
        if builder:
            self._suppress_dirty = True
            try:
                builder()
            finally:
                self._suppress_dirty = False
        new_w = self._pages.get(key)
        if new_w:
            self.stack.setCurrentWidget(new_w)

    # ---------------- SIDEBAR ----------------
    def _build_sidebar(self):
        import utils.theme as _t
        self.sidebar = QWidget(); self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(248)
        v = QVBoxLayout(self.sidebar)
        v.setContentsMargins(0, 18, 0, 14); v.setSpacing(0)

        _base = os.path.dirname(os.path.abspath(__file__))
        _logo_file = "darklogo.png" if S.get("ui_theme", "Dark") == "Light" else "lightlogo.png"
        _lp = os.path.join(_base, _logo_file)
        _lx = QPixmap(_lp) if os.path.exists(_lp) else None
        brand = QLabel()
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setContentsMargins(0, 0, 0, 8)
        if _lx and not _lx.isNull():
            brand.setPixmap(_lx.scaledToWidth(200, Qt.TransformationMode.SmoothTransformation))
        else:
            brand.setText("CClip"); brand.setObjectName("brandTitle")
        v.addWidget(brand)

        v.addSpacing(12)

        self.nav_container = QWidget()
        self.nav_layout = QVBoxLayout(self.nav_container)
        self.nav_layout.setContentsMargins(10, 0, 10, 0); self.nav_layout.setSpacing(3)
        v.addWidget(self.nav_container)

        self._nav_btns = {}
        for key, icon, label in self.NAV_ITEMS:
            b = NavButton(key, icon, label)
            b.clicked.connect(self.show_page)
            self.nav_layout.addWidget(b)
            self._nav_btns[key] = b

        v.addSpacing(14)

        pills = QWidget()
        pv = QVBoxLayout(pills); pv.setContentsMargins(18, 0, 18, 0); pv.setSpacing(6)
        self._sb_movies_lbl = self._make_pill(pv, "Movies", str(S['total_movies']), _t.ACC)
        self._sb_shorts_lbl = self._make_pill(pv, "Shorts", str(S['total_shorts']), _t.GREEN)
        v.addWidget(pills)

        v.addStretch(1)

        reset_btn = QPushButton("Reset Defaults")
        reset_btn.setObjectName("softAction")
        reset_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        reset_btn.setFixedHeight(34)
        reset_btn.clicked.connect(self._reset_all_defaults)

        # Music toggle button
        self._music_btn = QPushButton("\u266B")  # Musical note symbol
        self._music_btn.setObjectName("softAction")
        self._music_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._music_btn.setFixedHeight(34)
        self._music_btn.setFixedWidth(40)
        self._music_btn.setToolTip("Toggle menu music")
        self._music_btn.clicked.connect(self._toggle_music)

        reset_wrap = QWidget()
        rw = QHBoxLayout(reset_wrap); rw.setContentsMargins(18, 0, 18, 10)
        rw.addWidget(reset_btn)
        rw.addWidget(self._music_btn)
        v.addWidget(reset_wrap)

        cr = QLabel("\u00A9 2026  CClip v1.1")
        cr.setObjectName("tiny")
        cr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(cr)

    def _make_pill(self, parent_layout, label, value, color):
        f = QFrame(); f.setObjectName("cardSlim")
        h = QHBoxLayout(f); h.setContentsMargins(12, 8, 12, 8)
        l = QLabel(label); l.setObjectName("subSm")
        vv = QLabel(value); vv.setStyleSheet(f"color: {color}; font-weight: 600;")
        vv.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(l); h.addStretch(1); h.addWidget(vv)
        parent_layout.addWidget(f)
        return vv

    def _reset_all_defaults(self):
        import utils.theme as _t
        resp = ModernDialog.ask(
            self,
            "Reset defaults",
            "Restore every setting to the defaults stored in this code file?",
            buttons=[
                ("Cancel", "cancel", False),
                ("Reset",  "reset",  True),
            ],
            icon_char="\u26A0",
            icon_color=_t.YEL,
        )
        if resp != "reset":
            return
        S.clear()
        S.update(copy.deepcopy(DEFAULT_SETTINGS))
        for folder_key in ("in_folder", "out_folder", "mus_folder"):
            os.makedirs(S[folder_key], exist_ok=True)
        save_settings()
        self._dirty_keys.clear()
        self._page_snapshot = copy.deepcopy(S)
        if hasattr(self, "_toast") and self._toast._shown:
            self._toast.slide_out()
        self.apply_live_theme()

    # ---------------- RIGHT TELEMETRY ----------------
    def _build_right_sidebar(self):
        self.right_bar = QWidget(); self.right_bar.setObjectName("rightbar")
        self.right_bar.setFixedWidth(266)
        v = QVBoxLayout(self.right_bar)
        v.setContentsMargins(18, 18, 12, 12); v.setSpacing(6)

        h1 = QLabel("System "); h1.setObjectName("h2"); v.addWidget(h1)
        sub = QLabel("Live \u00B7 refreshes every 1.5s"); sub.setObjectName("subSm"); v.addWidget(sub)
        v.addSpacing(6)
        line = QFrame(); line.setObjectName("hairline"); v.addWidget(line)

        import utils.theme as _t
        bg_rgb = _t.SIDEBAR
        self.sys_fig = Figure(figsize=(2.5, 8.5), dpi=92, facecolor=bg_rgb)
        self.sys_fig.subplots_adjust(left=0.12, right=0.99, top=0.96, bottom=0.04, hspace=0.55)

        def setup_ax(ax, title, color):
            ax.set_facecolor(bg_rgb)
            ax.set_ylim(0, 100); ax.set_xlim(0, 59)
            ax.set_xticks([]); ax.set_yticks([0, 50, 100])
            ax.set_yticklabels(['0', '50', '100'], color=_t.SUB_DK, fontsize=7)
            ax.set_title(title, color=_t.TEXT, fontsize=9, pad=6, loc="left")
            ax.grid(True, color=_t.SUB_DK, linestyle=":", linewidth=0.6, alpha=0.85)
            for sp in ax.spines.values():
                sp.set_edgecolor(_t.BOR); sp.set_linewidth(0.6)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ln, = ax.plot([], [], color=color, lw=1.6)
            val = ax.text(0.98, 0.84, "0%", transform=ax.transAxes, ha="right", va="center",
                          color=color, fontsize=9, fontweight="bold")
            return ln, ax, val

        self.line_cpu, self.ax_cpu, self.val_cpu = setup_ax(self.sys_fig.add_subplot(411), "CPU", _t.ACC)
        self.line_gpu, self.ax_gpu, self.val_gpu = setup_ax(self.sys_fig.add_subplot(412), "GPU", _t.GREEN)
        self.line_ram, self.ax_ram, self.val_ram = setup_ax(self.sys_fig.add_subplot(413), "RAM", _t.YEL)
        self.line_dsk, self.ax_dsk, self.val_dsk = setup_ax(self.sys_fig.add_subplot(414), "DISK", _t.RED)

        self.sys_canvas = FigureCanvas(self.sys_fig)
        self.sys_canvas.setStyleSheet("background: transparent;")
        v.addWidget(self.sys_canvas, 1)

        self._clock_btn = QPushButton("")
        self._clock_btn.setFlat(True)
        self._clock_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._clock_btn.setStyleSheet(
            f"QPushButton {{ background: {_t.CARD2}; color: {_t.TEXT}; border: 1px solid {_t.BOR_SFT}; "
            f"border-radius: 8px; padding: 8px 10px; font-weight: 600; }}"
            f"QPushButton:hover {{ border-color: {_t.ACC}; background: {_t.CARD3}; }}"
        )
        self._clock_btn.clicked.connect(self._open_calendar_popup)
        v.addWidget(self._clock_btn)

    def _tick_clock(self):
        now = datetime.now()
        line1 = now.strftime("%d/%m/%Y")
        line2 = now.strftime("%A  %H:%M:%S")
        if hasattr(self, "_clock_btn") and self._clock_btn is not None:
            self._clock_btn.setText(f"{line1}\n{line2}")

    def _open_calendar_popup(self):
        print("Opening calendar popup")
        dlg = QDialog(self)
        dlg.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QFrame(dlg); outer.setObjectName("calOuter")
        import utils.theme as _t
        outer.setStyleSheet(
            f"QFrame#calOuter {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 14px; }}"
        )

        root = QVBoxLayout(dlg); root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(outer)

        v = QVBoxLayout(outer); v.setContentsMargins(18, 16, 18, 14); v.setSpacing(10)

        today_lbl = QLabel(datetime.now().strftime("%A, %d %B %Y"))
        today_lbl.setStyleSheet(
            f"color: {_t.TEXT}; font-size: 14px; font-weight: 600; "
            f"background: transparent; border: none;"
        )
        v.addWidget(today_lbl)

        time_lbl = QLabel(datetime.now().strftime("%H:%M:%S"))
        time_lbl.setStyleSheet(
            f"color: {_t.SUB}; font-size: 12px; background: transparent; border: none; "
            f"font-weight: 500;"
        )
        v.addWidget(time_lbl)

        import utils.theme as _t
        sep = QFrame(); sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {_t.BOR_SFT}; border: none;")
        v.addWidget(sep)

        cal = ModernCalendar()
        v.addWidget(cal)

        foot = QHBoxLayout(); foot.setContentsMargins(0, 4, 0, 0); foot.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(30); close_btn.setFixedWidth(90)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(dlg.close)
        foot.addWidget(close_btn)
        v.addLayout(foot)

        dlg.setWindowOpacity(0.0)
        fade = QPropertyAnimation(dlg, b"windowOpacity", dlg)
        fade.setDuration(160); fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(0.0); fade.setEndValue(1.0)

        # Position above the clock button
        dlg.adjustSize()
        if hasattr(self, "_clock_btn") and self._clock_btn is not None:
            gp = self._clock_btn.mapToGlobal(QPoint(0, 0))
            x = gp.x() + self._clock_btn.width() // 2 - dlg.sizeHint().width() // 2
            y = gp.y() - dlg.sizeHint().height() - 8
            # Clamp to available screen geometry so calendar is always visible
            screen = QApplication.primaryScreen().availableGeometry()
            x = max(screen.x() + 8, min(x, screen.x() + screen.width()  - dlg.sizeHint().width()  - 8))
            y = max(screen.y() + 8, min(y, screen.y() + screen.height() - dlg.sizeHint().height() - 8))
            # If above-button position causes clipping, try below instead
            if y < screen.y() + 8:
                y_below = gp.y() + self._clock_btn.height() + 8
                if y_below + dlg.sizeHint().height() <= screen.y() + screen.height() - 8:
                    y = y_below
                else:
                    y = screen.y() + 8
            dlg.move(x, y)
        dlg.show()
        fade.start()
        dlg.exec()

    # ---------------- PAGE TRANSITIONS ----------------
    def _add_page(self, key, widget):
        self._pages[key] = widget
        self.stack.addWidget(widget)

    def show_page(self, key, animate=True):
        import utils.theme as _t
        if key not in self._pages:
            return
        widget = self._pages[key]
        if self.stack.currentWidget() is widget:
            self._update_nav_visuals(key)
            self._move_indicator(key)
            return

        # Prompt if leaving a dirty tab
        if self._current_page and self._current_page != key and self._dirty_keys:
            tab_name = dict((k, lbl) for k, _icn, lbl in self.NAV_ITEMS).get(
                self._current_page, self._current_page
            )
            resp = ModernDialog.ask(
                self,
                "Unsaved changes",
                f"You have unsaved changes on the \u201C{tab_name}\u201D tab.\n"
                f"Do you want to save them, discard them, or stay on this tab?",
                buttons=[
                    ("Cancel",  "cancel",  False),
                    ("Discard", "discard", False),
                    ("Save",    "save",    True),
                ],
                icon_char="\u26A0",
                icon_color=_t.YEL,
            )
            if resp in (None, "cancel"):
                return
            if resp == "save":
                save_settings()
                self._dirty_keys.clear()
                if self._toast._shown:
                    self._toast.slide_out()
            elif resp == "discard":
                self._revert_dirty()
                prev = self._current_page
                if self._toast._shown:
                    self._toast.slide_out()
                self._rebuild_page(prev)
                widget = self._pages[key]

        # snapshot the incoming page so discard can revert
        self._page_snapshot = copy.deepcopy(S)
        self._current_page = key

        self.stack.setCurrentWidget(widget)
        self._update_nav_visuals(key)
        self._move_indicator(key)

        if animate:
            eff = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(eff)
            eff.setOpacity(0.0)
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(240)
            anim.setStartValue(0.0); anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda: widget.setGraphicsEffect(None))
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

        if key == "analytics":
            QTimer.singleShot(80, self._update_charts)

    def _update_nav_visuals(self, key):
        for k, b in self._nav_btns.items():
            b.setChecked(k == key)

    def _move_indicator(self, key):
        btn = self._nav_btns.get(key)
        if not btn:
            return
        self.nav_container.updateGeometry()
        y = btn.y() + (btn.height() - self._indicator.height()) // 2
        target = QPoint(4, y)
        if not self._indicator.isVisible() or self._indicator.pos() == QPoint(0, 0):
            self._indicator.move(target); self._indicator.show(); self._indicator.raise_()
            return
        if self._ind_anim.state() == QAbstractAnimation.State.Running:
            self._ind_anim.stop()
        self._ind_anim.setStartValue(self._indicator.pos())
        self._ind_anim.setEndValue(target)
        self._ind_anim.start()

    # ---------------- PAGE HELPERS ----------------
    def _scroll_page(self):
        import utils.theme as _t
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("pageBg")
        scroll.setStyleSheet(f"QScrollArea {{ background: {_t.BG}; border: none; }}")
        scroll.viewport().setStyleSheet(f"background: {_t.BG};")
        inner = QWidget(); inner.setObjectName("pageBg")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(24, 18, 24, 18); lay.setSpacing(10)
        scroll.setWidget(inner)
        return scroll, lay

    def _plain_page(self):
        page = QWidget(); page.setObjectName("pageBg")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 18, 24, 18); lay.setSpacing(10)
        return page, lay

    def _page_header(self, lay, title, subtitle=""):
        import utils.theme as _t
        h = QLabel(title); h.setObjectName("h1"); lay.addWidget(h)
        if subtitle:
            s = QLabel(subtitle); s.setObjectName("sub"); lay.addWidget(s)
        accent = QFrame()
        accent.setStyleSheet(f"background: {_t.ACC}; border: none; border-radius: 1px;")
        accent.setFixedHeight(2); accent.setFixedWidth(40)
        lay.addWidget(accent)
        anim = QPropertyAnimation(accent, b"minimumWidth", self)
        anim.setDuration(500); anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(40); anim.setEndValue(160)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        anim2 = QPropertyAnimation(accent, b"maximumWidth", self)
        anim2.setDuration(500); anim2.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim2.setStartValue(40); anim2.setEndValue(160)
        anim2.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        lay.addSpacing(4)

    # ---------------- ROW BUILDERS ----------------
    def _slider_row(self, parent_layout, label, key, min_val, max_val, default, is_int=False, callback=None):
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setMinimumWidth(210); lbl.setMaximumWidth(250)
        h.addWidget(lbl)

        slider = QSlider(Qt.Orientation.Horizontal)
        if is_int:
            slider.setMinimum(int(min_val)); slider.setMaximum(int(max_val))
            slider.setValue(int(S.get(key, default)))
        else:
            scale = 100
            slider.setMinimum(int(min_val * scale)); slider.setMaximum(int(max_val * scale))
            slider.setValue(int(float(S.get(key, default)) * scale))

        val_lbl = QLabel(""); val_lbl.setObjectName("pctLabel")
        val_lbl.setMinimumWidth(60)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        def fmt(v):
            return str(int(v)) if is_int else f"{v:.2f}"

        def on_change(raw):
            v = int(raw) if is_int else round(raw / 100.0, 2)
            val_lbl.setText(fmt(v))
            update_s(key, v)
            if callback:
                callback(v)

        slider.valueChanged.connect(on_change)
        cur = S.get(key, default)
        val_lbl.setText(fmt(int(cur) if is_int else float(cur)))

        h.addWidget(slider, 1); h.addWidget(val_lbl)
        parent_layout.addWidget(row)

    def _text_row(self, parent_layout, label, key, default):
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setMinimumWidth(210); lbl.setMaximumWidth(250); h.addWidget(lbl)
        edit = QLineEdit(str(S.get(key, default)))
        edit.textChanged.connect(lambda t: update_s(key, t.strip()))
        edit.setMaximumWidth(280); h.addWidget(edit); h.addStretch(1)
        parent_layout.addWidget(row)

    def _option_row(self, parent_layout, label, key, values, callback=None):
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setMinimumWidth(210); lbl.setMaximumWidth(250); h.addWidget(lbl)
        combo = ModernComboBox(); combo.addItems(values)
        combo.setMinimumWidth(100)
        cur = S.get(key, values[0])
        if cur in values:
            combo.setCurrentText(cur)
        def on_change(v):
            update_s(key, v)
            if callback:
                callback(v)
        combo.currentTextChanged.connect(on_change)
        h.addWidget(combo); h.addStretch(1)
        parent_layout.addWidget(row)

    def _color_row(self, parent_layout, label, key, default_hex, on_apply=None):
        """Plain (unframed) row: label, small swatch, hex, Pick button \u2014 opens ModernColorPicker."""
        row = QWidget(); h = QHBoxLayout(row)
        import utils.theme as _t
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setMinimumWidth(210); lbl.setMaximumWidth(250)
        h.addWidget(lbl)

        cur_hex = str(S.get(key, default_hex))
        if cur_hex.startswith("&H"):
            cur_hex = default_hex
            update_s(key, cur_hex, trigger_save_popup=False)

        sw = QFrame(); sw.setFixedSize(28, 28)
        sw.setStyleSheet(f"background: {cur_hex}; border: 1px solid {_t.BOR}; border-radius: 6px;")
        h.addWidget(sw)

        hex_lbl = QLabel(cur_hex.upper())
        hex_lbl.setMinimumWidth(86)
        hex_lbl.setStyleSheet(
            f"color: {_t.TEXT}; font-family: 'Cascadia Mono', Consolas, monospace; font-weight: 500; "
            f"background: transparent; border: none;"
        )
        h.addWidget(hex_lbl)

        btn = QPushButton("Pick"); btn.setObjectName("softAction"); btn.setFixedWidth(100); btn.setFixedHeight(30)
        def pick():
            start = str(S.get(key, default_hex))
            if start.startswith("&H"):
                start = default_hex
            col = ModernColorPicker.pick(self, start, f"Choose {label}")
            if col is not None:
                hexv = col.name().upper()
                sw.setStyleSheet(f"background: {hexv}; border: 1px solid {_t.BOR}; border-radius: 6px;")
                hex_lbl.setText(hexv)
                update_s(key, hexv)
                if on_apply:
                    on_apply(hexv)
        btn.clicked.connect(pick)
        h.addWidget(btn); h.addStretch(1)
        parent_layout.addWidget(row)

    def _font_combo_row(self, parent_layout, label, key, default, options, callback=None):
        """Uses the custom animated FontPicker widget (compact, no wasted right space)."""
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setMinimumWidth(210); lbl.setMaximumWidth(250)
        h.addWidget(lbl)
        cur = S.get(key, default)
        picker = FontPicker(options, current=cur, width=220)
        def on_change(v):
            update_s(key, v)
            if callback:
                callback(v)
        picker.fontChanged.connect(on_change)
        h.addWidget(picker); h.addStretch(1)
        parent_layout.addWidget(row)

    def _toggle_row(self, parent_layout, label, desc, key, on_color):
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 6, 0, 6); h.setSpacing(12)

        text_col = QVBoxLayout(); text_col.setSpacing(2)
        l = QLabel(label); l.setObjectName("h3"); text_col.addWidget(l)
        d = QLabel(desc); d.setObjectName("subSm"); d.setWordWrap(True); text_col.addWidget(d)
        wrap = QWidget(); wrap.setLayout(text_col)
        h.addWidget(wrap, 1)

        sw = ToggleSwitch(on_color=on_color)
        sw.setChecked(bool(S.get(key, False)))
        sw.toggled.connect(lambda v, k=key: update_s(k, bool(v)))
        h.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        parent_layout.addWidget(row)

    def _create_styled_combo(self, options, default, width=160, callback=None):
        """Create an animated premium dropdown like FontPicker (custom popup with slide/fade animation)."""
        picker = AnimatedComboPicker(options, current=default, width=width)
        if callback:
            picker.optionChanged.connect(callback)
        return picker

    def _folder_row(self, parent_layout, label, key):
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setObjectName("h3")
        lbl.setMinimumWidth(140); lbl.setMaximumWidth(160); h.addWidget(lbl)

        path_lbl = QLabel(S[key]); path_lbl.setObjectName("subSm"); path_lbl.setWordWrap(True)
        h.addWidget(path_lbl, 1)

        btn = QPushButton("Browse\u2026"); btn.setFixedWidth(120)
        def browse():
            d = QFileDialog.getExistingDirectory(self, f"Select {label}", S[key])
            if d:
                d = os.path.abspath(d)
                path_lbl.setText(d)
                update_s(key, d)
                os.makedirs(S[key], exist_ok=True)
        btn.clicked.connect(browse)
        h.addWidget(btn)
        parent_layout.addWidget(row)

    # ---------------- PAGE 1 - CONFIGURATION ----------------
    def _build_config_page(self):
        page, lay = self._scroll_page()
        self._page_header(lay, "Configuration", "Folders, API keys, and processing settings.")

        c1 = Card("Folder Selectors", "Choose where inputs, outputs, and music")
        self._folder_row(c1.body(), "Input Folder",  "in_folder")
        self._folder_row(c1.body(), "Output Folder", "out_folder")
        self._folder_row(c1.body(), "Music Folder",  "mus_folder")
        lay.addWidget(c1)

        c2 = Card("API Keys", "Secure credentials for Gemini AI.")
        kr = QWidget(); h = QHBoxLayout(kr); h.setContentsMargins(0, 6, 0, 4); h.setSpacing(8)
        l = QLabel("Gemini API Key"); l.setObjectName("h3"); l.setMinimumWidth(140); h.addWidget(l)
        self._gem_entry = QLineEdit(S["gemini_key"])
        self._gem_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self._gem_entry.setPlaceholderText("AIzaSy\u2026")
        self._gem_entry.textChanged.connect(lambda t: update_s("gemini_key", t.strip(), trigger_save_popup=True))
        h.addWidget(self._gem_entry, 1)
        eye = QPushButton("\U0001F441"); eye.setFixedWidth(44); eye.setToolTip("Show / hide API key")
        eye.setStyleSheet("font-size: 14px;")
        def toggle():
            is_hidden = self._gem_entry.echoMode() == QLineEdit.EchoMode.Password
            self._gem_entry.setEchoMode(
                QLineEdit.EchoMode.Normal if is_hidden else QLineEdit.EchoMode.Password
            )
            # open eye when showing, closed eye (no-see) when hidden
            eye.setText("\U0001F441" if is_hidden else "\U0001F576")
        eye.clicked.connect(toggle); h.addWidget(eye)
        save_btn = QPushButton("Save Key"); save_btn.setObjectName("softAction"); save_btn.setFixedWidth(110)
        save_btn.clicked.connect(self._save_api_key_explicit)
        h.addWidget(save_btn)
        c2.body().addWidget(kr)
        lay.addWidget(c2)

        c3 = Card("Processing Mode", "Test-run option for safe testing.")
        self._toggle_row(c3.body(), "Test Mode",
                         "Skips AI calls and uses mock data - no API credits consumed.",
                         "test_mode", ACC2)
        lay.addWidget(c3)

        lay.addStretch(1)
        self._add_page("config", page)

    def _save_api_key_explicit(self):
        new_key = self._gem_entry.text().strip()
        update_s("gemini_key", new_key, trigger_save_popup=False)
        save_settings()
        if self._toast._shown:
            self._toast.slide_out()
        log("API Key successfully saved.", "success")
        QMessageBox.information(self, "Saved", "Your Gemini API Key has been saved successfully.")

    # ---------------- PAGE 2 - AI BRAIN ----------------
    def _build_prompt_page(self):
        page, lay = self._plain_page()
        self._page_header(lay, "Prompt Editor", "Modify the core instructions sent to AI.")

        card = QFrame(); card.setObjectName("card")
        cv = QVBoxLayout(card); cv.setContentsMargins(18, 14, 18, 14); cv.setSpacing(8)

        head = QWidget(); hh = QHBoxLayout(head); hh.setContentsMargins(0, 0, 0, 0)
        t = QLabel("Gemini Base Prompt"); t.setObjectName("h3"); hh.addWidget(t); hh.addStretch(1)
        reset = QPushButton("Reset to Default"); reset.setFixedHeight(28)
        reset.clicked.connect(self._reset_prompt)
        hh.addWidget(reset)
        cv.addWidget(head)
        line = QFrame(); line.setObjectName("hairline"); cv.addWidget(line)

        self._prompt_box = QPlainTextEdit()
        self._prompt_box.setObjectName("terminal")
        self._prompt_box.setPlainText(S["ai_prompt"])
        self._prompt_box.textChanged.connect(
            lambda: update_s("ai_prompt", self._prompt_box.toPlainText().strip())
        )
        cv.addWidget(self._prompt_box, 1)

        lay.addWidget(card, 1)
        self._add_page("prompt", page)

    def _reset_prompt(self):
        self._prompt_box.setPlainText(DEFAULT_PROMPT)
        update_s("ai_prompt", DEFAULT_PROMPT)

    # ---------------- PAGE 3 - AUDIO ----------------
    def _build_audio_page(self):
        page, lay = self._scroll_page()
        self._page_header(lay, "Audio Mixer", "Fine-tune audio levels and ducking mechanics.")

        c = Card("Volume Controls", "Balance between movie, voiceover, and music.")
        self._slider_row(c.body(), "Movie Volume (Base Level)", "vol_movie_base", 0.0, 2.0, 1.0)
        self._slider_row(c.body(), "Movie Volume (Intro)", "vol_movie_duck", 0.0, 2.0, 0.6)
        self._slider_row(c.body(), "AI Voiceover (Intro Hook)", "vol_vo_base", 0.0, 2.0, 1.0)
        self._slider_row(c.body(), "AI Voiceover (Background)", "vol_vo_duck", 0.0, 1.0, 0.06)
        self._slider_row(c.body(), "Background Music (Loop)", "vol_bgm", 0.0, 1.0, 0.05)
        lay.addWidget(c)

        import utils.theme as _t
        c2 = Card("Auto-Ducking", "Dynamically lower movie audio when AI voiceover is playing.")
        self._toggle_row(c2.body(), "Enable Audio Auto-Ducking",
                         "Use FFmpeg sidechaincompress to dip movie audio by -15 dB whenever any AI VO track is active.",
                         "enable_auto_ducking", _t.ACC)
        lay.addWidget(c2)

        lay.addStretch(1)
        self._add_page("audio", page)

    # ---------------- PAGE 4 - VIDEO/SUBS ----------------
    def _build_video_subs_page(self):
        import utils.theme as _t
        page, lay = self._scroll_page()
        self._page_header(lay, "Video & Subtitle FX", "Visual rendering pipeline and subtitle styling.")

        c1 = Card("Video Pipeline", "Speed, blur, and color grading.")
        self._option_row(c1.body(), "1:1 Fill Strategy", "fill_strategy",
                         ["Stretch", "Crop to Center", "Blur Padding"])
        self._slider_row(c1.body(), "Clip Speed", "v_speed", 1.0, 2.0, 1.1)
        self._slider_row(c1.body(), "Background Blur Amount", "v_blur", 5, 50, 30, is_int=True)
        self._slider_row(c1.body(), "Background Brightness", "v_bg_bri", -0.5, 0.5, -0.03)
        self._slider_row(c1.body(), "Background Saturation", "v_bg_sat", 0.0, 2.0, 0.92)
        self._slider_row(c1.body(), "Foreground Brightness", "v_fg_bri", -0.5, 0.5, 0.01)
        self._slider_row(c1.body(), "Foreground Saturation", "v_fg_sat", 0.0, 2.0, 1.12)
        self._slider_row(c1.body(), "Foreground Contrast", "v_fg_con", 0.5, 1.5, 1.08)
        lay.addWidget(c1)

        c2 = Card("Subtitle Styling", "ASS format - font, size, colors.")
        self._font_combo_row(c2.body(), "Font Family", "sub_font", "Readfine", SUB_FONTS)
        self._slider_row(c2.body(), "Font Size", "sub_size", 40, 150, 92, is_int=True)
        self._slider_row(c2.body(), "Height ", "sub_margin_v", 50, 1000, 430, is_int=True)
        self._color_row(c2.body(), "Primary Color", "sub_pri", "#FFFFFF")
        self._color_row(c2.body(), "Secondary Color", "sub_sec", "#FFFF00")
        self._color_row(c2.body(), "Outline Color", "sub_out", "#000000")
        self._color_row(c2.body(), "Shadow Color", "sub_bak", "#000000")
        self._slider_row(c2.body(), "Outline Thickness", "sub_out_w", 0, 10, 3, is_int=True)
        self._slider_row(c2.body(), "Shadow Thickness", "sub_sha_w", 0, 10, 3, is_int=True)
        lay.addWidget(c2)

        c3 = Card("Logo Overlay", "Floating logo with transparency.")
        self._toggle_row(c3.body(), "Enable Logo Overlay",
                         "Show darklogo.png floating overlay on rendered clips (requires darklogo.png in app folder)",
                         "logo_enabled", _t.GREEN)
        lay.addWidget(c3)

        c4 = Card("Advanced Clip Features", "AI-driven hooks, impact moments, and render tricks.")
        self._toggle_row(c4.body(), "Enable Cold-Open Hook",
                         "Prepend a 3-second out-of-context quote to the start of every clip (AI picks the hook).",
                         "enable_cold_open", _t.ACC)
        self._toggle_row(c4.body(), "Enable Mid-Clip Impact VO",
                         "Freeze-frame at the most dramatic moment with a digital zoom and AI voiceover.",
                         "enable_impact_vo", _t.GREEN)
        self._toggle_row(c4.body(), "Enable Mirror Cuts",
                         "Alternate video chunks are horizontally flipped for a dynamic mirror-cut effect.",
                         "enable_mirror_cuts", _t.YEL)
        self._toggle_row(c4.body(), "Enable Silence Cutting",
                         "Remove silent segments from the audio to tighten pacing (Tempo Booster).",
                         "enable_silence_cut", _t.CYAN)
        self._toggle_row(c4.body(), "Enable Motion Blur Transitions",
                         "Blend frames at cut points using minterpolate (increases render time).",
                         "enable_motion_blur", _t.RED)
        lay.addWidget(c4)

        lay.addStretch(1)
        self._add_page("video", page)

    # ---------------- PAGE 5 - ENGINE ----------------
    def _build_engine_page(self):
        import utils.theme as _t
        page, lay = self._plain_page()
        self._page_header(lay, "Create", "Launch the batch processor - watch the magic.")

        hero = QFrame(); hero.setObjectName("card")
        hh = QHBoxLayout(hero); hh.setContentsMargins(22, 18, 22, 18); hh.setSpacing(12)
        lcol = QVBoxLayout(); lcol.setSpacing(2)
        title = QLabel("Ready to process"); title.setObjectName("h2"); lcol.addWidget(title)
        self._hero_sub = QLabel("Configure folders and API key, then launch the batch.")
        self._hero_sub.setObjectName("sub"); lcol.addWidget(self._hero_sub)
        lwrap = QWidget(); lwrap.setLayout(lcol); hh.addWidget(lwrap, 1)

        # Button container for Start Batch + Show in folder
        btn_container = QWidget()
        btn_h = QHBoxLayout(btn_container); btn_h.setContentsMargins(0, 0, 0, 0); btn_h.setSpacing(10)

        # Show in folder button (square folder icon)
        self._show_folder_btn = QPushButton()
        self._show_folder_btn.setObjectName("softAction")
        self._show_folder_btn.setFixedSize(52, 52)
        # Load folder icon from image
        folder_icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "folderlogo.png")
        if os.path.exists(folder_icon_path):
            icon = QIcon(folder_icon_path)
            self._show_folder_btn.setIcon(icon)
            self._show_folder_btn.setIconSize(QSize(28, 28))
        self._show_folder_btn.setStyleSheet(
            f"QPushButton {{ border-radius: 10px; "
            f"background: {_t.CARD2}; border: 1px solid {_t.BOR}; }}"
            f"QPushButton:hover {{ background: {_t.CARD3}; border-color: {_t.ACC}; }}"
            f"QPushButton:pressed {{ background: {_t.ACC2}; }}"
        )
        # Add opacity effect to darken icon on hover
        self._folder_icon_opacity = QGraphicsOpacityEffect(self._show_folder_btn)
        self._folder_icon_opacity.setOpacity(1.0)
        self._show_folder_btn.setGraphicsEffect(self._folder_icon_opacity)
        self._show_folder_btn.enterEvent = lambda e: self._folder_icon_opacity.setOpacity(0.6)
        self._show_folder_btn.leaveEvent = lambda e: self._folder_icon_opacity.setOpacity(1.0)
        self._show_folder_btn.setToolTip("Open clips output folder in File Explorer")
        self._show_folder_btn.clicked.connect(self._on_show_in_folder)
        btn_h.addWidget(self._show_folder_btn)

        self._start_btn = QPushButton("Start Batch")
        self._start_btn.setObjectName("primary")
        self._start_btn.setFixedSize(220, 52)
        self._start_btn.setStyleSheet(
            f"QPushButton#primary {{ font-size: 20px; font-weight: 600; border-radius: 10px; "
            f"background: {_t.ACC2}; color: #ffffff; border: 1px solid {_t.ACC2}; letter-spacing: 0.3px; }}"
            f"QPushButton#primary:hover {{ background: {_t.ACC_DK}; border-color: {_t.ACC}; }}"
            f"QPushButton#primary:disabled {{ background: {_t.CARD3}; color: {_t.SUB_DK}; border-color: {_t.BOR}; }}"
        )
        self._start_btn.clicked.connect(self._on_start_cancel)
        btn_h.addWidget(self._start_btn)
        hh.addWidget(btn_container)
        lay.addWidget(hero)

        pc = Card("Progress", "Live batch telemetry.")

        # Elapsed and Estimated time row - at the TOP of progress card
        time_row = QWidget(); tr_h = QHBoxLayout(time_row); tr_h.setContentsMargins(0, 0, 0, 8)
        self._elapsed_lbl = QLabel("Elapsed: 00:00:00")
        self._elapsed_lbl.setObjectName("subSm")
        self._elapsed_lbl.setStyleSheet(f"color: {_t.ACC}; font-family: 'Cascadia Mono', Consolas, monospace; font-weight: 600;")
        tr_h.addWidget(self._elapsed_lbl, 1)
        self._eta_lbl = QLabel("ETA: --:--:--")
        self._eta_lbl.setObjectName("subSm")
        self._eta_lbl.setStyleSheet(f"color: {_t.GREEN}; font-family: 'Cascadia Mono', Consolas, monospace; font-weight: 600;")
        self._eta_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        tr_h.addWidget(self._eta_lbl)
        pc.body().addWidget(time_row)

        mr = QWidget(); mh = QHBoxLayout(mr); mh.setContentsMargins(0, 0, 0, 0)
        self._micro_lbl = QLabel("Idle"); self._micro_lbl.setObjectName("sub"); mh.addWidget(self._micro_lbl, 1)
        self._micro_pct = QLabel("0%"); self._micro_pct.setObjectName("pctLabel"); mh.addWidget(self._micro_pct)
        pc.body().addWidget(mr)
        self._micro_bar = AnimatedProgressBar(color=_t.ACC, height=8)
        pc.body().addWidget(self._micro_bar)
        pc.body().addSpacing(8)

        mr2 = QWidget(); mh2 = QHBoxLayout(mr2); mh2.setContentsMargins(0, 0, 0, 0)
        self._macro_lbl = QLabel("Overall \u00B7 Movie 0 / 0"); self._macro_lbl.setObjectName("h3"); mh2.addWidget(self._macro_lbl, 1)
        self._macro_pct = QLabel("0%"); self._macro_pct.setStyleSheet(f"color: {_t.ACC2}; font-family: 'Cascadia Mono', Consolas, monospace;")
        mh2.addWidget(self._macro_pct)
        pc.body().addWidget(mr2)
        self._macro_bar = AnimatedProgressBar(color=_t.ACC2, height=14)
        pc.body().addWidget(self._macro_bar)
        lay.addWidget(pc)

        tc = QFrame(); tc.setObjectName("card")
        tv = QVBoxLayout(tc); tv.setContentsMargins(18, 14, 18, 14); tv.setSpacing(8)
        head = QWidget(); hh = QHBoxLayout(head); hh.setContentsMargins(0, 0, 0, 0)
        t = QLabel("Live Terminal"); t.setObjectName("h3"); hh.addWidget(t); hh.addStretch(1)
        clr = QPushButton("Clear")
        clr.setFixedHeight(32); clr.setMinimumWidth(80)
        clr.clicked.connect(self._clear_terminal)
        hh.addWidget(clr)
        tv.addWidget(head)
        line = QFrame(); line.setObjectName("hairline"); tv.addWidget(line)

        self._term = QPlainTextEdit()
        self._term.setObjectName("terminal")
        self._term.setReadOnly(True)
        self._term.setStyleSheet(self._term.styleSheet() + f"QPlainTextEdit#terminal {{ color: {_t.GREEN}; }}")
        tv.addWidget(self._term, 1)
        lay.addWidget(tc, 1)

        self._tlog("-" * 62, "dim")
        self._tlog("  CClip -  Ready", "success")
        self._tlog("  Configure folders + API key, then press Start Batch.", "info")
        self._tlog("-" * 62, "dim")

        self._add_page("engine", page)

    def _clear_terminal(self):
        self._term.clear()

    def _tlog(self, msg, color="normal"):
        import utils.theme as _t
        color_map = {
            "error": _t.RED, "warn": _t.YEL, "success": _t.GREEN,
            "info": _t.ACC, "dim": _t.SUB_DK, "mono": _t.SUB_DK, "normal": _t.GREEN,
        }
        c = color_map.get(color, _t.GREEN)
        ts = datetime.now().strftime("%H:%M:%S")
        # Escape HTML-sensitive chars
        safe = (msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        self._term.appendHtml(f'<span style="color:{_t.SUB_DK};">[{ts}]</span>  <span style="color:{c};">{safe}</span>')
        # Also write to log file (strip HTML for log)
        self._write_log(msg)

    # ---------------- PAGE 6 - ANALYTICS ----------------
    def _build_analytics_page(self):
        import utils.theme as _t
        page, lay = self._plain_page()
        # Set page background to match theme
        page.setStyleSheet(f"QWidget#pageBg {{ background: {_t.BG}; }}")
        self._page_header(lay, "Analytics Dashboard", "YouTube channel insights - performance, metrics, and content analysis.")

        # ========== HEADER: CHANNEL STATS ==========
        header_card = QFrame(); header_card.setObjectName("card")
        hh = QHBoxLayout(header_card); hh.setContentsMargins(20, 16, 20, 16); hh.setSpacing(16)

        # Channel info (left side)
        ch_info = QVBoxLayout(); ch_info.setSpacing(4)
        self._ch_name_lbl = QLabel("Not Connected")
        self._ch_name_lbl.setObjectName("h2")
        self._ch_name_lbl.setStyleSheet(f"color: {_t.TEXT}; font-weight: 700;")
        ch_info.addWidget(self._ch_name_lbl)

        self._ch_subtitle = QLabel("Authenticate via YouTube Panel to see live data")
        self._ch_subtitle.setObjectName("sub")
        ch_info.addWidget(self._ch_subtitle)
        hh.addLayout(ch_info, 1)

        # Stats counters
        stats_layout = QHBoxLayout(); stats_layout.setSpacing(12)
        self._ch_subs = self._stat_badge(stats_layout, "0", "Subscribers", _t.GREEN)
        self._ch_views = self._stat_badge(stats_layout, "0", "Views", _t.ACC)
        self._ch_videos = self._stat_badge(stats_layout, "0", "Videos", _t.YEL)
        self._ch_watch_hours = self._stat_badge(stats_layout, "0", "Watch Hours", _t.CYAN)
        self._quota_badge = self._stat_badge(stats_layout, "0/10K", "Quota Used", _t.SUB)
        hh.addLayout(stats_layout)

        lay.addWidget(header_card)

        # ========== CONTROLS ROW ==========
        controls = QWidget(); cl = QHBoxLayout(controls); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(12)

        # Refresh button with spinner
        self._refresh_btn = QPushButton("\u21bb  Refresh Analytics")
        self._refresh_btn.setObjectName("primary")
        self._refresh_btn.setFixedHeight(42)
        self._refresh_btn.setFixedWidth(200)
        self._refresh_btn.clicked.connect(self._on_refresh_analytics)
        cl.addWidget(self._refresh_btn)

        # Loading spinner label (hidden by default)
        self._loading_lbl = QLabel("")
        self._loading_lbl.setStyleSheet(f"color: {_t.ACC}; font-size: 13px;")
        cl.addWidget(self._loading_lbl)
        cl.addStretch(1)

        # Date range selector (styled like FontPicker)
        self._date_range_combo = self._create_styled_combo(
            ["Last 7 Days", "Last 28 Days", "Last 90 Days", "Lifetime"],
            "Last 28 Days",
            width=160,
            callback=lambda text: self._update_analytics_ui()
        )
        cl.addWidget(self._date_range_combo)

        # Graphs Toggle Button
        self._graphs_toggle = QPushButton("Show Insights")
        self._graphs_toggle.setObjectName("softAction")
        self._graphs_toggle.setFixedHeight(42)
        self._graphs_toggle.setFixedWidth(160)
        self._graphs_toggle.setCheckable(True)
        self._graphs_toggle.clicked.connect(self._on_toggle_graphs)
        cl.addWidget(self._graphs_toggle)

        lay.addWidget(controls)

        # ========== COLLAPSIBLE GRAPHS SECTION ==========
        self._graphs_container = QWidget()
        self._graphs_container.setVisible(False)
        graphs_scroll = QScrollArea()
        graphs_scroll.setWidgetResizable(True)
        graphs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        graphs_scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}")

        graphs_widget = QWidget()
        graphs_widget.setStyleSheet(f"background: {_t.BG};")
        gl = QVBoxLayout(graphs_widget); gl.setContentsMargins(0, 0, 0, 0); gl.setSpacing(16)

        # SECTION 1: The Big Picture (Cumulative Growth)
        s1_card = Card("Growth Overview", "Channel trajectory and cumulative momentum")
        s1_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        s1_grid = QWidget(); s1l = QVBoxLayout(s1_grid); s1l.setContentsMargins(4, 4, 4, 4); s1l.setSpacing(14)

        # Total Views Graph
        views_card = QFrame()
        views_card.setObjectName("card")
        views_card.setStyleSheet(f"QFrame {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 8px; }}")
        views_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vvl = QVBoxLayout(views_card); vvl.setContentsMargins(12, 10, 12, 10)
        vtl = QLabel("Views Growth Trend"); vtl.setObjectName("h3"); vvl.addWidget(vtl)
        self._cumulative_views_fig = Figure(figsize=(10, 3.2), dpi=100, facecolor=_t.CARD, edgecolor=_t.CARD)
        self._cumulative_views_ax = self._cumulative_views_fig.add_subplot(111)
        self._cumulative_views_ax.set_facecolor(_t.CARD)
        self._cumulative_views_canvas = FigureCanvas(self._cumulative_views_fig)
        self._cumulative_views_canvas.setStyleSheet(f"background: {_t.CARD};")
        self._cumulative_views_canvas.setMinimumHeight(245)
        self._cumulative_views_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vvl.addWidget(self._cumulative_views_canvas, 1)
        s1l.addWidget(views_card, 1)

        # Total Subscribers Graph
        subs_card = QFrame()
        subs_card.setObjectName("card")
        subs_card.setStyleSheet(f"QFrame {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 8px; }}")
        subs_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ssl = QVBoxLayout(subs_card); ssl.setContentsMargins(12, 10, 12, 10)
        stl = QLabel("Subscriber Growth Trend"); stl.setObjectName("h3"); ssl.addWidget(stl)
        self._cumulative_subs_fig = Figure(figsize=(10, 3.2), dpi=100, facecolor=_t.CARD, edgecolor=_t.CARD)
        self._cumulative_subs_ax = self._cumulative_subs_fig.add_subplot(111)
        self._cumulative_subs_ax.set_facecolor(_t.CARD)
        self._cumulative_subs_canvas = FigureCanvas(self._cumulative_subs_fig)
        self._cumulative_subs_canvas.setStyleSheet(f"background: {_t.CARD};")
        self._cumulative_subs_canvas.setMinimumHeight(245)
        self._cumulative_subs_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ssl.addWidget(self._cumulative_subs_canvas, 1)
        s1l.addWidget(subs_card, 1)

        s1_card.body().addWidget(s1_grid)
        gl.addWidget(s1_card)

        # SECTION 2: The Daily Pulse (Daily Activity)
        s2_card = Card("Daily Pulse", "Recent activity across views, subscribers, and watch time")
        s2_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        s2_grid = QWidget(); s2l = QHBoxLayout(s2_grid); s2l.setContentsMargins(4, 4, 4, 4); s2l.setSpacing(10)

        # Views Per Day
        vpd_card = QFrame()
        vpd_card.setObjectName("card")
        vpd_card.setStyleSheet(f"QFrame {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 8px; }}")
        vpdl = QVBoxLayout(vpd_card); vpdl.setContentsMargins(12, 10, 12, 10)
        vpdtl = QLabel("Views Per Day"); vpdtl.setObjectName("h3"); vpdl.addWidget(vpdtl)
        self._daily_views_fig = Figure(figsize=(4.5, 3.0), dpi=100, facecolor=_t.CARD, edgecolor=_t.CARD)
        self._daily_views_ax = self._daily_views_fig.add_subplot(111)
        self._daily_views_ax.set_facecolor(_t.CARD)
        self._daily_views_canvas = FigureCanvas(self._daily_views_fig)
        self._daily_views_canvas.setStyleSheet(f"background: {_t.CARD};")
        self._daily_views_canvas.setAutoFillBackground(False)
        self._daily_views_canvas.setMinimumHeight(180)
        vpdl.addWidget(self._daily_views_canvas, 1)
        s2l.addWidget(vpd_card, 1)

        # Subs Per Day
        spd_card = QFrame()
        spd_card.setObjectName("card")
        spd_card.setStyleSheet(f"QFrame {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 8px; }}")
        spdl = QVBoxLayout(spd_card); spdl.setContentsMargins(12, 10, 12, 10)
        spdtl = QLabel("Subscribers Per Day"); spdtl.setObjectName("h3"); spdl.addWidget(spdtl)
        self._daily_subs_fig = Figure(figsize=(4.5, 3.0), dpi=100, facecolor=_t.CARD, edgecolor=_t.CARD)
        self._daily_subs_ax = self._daily_subs_fig.add_subplot(111)
        self._daily_subs_ax.set_facecolor(_t.CARD)
        self._daily_subs_canvas = FigureCanvas(self._daily_subs_fig)
        self._daily_subs_canvas.setStyleSheet(f"background: {_t.CARD};")
        self._daily_subs_canvas.setAutoFillBackground(False)
        self._daily_subs_canvas.setMinimumHeight(180)
        spdl.addWidget(self._daily_subs_canvas, 1)
        s2l.addWidget(spd_card, 1)

        # Watch Hours Per Day
        whpd_card = QFrame()
        whpd_card.setObjectName("card")
        whpd_card.setStyleSheet(f"QFrame {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 8px; }}")
        whpdl = QVBoxLayout(whpd_card); whpdl.setContentsMargins(12, 10, 12, 10)
        whpdtl = QLabel("Watch Hours Per Day"); whpdtl.setObjectName("h3"); whpdl.addWidget(whpdtl)
        self._daily_watch_fig = Figure(figsize=(4.5, 3.0), dpi=100, facecolor=_t.CARD, edgecolor=_t.CARD)
        self._daily_watch_ax = self._daily_watch_fig.add_subplot(111)
        self._daily_watch_ax.set_facecolor(_t.CARD)
        self._daily_watch_canvas = FigureCanvas(self._daily_watch_fig)
        self._daily_watch_canvas.setStyleSheet(f"background: {_t.CARD};")
        self._daily_watch_canvas.setAutoFillBackground(False)
        self._daily_watch_canvas.setMinimumHeight(180)
        whpdl.addWidget(self._daily_watch_canvas, 1)
        s2l.addWidget(whpd_card, 1)

        s2_card.body().addWidget(s2_grid)
        gl.addWidget(s2_card)

        # SECTION 3: Content Performance (Video Metrics + Demographics)
        s3_card = Card("Content Performance & Audience", "Shorts performance, distribution, and viewer profile")
        s3_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        s3_grid = QWidget(); s3l = QHBoxLayout(s3_grid); s3l.setContentsMargins(4, 4, 4, 4); s3l.setSpacing(10)

        # Left: Top Shorts + Traffic Sources
        left_col = QVBoxLayout(); left_col.setSpacing(12)

        top_shorts_card = QFrame()
        top_shorts_card.setObjectName("card")
        top_shorts_card.setStyleSheet(f"QFrame {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 8px; }}")
        tsc = QVBoxLayout(top_shorts_card); tsc.setContentsMargins(12, 10, 12, 10)
        tsctl = QLabel("Top Shorts This Week"); tsctl.setObjectName("h3"); tsc.addWidget(tsctl)
        self._top_shorts_list = QVBoxLayout()
        self._top_shorts_list.setSpacing(6)
        tsc.addLayout(self._top_shorts_list, 1)
        left_col.addWidget(top_shorts_card)

        traffic_card = QFrame()
        traffic_card.setObjectName("card")
        traffic_card.setStyleSheet(f"QFrame {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 8px; }}")
        tc = QVBoxLayout(traffic_card); tc.setContentsMargins(12, 10, 12, 10)
        tcl = QLabel("Traffic Sources"); tcl.setObjectName("h3"); tc.addWidget(tcl)
        self._traffic_sources_list = QVBoxLayout()
        self._traffic_sources_list.setSpacing(4)
        tc.addLayout(self._traffic_sources_list, 1)
        left_col.addWidget(traffic_card)
        s3l.addLayout(left_col, 1)

        # Middle: Retention
        mid_col = QVBoxLayout(); mid_col.setSpacing(12)

        retention_card = QFrame()
        retention_card.setObjectName("card")
        retention_card.setStyleSheet(f"QFrame {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 8px; }}")
        rc = QVBoxLayout(retention_card); rc.setContentsMargins(12, 10, 12, 10)
        rcl = QLabel("Shorts Retention"); rcl.setObjectName("h3"); rc.addWidget(rcl)
        self._retention_fig = Figure(figsize=(3.5, 2.8), dpi=100, facecolor=_t.CARD, edgecolor=_t.CARD)
        self._retention_ax = self._retention_fig.add_subplot(111)
        self._retention_ax.set_facecolor(_t.CARD)
        self._retention_canvas = FigureCanvas(self._retention_fig)
        self._retention_canvas.setStyleSheet(f"background: {_t.CARD};")
        self._retention_canvas.setAutoFillBackground(False)
        self._retention_canvas.setMinimumHeight(180)
        rc.addWidget(self._retention_canvas, 1)
        mid_col.addWidget(retention_card)
        s3l.addLayout(mid_col, 1)

        # Right: Demographics
        right_col = QVBoxLayout(); right_col.setSpacing(12)

        demo_card = QFrame()
        demo_card.setObjectName("card")
        demo_card.setStyleSheet(f"QFrame {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 8px; }}")
        dc = QVBoxLayout(demo_card); dc.setContentsMargins(12, 10, 12, 10)
        dcl = QLabel("Age & Gender"); dcl.setObjectName("h3"); dc.addWidget(dcl)
        self._demo_fig = Figure(figsize=(3.5, 2.8), dpi=100, facecolor=_t.CARD, edgecolor=_t.CARD)
        self._demo_ax = self._demo_fig.add_subplot(111)
        self._demo_ax.set_facecolor(_t.CARD)
        self._demo_canvas = FigureCanvas(self._demo_fig)
        self._demo_canvas.setStyleSheet(f"background: {_t.CARD};")
        self._demo_canvas.setAutoFillBackground(False)
        self._demo_canvas.setMinimumHeight(140)
        dc.addWidget(self._demo_canvas, 1)
        right_col.addWidget(demo_card)

        device_card = QFrame()
        device_card.setObjectName("card")
        device_card.setStyleSheet(f"QFrame {{ background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 8px; }}")
        devc = QVBoxLayout(device_card); devc.setContentsMargins(12, 10, 12, 10)
        devcl = QLabel("Device Type"); devcl.setObjectName("h3"); devc.addWidget(devcl)
        self._device_fig = Figure(figsize=(3.5, 2.8), dpi=100, facecolor=_t.CARD, edgecolor=_t.CARD)
        self._device_ax = self._device_fig.add_subplot(111)
        self._device_ax.set_facecolor(_t.CARD)
        self._device_canvas = FigureCanvas(self._device_fig)
        self._device_canvas.setStyleSheet(f"background: {_t.CARD};")
        self._device_canvas.setAutoFillBackground(False)
        self._device_canvas.setMinimumHeight(140)
        devc.addWidget(self._device_canvas, 1)
        right_col.addWidget(device_card)
        s3l.addLayout(right_col, 1)

        s3_card.body().addWidget(s3_grid)
        gl.addWidget(s3_card)

        graphs_widget.setLayout(gl)
        graphs_scroll.setWidget(graphs_widget)

        graphs_main_layout = QVBoxLayout(self._graphs_container)
        graphs_main_layout.setContentsMargins(0, 0, 0, 0)
        graphs_main_layout.addWidget(graphs_scroll)

        lay.addWidget(self._graphs_container)

        # ========== VIDEO LIST (Always Visible) ==========
        self._videos_card = Card("Video Performance", "All uploaded videos with metrics and copyright status")
        self._videos_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._videos_scroll = QScrollArea()
        self._videos_scroll.setWidgetResizable(True)
        self._videos_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._videos_scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}")

        self._videos_container = QWidget()
        self._videos_container.setObjectName("analyticsVideosContainer")
        self._videos_container.setStyleSheet(f"QWidget#analyticsVideosContainer {{ background: {_t.BG}; }}")
        self._videos_layout = QVBoxLayout(self._videos_container)
        self._videos_layout.setContentsMargins(12, 12, 12, 12)
        self._videos_layout.setSpacing(8)
        self._videos_layout.addStretch(1)

        self._videos_scroll.setWidget(self._videos_container)
        self._videos_card.body().addWidget(self._videos_scroll, 1)
        lay.addWidget(self._videos_card, 1)

        # Store analytics data and state
        self._analytics_data = None
        self._analytics_thread = None
        self._is_graphs_expanded = False

        # Initialize empty state
        self._update_analytics_ui()
        self._add_page("analytics", page)

    def _stat_badge(self, parent, value, label, color):
        """Create a stat badge for the header."""
        import utils.theme as _t
        badge = QFrame()
        badge.setObjectName("analyticsStatBadge")
        badge.setStyleSheet(
            f"QFrame#analyticsStatBadge {{ background: {_t.CARD2}; border: 1px solid {_t.BOR_SFT}; border-radius: 10px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        v = QVBoxLayout(badge); v.setContentsMargins(12, 8, 12, 8); v.setSpacing(2)
        val_lbl = QLabel(value)
        val_lbl.setObjectName("h2")
        val_lbl.setStyleSheet(f"color: {color}; font-weight: 700; font-size: 20px;")
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(val_lbl)
        lbl = QLabel(label)
        lbl.setObjectName("subSm")
        lbl.setStyleSheet(f"color: {_t.SUB}; font-size: 10px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(lbl)
        parent.addWidget(badge)
        return val_lbl

    def _to_float(self, value, default=0.0):
        try:
            if value is None or value == "":
                return default
            return float(value)
        except Exception:
            return default

    def _to_int(self, value, default=0):
        return int(round(self._to_float(value, default)))

    def _format_compact_number(self, value, _pos=None):
        value = self._to_float(value, 0)
        sign = "-" if value < 0 else ""
        value = abs(value)
        if value >= 1_000_000_000:
            return f"{sign}{value / 1_000_000_000:.1f}B"
        if value >= 1_000_000:
            return f"{sign}{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{sign}{value / 1_000:.1f}K"
        if value == int(value):
            return f"{sign}{int(value)}"
        return f"{sign}{value:.1f}"

    def _current_analytics_limit(self):
        if not hasattr(self, "_date_range_combo"):
            return 28
        return {
            "Last 7 Days": 7,
            "Last 28 Days": 28,
            "Last 90 Days": 90,
            "Lifetime": None,
        }.get(self._date_range_combo.currentText(), 28)

    def _limit_analytics_series(self, rows):
        rows = list(rows or [])
        limit = self._current_analytics_limit()
        if limit is None:
            return rows
        return rows[-limit:]

    def _day_label(self, value, compact=False):
        if not value:
            return ""
        try:
            parsed = datetime.strptime(str(value)[:10], "%Y-%m-%d")
            return parsed.strftime("%d %b" if compact else "%a")
        except Exception:
            text = str(value)
            return text[-5:] if compact and len(text) >= 5 else text

    def _setup_analytics_axes(self, fig, ax, ylabel=None, grid_axis="y"):
        fig.patch.set_facecolor(_t.CARD)
        ax.set_facecolor(_t.CARD)
        ax.tick_params(colors=_t.SUB_DK, labelsize=8, length=0)
        ax.grid(True, axis=grid_axis, color=_t.BOR_SFT, linestyle="-", linewidth=0.7, alpha=0.55)
        ax.set_axisbelow(True)
        ax.yaxis.set_major_formatter(FuncFormatter(self._format_compact_number))
        if ylabel:
            ax.set_ylabel(ylabel, color=_t.SUB_DK, fontsize=8)
        for sp in ax.spines.values():
            sp.set_edgecolor(_t.BOR_SFT)
            sp.set_linewidth(0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    def _finish_analytics_chart(self, fig, canvas):
        import utils.theme as _t
        fig.patch.set_facecolor(_t.CARD)
        fig.patch.set_alpha(1.0)
        canvas.setStyleSheet(f"background: {_t.CARD};")
        canvas.setAutoFillBackground(False)
        try:
            fig.tight_layout(pad=1.0)
        except Exception:
            pass
        canvas.draw_idle()

    def _draw_empty_chart(self, fig, ax, canvas, message):
        import utils.theme as _t
        ax.clear()
        fig.patch.set_facecolor(_t.CARD)
        ax.set_facecolor(_t.CARD)
        ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes, color=_t.SUB_DK, fontsize=10)
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
        self._finish_analytics_chart(fig, canvas)

    def _clear_layout(self, layout):
        while layout.count() > 0:
            item = layout.takeAt(0)
            child = item.layout()
            if child:
                self._clear_layout(child)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _empty_list_row(self, text):
        import utils.theme as _t
        row = QFrame()
        row.setObjectName("analyticsEmptyRow")
        row.setStyleSheet(
            f"QFrame#analyticsEmptyRow {{ background: {_t.CARD2}; border: 1px solid {_t.BOR_SFT}; border-radius: 10px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        h = QHBoxLayout(row); h.setContentsMargins(14, 12, 14, 12); h.setSpacing(10)
        dot = QLabel("—")
        dot.setStyleSheet(f"color: {_t.SUB_DK}; font-size: 18px; font-weight: 700;")
        dot.setFixedWidth(24)
        dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(dot)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {_t.SUB}; font-size: 12px;")
        h.addWidget(lbl, 1)
        return row

    # ---------------- PAGE 7 - YOUTUBE ----------------
    def _build_youtube_page(self):
        import utils.theme as _t
        page, lay = self._scroll_page()
        self._page_header(lay, "YouTube Control Panel", "Authenticate, set privacy, and upload.")

        user_data_dir = os.path.join(os.path.dirname(__file__), "..", "user_data")
        token_path = os.path.join(user_data_dir, "token.json")
        
        ac = Card("Authentication Status", f"OAuth credentials live in {token_path}.")
        ar = QWidget(); ah = QHBoxLayout(ar); ah.setContentsMargins(0, 6, 0, 6); ah.setSpacing(10)
        self._auth_dot = QLabel("\u25CF"); self._auth_dot.setStyleSheet(f"color: {_t.RED}; font-size: 22px;")
        ah.addWidget(self._auth_dot)
        self._auth_lbl = QLabel(f"  Not authenticated  -  token.json not found in {user_data_dir}/")
        self._auth_lbl.setObjectName("h3")
        self._auth_lbl.setStyleSheet(f"color: {_t.RED}; font-weight: 600;")
        ah.addWidget(self._auth_lbl, 1)
        self._login_btn = QPushButton("Login to YouTube"); self._login_btn.setObjectName("softAction")
        self._login_btn.setFixedSize(200, 36)
        self._login_btn.clicked.connect(self._yt_login)
        ah.addWidget(self._login_btn)
        ac.body().addWidget(ar)

        if os.path.exists(token_path):
            self._set_auth_ui(True)

        inst = QFrame()
        inst.setStyleSheet(
            f"QFrame {{ background: {_t.CARD2}; border: 1px solid {_t.BOR_SFT}; border-radius: 8px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        ih = QHBoxLayout(inst); ih.setContentsMargins(14, 12, 14, 12); ih.setSpacing(12)
        info = QLabel("\u24D8")
        info.setStyleSheet(f"color: {_t.ACC}; font-size: 22px; background: transparent; border: none;")
        info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        info.setFixedWidth(24)
        ih.addWidget(info)
        iv = QVBoxLayout(); iv.setContentsMargins(0, 0, 0, 0); iv.setSpacing(4)
        it = QLabel("How to authenticate")
        it.setStyleSheet(f"color: {_t.TEXT}; font-weight: 600; background: transparent; border: none;")
        iv.addWidget(it)
        il = QLabel(f"Download client_secrets.json from Google Cloud Console \u2192 APIs & Services \u2192 Credentials \u2192 OAuth 2.0 Client IDs \u2192 Desktop App. Place the file in the {user_data_dir}/ folder.")
        il.setWordWrap(True)
        il.setStyleSheet(f"color: {_t.SUB}; font-size: 12px; background: transparent; border: none;")
        iv.addWidget(il)
        ih.addLayout(iv, 1)
        ac.body().addWidget(inst)
        lay.addWidget(ac)

        pm = Card("Upload Settings", "Private drafts are safer during testing.")
        self._toggle_row(pm.body(), "Publish Mode - Public Upload",
                         "OFF = Private (safe)    ON = Public (visible worldwide - irreversible)",
                         "pub_mode", _t.RED)
        self._toggle_row(pm.body(), "Auto-Upload After Render",
                         "Automatically upload all rendered clips when batch completes (requires authentication)",
                         "auto_upload", _t.GREEN)
        lay.addWidget(pm)

        uc = Card("Batch Uploader", "Upload every pending clip in the output folder.")
        ur = QWidget(); uh = QHBoxLayout(ur); uh.setContentsMargins(0, 6, 0, 6)
        self._upload_btn = QPushButton("Upload All Pending"); self._upload_btn.setObjectName("softAction")
        self._upload_btn.setFixedSize(260, 46)
        self._upload_btn.clicked.connect(self._on_upload_all)
        uh.addWidget(self._upload_btn); uh.addStretch(1)
        uc.body().addWidget(ur)
        lay.addWidget(uc)

        lc = Card("Upload Log", "Real-time log of uploads.")
        self._yt_term = QPlainTextEdit(); self._yt_term.setObjectName("ytTerminal")
        self._yt_term.setReadOnly(True); self._yt_term.setFixedHeight(260)
        self._yt_term.setStyleSheet(self._yt_term.styleSheet() + f"QPlainTextEdit#ytTerminal {{ color: {_t.ACC}; }}")
        self._yt_term.setPlainText("  YouTube upload log will appear here.\n  Authenticate first, then click 'Upload All Pending'.")
        lc.body().addWidget(self._yt_term)
        lay.addWidget(lc)

        lay.addStretch(1)
        self._add_page("youtube", page)

    def _set_auth_ui(self, ok):
        import utils.theme as _t
        user_data_dir = os.path.join(os.path.dirname(__file__), "..", "user_data")
        token_path = os.path.join(user_data_dir, "token.json")
        update_s("yt_authed", ok, trigger_save_popup=False)
        color = _t.GREEN if ok else _t.RED
        text = f"  Authenticated  -  {token_path} found" if ok else f"  Not authenticated  -  {token_path} not found"
        self._auth_dot.setStyleSheet(f"color: {color}; font-size: 22px;")
        self._auth_lbl.setStyleSheet(f"color: {color}; font-weight: 600;")
        self._auth_lbl.setText(text)
        self._login_btn.setText("Re-Authenticate" if ok else "Login to YouTube")

    def _yt_login(self):
        user_data_dir = os.path.join(os.path.dirname(__file__), "..", "user_data")
        token_path = os.path.join(user_data_dir, "token.json")
        self._yt_write("  Opening browser for OAuth...\n  Follow the login steps.")
        self._auth_thread = AuthThread()
        self._auth_thread.success.connect(lambda: (self._set_auth_ui(True), self._yt_write(f"  Authentication successful.  {token_path} saved.")))
        self._auth_thread.error.connect(lambda msg: QMessageBox.critical(self, "Auth Error", msg))
        self._auth_thread.start()

    def _on_upload_all(self):
        self._yt_write("  Starting upload session...", clear=False)
        self._upload_btn.setText("Uploading\u2026"); self._upload_btn.setEnabled(False)
        self._upload_btn.setStyleSheet(f"QPushButton {{ background: {_t.YEL}; color: #1c1c1c; border: 1px solid {_t.YEL}; border-radius: 6px; }}")
        self._upload_thread = UploadThread()
        self._upload_thread.finished_ok.connect(self._on_upload_done)
        self._upload_thread.start()

    def _on_upload_done(self, ok):
        self._upload_btn.setText("Upload All Pending"); self._upload_btn.setEnabled(True)
        self._upload_btn.setStyleSheet("")  # fall back to QSS #softAction
        self._upload_btn.setObjectName("softAction")
        self._upload_btn.style().unpolish(self._upload_btn); self._upload_btn.style().polish(self._upload_btn)

    def _yt_write(self, msg, clear=True):
        if clear:
            self._yt_term.setPlainText(msg)
        else:
            self._yt_term.appendPlainText(msg)
        # Also write to log file with [YouTube] prefix
        self._write_log(f"[YouTube] {msg}")

    # ---------------- PAGE 8 - UI CONFIG ----------------
    def _build_uiconfig_page(self):
        page, lay = self._scroll_page()
        self._page_header(lay, "UI Configuration", "Theme, font, scale, and a custom palette builder.")

        tc = Card("Appearance", "Light, Dark, or build your own custom palette \u2014 changes apply instantly.")
        self._option_row(tc.body(), "Theme Mode", "ui_theme", ["Dark", "Light", "Custom"],
                         callback=lambda v: self.apply_live_theme())
        self._font_combo_row(tc.body(), "Font Family", "ui_font", "Segoe UI Variable", UI_FONTS,
                             callback=lambda v: self.apply_live_theme())
        lay.addWidget(tc)

        sc = Card("Scaling & Size", "Zoom the whole interface in or out.")
        self._slider_row(sc.body(), "UI Scale", "ui_scale", 0.8, 1.5, 1.0, is_int=False,
                         callback=lambda v: self._apply_live_theme_debounced())
        lay.addWidget(sc)

        cc = Card("Custom Theme Builder", "Active when Theme Mode = Custom. Pick any color you want \u2014 changes apply instantly.")
        on_col = lambda _=None: self.apply_live_theme()
        self._custom_color_row(cc.body(), "App Background",   "BG",       "#1c1c1c", on_col)
        self._custom_color_row(cc.body(), "Card Surface",     "CARD",     "#272727", on_col)
        self._custom_color_row(cc.body(), "Card Hover",       "CARD3",    "#323232", on_col)
        self._custom_color_row(cc.body(), "Sidebar",          "SIDEBAR",  "#1f1f1f", on_col)
        self._custom_color_row(cc.body(), "Accent (Primary)", "ACC2",     "#0078d4", on_col)
        self._custom_color_row(cc.body(), "Accent (Light)",   "ACC",      "#60cdff", on_col)
        self._custom_color_row(cc.body(), "Text",             "TEXT",     "#ffffff", on_col)
        self._custom_color_row(cc.body(), "Sub Text",         "SUB",      "#b0b0b0", on_col)
        self._custom_color_row(cc.body(), "Border",           "BOR",      "#3a3a3a", on_col)
        lay.addWidget(cc)

        lay.addStretch(1)
        self._add_page("uiconfig", page)

    def _custom_color_row(self, parent_layout, label, palette_key, default_hex, on_apply=None):
        import utils.theme as _t
        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background: {_t.CARD2}; border: 1px solid {_t.BOR_SFT}; border-radius: 8px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        h = QHBoxLayout(row); h.setContentsMargins(12, 8, 12, 8); h.setSpacing(12)
        lbl = QLabel(label)
        lbl.setMinimumWidth(200); lbl.setMaximumWidth(260)
        lbl.setStyleSheet(f"color: {_t.TEXT}; font-weight: 500; background: transparent; border: none;")
        h.addWidget(lbl)

        cur_hex = S.get("custom_theme", {}).get(palette_key, default_hex)

        sw = QFrame(); sw.setFixedSize(32, 32)
        sw.setStyleSheet(f"background: {cur_hex}; border: 2px solid {_t.BOR}; border-radius: 8px;")
        h.addWidget(sw)

        hex_lbl = QLabel(cur_hex.upper())
        hex_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hex_lbl.setMinimumWidth(92)
        hex_lbl.setStyleSheet(
            f"color: {_t.TEXT}; background: {_t.CARD3}; border: 1px solid {_t.BOR}; border-radius: 6px; "
            f"padding: 5px 10px; font-family: 'Cascadia Mono', Consolas, monospace; font-weight: 600;"
        )
        h.addWidget(hex_lbl)

        btn = QPushButton("Pick"); btn.setObjectName("softAction"); btn.setFixedWidth(110); btn.setFixedHeight(32)
        def pick():
            start = S.get("custom_theme", {}).get(palette_key, default_hex)
            col = ModernColorPicker.pick(self, start, f"Choose {label}")
            if col is not None:
                hexv = col.name().upper()
                sw.setStyleSheet(f"background: {hexv}; border: 2px solid {_t.BOR}; border-radius: 8px;")
                hex_lbl.setText(hexv)
                ct = dict(S.get("custom_theme", {}))
                ct[palette_key] = hexv
                update_s("custom_theme", ct)
                if on_apply:
                    on_apply(hexv)
        btn.clicked.connect(pick)
        h.addWidget(btn); h.addStretch(1)
        parent_layout.addWidget(row)

    def _apply_live_theme_debounced(self):
        """Debounce rapid slider changes before re-theming."""
        if not hasattr(self, "_theme_timer") or self._theme_timer is None:
            self._theme_timer = QTimer(self)
            self._theme_timer.setSingleShot(True)
            self._theme_timer.setInterval(400)
            self._theme_timer.timeout.connect(self.apply_live_theme)
        self._theme_timer.stop()
        self._theme_timer.start()

    # ---------------- BATCH EVENTS ----------------
    def _on_show_in_folder(self):
        """Open the clips output folder in File Explorer."""
        folder_path = os.path.abspath(S["out_folder"])
        os.makedirs(folder_path, exist_ok=True)
        try:
            import subprocess
            subprocess.Popen(["explorer", folder_path], creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            log(f"Failed to open folder: {e}", "error")
            QMessageBox.warning(self, "Error", f"Could not open folder:\n{folder_path}")

    def _on_start_cancel(self):
        if self._running:
            CANCEL.set()
            self._start_btn.setText("Cancelling\u2026")
            self._start_btn.setEnabled(False)
            return

        if not S["gemini_key"] and not S["test_mode"]:
            QMessageBox.warning(self, "API Key Missing", "No Gemini API key entered!")
            self.show_page("config")
            return

        self._running = True
        CANCEL.clear()
        self._batch_start_time = time.time()

        self._start_btn.setText("Cancel Batch")
        self._start_btn.setObjectName("danger")
        self._start_btn.style().unpolish(self._start_btn); self._start_btn.style().polish(self._start_btn)
        self._start_btn.setEnabled(True)
        self._hero_sub.setText("Batch in progress - live updates below.")
        self.show_page("engine")

        self._tlog("", "dim")
        self._tlog("  > Batch started.", "success")

        # Start elapsed time timer
        if not hasattr(self, '_elapsed_timer') or self._elapsed_timer is None:
            self._elapsed_timer = QTimer(self)
            self._elapsed_timer.timeout.connect(self._update_elapsed_time)
        self._elapsed_timer.start(1000)  # Update every second

        self._batch_thread = BatchThread()
        self._batch_thread.progress.connect(self._on_progress)
        self._batch_thread.macro.connect(self._on_macro)
        self._batch_thread.finished_ok.connect(self._on_batch_done)
        self._batch_thread.error_signal.connect(lambda msg: self._tlog(msg, "error"))
        self._batch_thread.start()

    def _update_elapsed_time(self):
        """Update the elapsed time label during batch processing."""
        if not self._running or not hasattr(self, '_batch_start_time'):
            return
        elapsed = time.time() - self._batch_start_time
        elapsed_str = self._format_duration(elapsed)
        self._elapsed_lbl.setText(f"Elapsed: {elapsed_str}")

    def _format_duration(self, seconds):
        """Format seconds as HH:MM:SS."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _on_progress(self, cur, total, label):
        pct = cur / max(total, 1)
        self._micro_lbl.setText(label)
        self._micro_pct.setText(f"{int(pct*100)}%")
        self._micro_bar.animate_to(pct)
        self._update_eta(cur, total)

    def _on_macro(self, cur, total, movie_name):
        pct = (cur - 1) / max(total, 1)
        self._macro_lbl.setText(f"Overall \u00B7 Movie {cur} / {total}  -  {movie_name}")
        self._macro_pct.setText(f"{int(pct*100)}%")
        self._macro_bar.animate_to(pct)
        self._update_eta(cur, total, is_macro=True)

    def _update_eta(self, cur, total, is_macro=False):
        """Calculate and display ETA based on progress and elapsed time."""
        if not hasattr(self, '_batch_start_time') or cur <= 0 or total <= 0:
            return
        elapsed = time.time() - self._batch_start_time
        # Estimate total time: elapsed / (cur/total) = elapsed * total / cur
        estimated_total = elapsed * total / cur
        remaining = estimated_total - elapsed
        if remaining < 0:
            remaining = 0
        eta_str = self._format_duration(remaining)
        self._eta_lbl.setText(f"ETA: {eta_str}")

    def _on_batch_done(self, ok):
        self._running = False
        # Stop the elapsed timer
        if hasattr(self, '_elapsed_timer') and self._elapsed_timer:
            self._elapsed_timer.stop()
        # Reset time displays
        self._elapsed_lbl.setText("Elapsed: 00:00:00")
        self._eta_lbl.setText("ETA: --:--:--")

        self._start_btn.setText("Start Batch")
        self._start_btn.setObjectName("primary")
        self._start_btn.style().unpolish(self._start_btn); self._start_btn.style().polish(self._start_btn)
        self._start_btn.setEnabled(True)
        self._hero_sub.setText("Batch complete." if ok else "Batch stopped.")
        self._macro_bar.animate_to(1.0 if ok else 0.0)
        self._macro_pct.setText("100%" if ok else "0%")
        self._micro_bar.animate_to(1.0 if ok else 0.0)
        self._micro_pct.setText("100%" if ok else "0%")
        self._update_charts()
        self._sb_movies_lbl.setText(str(S['total_movies']))
        self._sb_shorts_lbl.setText(str(S['total_shorts']))

        # Auto-upload if enabled and batch completed successfully
        if ok and S.get("auto_upload", False):
            user_data_dir = os.path.join(os.path.dirname(__file__), "..", "user_data")
            token_path = os.path.join(user_data_dir, "token.json")
            if os.path.exists(token_path):
                log("Auto-upload enabled - starting upload session...", "info")
                self._yt_write("  Auto-upload starting after batch completion...", clear=False)
                self._on_upload_all()
            else:
                log(f"Auto-upload enabled but not authenticated ({token_path} missing) - skipping upload", "warn")

    # ---------------- TOAST ----------------
    def show_save_popup_safe(self):
        # Called from any thread via update_s; must marshal to GUI thread
        print(f"show_save_popup_safe called")
        QTimer.singleShot(0, self._toast.slide_in)

    def _on_toast_save(self):
        print("Toast save clicked - saving settings")
        save_settings()
        self._dirty_keys.clear()
        self._toast.slide_out()

    def _on_toast_discard(self):
        """Revert only the keys the user has touched since entering the current tab, then rebuild that tab."""
        self._revert_dirty()
        self._toast.slide_out()
        if self._current_page:
            self._rebuild_page(self._current_page)

    def _revert_dirty(self):
        """Restore dirty keys to the per-tab snapshot taken when the tab was entered."""
        for k in list(self._dirty_keys):
            if k in self._page_snapshot:
                S[k] = copy.deepcopy(self._page_snapshot[k])
        self._dirty_keys.clear()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "_toast") and self._toast._shown:
            self._toast.slide_in()

    # ---------------- POLLING ----------------
    def _poll_log(self):
        try:
            while True:
                msg, color = LOG_Q.get_nowait()
                self._tlog(msg, color)
        except queue.Empty:
            pass

    def _poll_yt_log(self):
        try:
            while True:
                msg = YT_LOG_Q.get_nowait()
                self._yt_write(msg, clear=False)
        except queue.Empty:
            pass

    def _poll_stats(self):
        latest = None
        try:
            while True:
                latest = STATS_Q.get_nowait()
        except queue.Empty:
            pass
        if not latest:
            return
        cpu, ram, gp, gt, gm, dsk = latest
        self.hist_cpu.pop(0); self.hist_cpu.append(cpu)
        self.hist_gpu.pop(0); self.hist_gpu.append(gp)
        self.hist_ram.pop(0); self.hist_ram.append(ram)
        self.hist_dsk.pop(0); self.hist_dsk.append(dsk)
        xs = list(range(60))
        def upd(ln, data, val):
            ln.set_data(xs, data)
            val.set_text(f"{data[-1]:.0f}%")
        upd(self.line_cpu, self.hist_cpu, self.val_cpu)
        upd(self.line_gpu, self.hist_gpu, self.val_gpu)
        upd(self.line_ram, self.hist_ram, self.val_ram)
        upd(self.line_dsk, self.hist_dsk, self.val_dsk)
        try:
            self.sys_canvas.draw_idle()
        except Exception:
            pass

    # ---------------- MUSIC CONTROLS ----------------
    def _toggle_music(self):
        import utils.theme as _t
        if not hasattr(self, '_music_player') or not self._music_player:
            return
        self._music_muted = not self._music_muted
        if self._music_muted:
            self._fade_music_out()
            self._music_btn.setStyleSheet(f"color: {_t.SUB_DK};")
            self._music_btn.setText("\u266A")  # Muted note
        else:
            self._fade_music_in()
            self._music_btn.setStyleSheet(f"color: {_t.ACC};")
            self._music_btn.setText("\u266B")  # Full note

    def _fade_music_out(self):
        self._fade_anim = QPropertyAnimation(self._audio_output, b"volume", self)
        self._fade_anim.setDuration(800)
        self._fade_anim.setStartValue(self._audio_output.volume())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._music_player.pause)
        self._fade_anim.start()

    def _fade_music_in(self):
        self._music_player.play()
        self._fade_anim = QPropertyAnimation(self._audio_output, b"volume", self)
        self._fade_anim.setDuration(800)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(0.5)
        self._fade_anim.start()

    def _on_music_status_changed(self, status):
        from PyQt6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            # Soft fade out, then restart with fade in
            self._fade_anim = QPropertyAnimation(self._audio_output, b"volume", self)
            self._fade_anim.setDuration(500)
            self._fade_anim.setStartValue(self._audio_output.volume())
            self._fade_anim.setEndValue(0.0)
            def restart_and_fade():
                self._music_player.setPosition(0)
                self._music_player.play()
                self._fade_anim = QPropertyAnimation(self._audio_output, b"volume", self)
                self._fade_anim.setDuration(1000)
                self._fade_anim.setStartValue(0.0)
                self._fade_anim.setEndValue(0.5)
                self._fade_anim.start()
            self._fade_anim.finished.connect(restart_and_fade)
            self._fade_anim.start()

    # ---------------- ANALYTICS UPDATE METHODS ----------------
    def _update_cumulative_charts(self, cumulative_views, cumulative_subs):
        """Update the cumulative growth charts."""
        def draw_growth(fig, ax, canvas, rows, color, label, empty_text):
            rows = self._limit_analytics_series(rows)
            ax.clear()
            if not rows:
                self._draw_empty_chart(fig, ax, canvas, empty_text)
                return
            dates = [self._day_label(d.get("date", ""), compact=True) for d in rows]
            values = [self._to_float(d.get("value", 0)) for d in rows]
            xs = list(range(len(values)))
            self._setup_analytics_axes(fig, ax, label)
            ax.plot(xs, values, color=color, linewidth=2.6, solid_capstyle="round")
            ax.fill_between(xs, values, min(values) if values else 0, alpha=0.18, color=color)
            import utils.theme as _t
            ax.scatter(xs[-1], values[-1], s=34, color=color, edgecolor=_t.CARD, linewidth=1.2, zorder=5)
            ax.annotate(self._format_compact_number(values[-1]), xy=(xs[-1], values[-1]), xytext=(-8, 8),
                        textcoords="offset points", ha="right", color=color, fontsize=8, fontweight="bold")
            step = max(1, len(xs) // 5)
            ticks = xs[::step]
            if xs[-1] not in ticks:
                ticks.append(xs[-1])
            ax.set_xticks(ticks)
            ax.set_xticklabels([dates[i] for i in ticks], color=SUB_DK, fontsize=8)
            if len(xs) == 1:
                ax.set_xlim(-0.5, 0.5)
            else:
                ax.set_xlim(xs[0], xs[-1])
            self._finish_analytics_chart(fig, canvas)

        draw_growth(self._cumulative_views_fig, self._cumulative_views_ax, self._cumulative_views_canvas,
                    cumulative_views, ACC, "Views", "No growth data yet")
        draw_growth(self._cumulative_subs_fig, self._cumulative_subs_ax, self._cumulative_subs_canvas,
                    cumulative_subs, GREEN, "Subscribers", "No subscriber data yet")

    def _update_daily_charts(self, daily_views, daily_subs, daily_watch):
        """Update the daily activity charts."""
        self._daily_views_ax.clear()
        rows = self._limit_analytics_series(daily_views)
        if rows:
            labels = [self._day_label(d.get("day", ""), compact=self._current_analytics_limit() != 7) for d in rows]
            values = [self._to_float(d.get("views", 0)) for d in rows]
            xs = list(range(len(values)))
            self._setup_analytics_axes(self._daily_views_fig, self._daily_views_ax, "Views")
            self._daily_views_ax.bar(xs, values, color=ACC, alpha=0.9, width=0.68)
            step = max(1, len(xs) // 7)
            self._daily_views_ax.set_xticks(xs[::step])
            self._daily_views_ax.set_xticklabels(labels[::step], color=SUB_DK, fontsize=8, rotation=0)
            self._finish_analytics_chart(self._daily_views_fig, self._daily_views_canvas)
        else:
            self._draw_empty_chart(self._daily_views_fig, self._daily_views_ax, self._daily_views_canvas, "No views data")

        self._daily_subs_ax.clear()
        rows = self._limit_analytics_series(daily_subs)
        if rows:
            labels = [self._day_label(d.get("day", ""), compact=self._current_analytics_limit() != 7) for d in rows]
            values = [self._to_float(d.get("subscribersGained", 0)) - self._to_float(d.get("subscribersLost", 0)) for d in rows]
            xs = list(range(len(values)))
            colors = [GREEN if v >= 0 else RED for v in values]
            self._setup_analytics_axes(self._daily_subs_fig, self._daily_subs_ax, "Net Subs")
            self._daily_subs_ax.axhline(0, color=BOR, linewidth=1)
            self._daily_subs_ax.bar(xs, values, color=colors, alpha=0.9, width=0.68)
            step = max(1, len(xs) // 7)
            self._daily_subs_ax.set_xticks(xs[::step])
            self._daily_subs_ax.set_xticklabels(labels[::step], color=SUB_DK, fontsize=8, rotation=0)
            self._finish_analytics_chart(self._daily_subs_fig, self._daily_subs_canvas)
        else:
            self._draw_empty_chart(self._daily_subs_fig, self._daily_subs_ax, self._daily_subs_canvas, "No subscriber data")

        self._daily_watch_ax.clear()
        rows = self._limit_analytics_series(daily_watch)
        if rows:
            labels = [self._day_label(d.get("day", ""), compact=self._current_analytics_limit() != 7) for d in rows]
            values = [self._to_float(d.get("estimatedMinutesWatched", 0)) / 60 for d in rows]
            xs = list(range(len(values)))
            self._setup_analytics_axes(self._daily_watch_fig, self._daily_watch_ax, "Hours")
            self._daily_watch_ax.plot(xs, values, color=CYAN, linewidth=2.4, marker="o", markersize=3.5)
            self._daily_watch_ax.fill_between(xs, values, 0, alpha=0.16, color=CYAN)
            step = max(1, len(xs) // 7)
            self._daily_watch_ax.set_xticks(xs[::step])
            self._daily_watch_ax.set_xticklabels(labels[::step], color=SUB_DK, fontsize=8, rotation=0)
            self._finish_analytics_chart(self._daily_watch_fig, self._daily_watch_canvas)
        else:
            self._draw_empty_chart(self._daily_watch_fig, self._daily_watch_ax, self._daily_watch_canvas, "No watch-time data")

    def _update_retention_chart(self, retention_data):
        """Update the retention pie chart."""
        self._retention_ax.clear()
        if not retention_data:
            self._draw_empty_chart(self._retention_fig, self._retention_ax, self._retention_canvas, "No retention data")
            return
        stayed = max(0, min(100, self._to_float(retention_data.get("stayed", 0))))
        swiped = max(0, min(100, self._to_float(retention_data.get("swiped", 100 - stayed))))
        total = stayed + swiped
        if total <= 0:
            self._draw_empty_chart(self._retention_fig, self._retention_ax, self._retention_canvas, "No retention data")
            return
        stayed = stayed / total * 100
        swiped = 100 - stayed
        self._retention_fig.patch.set_facecolor(CARD)
        self._retention_ax.set_facecolor(CARD)
        wedges, _, autos = self._retention_ax.pie(
            [stayed, swiped],
            labels=None,
            colors=[GREEN, RED],
            autopct=lambda pct: f"{pct:.0f}%" if pct >= 5 else "",
            startangle=90,
            counterclock=False,
            pctdistance=0.67,
            textprops={"color": TEXT, "fontsize": 9, "fontweight": "bold"},
            wedgeprops={"linewidth": 2, "edgecolor": CARD}
        )
        self._retention_ax.legend(
            wedges,
            [f"Stayed {stayed:.0f}%", f"Swiped {swiped:.0f}%"],
            loc="lower center",
            bbox_to_anchor=(0.5, -0.08),
            ncol=2,
            frameon=False,
            fontsize=8,
            labelcolor=SUB
        )
        self._retention_ax.axis("equal")
        for at in autos:
            at.set_color(TEXT)
        for sp in self._retention_ax.spines.values():
            sp.set_visible(False)
        self._finish_analytics_chart(self._retention_fig, self._retention_canvas)

    def _update_device_chart(self, device_data):
        """Update the device type doughnut chart."""
        self._device_ax.clear()
        items = [(str(k).replace("_", " ").title(), self._to_float(v)) for k, v in (device_data or {}).items() if self._to_float(v) > 0]
        items = sorted(items, key=lambda x: x[1], reverse=True)[:5]
        if not items:
            self._draw_empty_chart(self._device_fig, self._device_ax, self._device_canvas, "No device data")
            return
        labels = [x[0] for x in items]
        values = [x[1] for x in items]
        colors = [ACC, GREEN, YEL, CYAN, RED][:len(values)]
        self._device_fig.patch.set_facecolor(CARD)
        self._device_ax.set_facecolor(CARD)
        wedges, _, autos = self._device_ax.pie(
            values, labels=None, colors=colors, autopct=lambda pct: f"{pct:.0f}%" if pct >= 7 else "",
            startangle=90, pctdistance=0.78, textprops={"color": TEXT, "fontsize": 8, "fontweight": "bold"},
            wedgeprops={"linewidth": 2, "edgecolor": CARD, "width": 0.42}
        )
        self._device_ax.text(0, 0, self._format_compact_number(sum(values)), ha="center", va="center",
                             color=TEXT, fontsize=11, fontweight="bold")
        self._device_ax.legend(wedges, labels, loc="lower center", bbox_to_anchor=(0.5, -0.12),
                               ncol=2, frameon=False, fontsize=7, labelcolor=SUB)
        self._device_ax.axis("equal")
        for at in autos:
            at.set_color(TEXT)
        self._finish_analytics_chart(self._device_fig, self._device_canvas)

    def _update_demographics_chart(self, demo_data):
        """Update the age and gender demographics chart."""
        self._demo_ax.clear()
        if not demo_data or not (demo_data.get("age") or demo_data.get("gender")):
            self._draw_empty_chart(self._demo_fig, self._demo_ax, self._demo_canvas, "No demographic data")
            return
        age_data = demo_data.get("age", {})
        gender_data = demo_data.get("gender", {})
        age_map = [
            ("13-17", "AGE_13_17"), ("18-24", "AGE_18_24"), ("25-34", "AGE_25_34"),
            ("35-44", "AGE_35_44"), ("45-54", "AGE_45_54"), ("55-64", "AGE_55_64"), ("65+", "AGE_65_")
        ]
        labels = [label for label, key in age_map if self._to_float(age_data.get(key, 0)) > 0]
        values = [self._to_float(age_data.get(key, 0)) for label, key in age_map if self._to_float(age_data.get(key, 0)) > 0]
        if not values and gender_data:
            labels = [str(k).replace("_", " ").title() for k in gender_data.keys()]
            values = [self._to_float(v) for v in gender_data.values()]
        if not values:
            self._draw_empty_chart(self._demo_fig, self._demo_ax, self._demo_canvas, "No demographic data")
            return
        xs = list(range(len(values)))
        self._setup_analytics_axes(self._demo_fig, self._demo_ax, "Viewer %")
        bars = self._demo_ax.bar(xs, values, color=[ACC, BLUE, CYAN, GREEN, YEL, RED, ACC2][:len(values)], alpha=0.9, width=0.62)
        self._demo_ax.set_xticks(xs)
        self._demo_ax.set_xticklabels(labels, color=SUB_DK, fontsize=7, rotation=0)
        self._demo_ax.set_ylim(0, max(values) * 1.28 if max(values) > 0 else 10)
        for bar, value in zip(bars, values):
            self._demo_ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{value:.0f}%",
                               ha="center", va="bottom", color=TEXT, fontsize=7)
        if gender_data:
            gender_text = " · ".join(f"{str(k).replace('_', ' ').title()} {self._to_float(v):.0f}%" for k, v in gender_data.items())
            self._demo_ax.text(0.98, 0.95, gender_text, transform=self._demo_ax.transAxes, ha="right", va="top",
                               color=SUB, fontsize=7)
        self._finish_analytics_chart(self._demo_fig, self._demo_canvas)

    def _update_top_shorts_list(self, top_shorts):
        """Update the top shorts list."""
        self._clear_layout(self._top_shorts_list)
        if not top_shorts:
            self._top_shorts_list.addWidget(self._empty_list_row("No Shorts data available yet. Refresh analytics after uploading Shorts."))
            return
        max_views = max(self._to_float(s.get("views", 0)) for s in top_shorts[:5]) or 1
        for i, short in enumerate(top_shorts[:5]):
            row = QFrame()
            row.setObjectName("analyticsMiniRow")
            import utils.theme as _t
            row.setStyleSheet(
                f"QFrame#analyticsMiniRow {{ background: {_t.CARD2}; border: 1px solid {_t.BOR_SFT}; border-radius: 10px; }}"
                f"QFrame#analyticsMiniRow:hover {{ background: {_t.CARD3}; border-color: {_t.BOR}; }}"
                f"QLabel {{ background: transparent; border: none; }}"
            )
            h = QHBoxLayout(row); h.setContentsMargins(10, 8, 10, 8); h.setSpacing(10)
            rank = QLabel(f"#{i+1}")
            rank.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rank.setFixedSize(34, 34)
            import utils.theme as _t
            rank.setStyleSheet(f"color: {_t.ACC}; background: {_t.CARD}; border: 1px solid {_t.BOR}; border-radius: 17px; font-weight: 800;")
            h.addWidget(rank)
            title_col = QVBoxLayout(); title_col.setSpacing(4)
            title_text = short.get("title", "Untitled")
            title = QLabel(title_text)
            title.setStyleSheet(f"color: {_t.TEXT}; font-size: 12px; font-weight: 600;")
            title.setToolTip(title_text)
            title.setWordWrap(True)
            title_col.addWidget(title)
            bar_wrap = QFrame()
            bar_wrap.setFixedHeight(5)
            bar_wrap.setStyleSheet(f"background: {_t.CARD}; border-radius: 2px;")
            bar = QFrame(bar_wrap)
            bar.setGeometry(0, 0, max(4, int(120 * self._to_float(short.get("views", 0)) / max_views)), 5)
            bar.setStyleSheet(f"background: {_t.ACC}; border-radius: 2px;")
            title_col.addWidget(bar_wrap)
            h.addLayout(title_col, 1)
            views = QLabel(f"{self._to_int(short.get('views', 0)):,}")
            views.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            views.setStyleSheet(f"color: {_t.ACC}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; font-weight: 700;")
            h.addWidget(views)
            self._top_shorts_list.addWidget(row)

    def _update_traffic_sources_list(self, traffic_data):
        """Update the traffic sources list."""
        self._clear_layout(self._traffic_sources_list)
        if not traffic_data:
            self._traffic_sources_list.addWidget(self._empty_list_row("No traffic-source data available for the selected period."))
            return
        source_names = {
            "ANNOTATION": "Annotations",
            "CAMPAIGN_CARD": "Cards",
            "END_SCREEN": "End Screens",
            "HASHTAG": "Hashtags",
            "NOTIFICATION": "Notifications",
            "PLAYLIST": "Playlists",
            "RELATED_VIDEO": "Related Videos",
            "SEARCH": "YouTube Search",
            "SUBSCRIBER": "Subscriptions",
            "VIDEO_REMIXES": "Remixes",
            "YOUTUBE_CHANNEL": "Channel Pages",
            "YOUTUBE_HOME": "Home Page",
            "YOUTUBE_SHORTS": "Shorts Feed",
            "EXT_URL": "External",
            "NO_LINK_EMBEDDED": "Embedded Player",
            "NO_LINK_OTHER": "Direct/Other"
        }
        clean_items = [(source, self._to_float(views)) for source, views in traffic_data.items() if self._to_float(views) > 0]
        total = sum(v for _, v in clean_items)
        sorted_sources = sorted(clean_items, key=lambda x: x[1], reverse=True)[:6]
        if total <= 0 or not sorted_sources:
            self._traffic_sources_list.addWidget(self._empty_list_row("No traffic-source data available for the selected period."))
            return
        for source, views in sorted_sources:
            row = QFrame()
            row.setObjectName("trafficRow")
            row.setStyleSheet(
                f"QFrame#trafficRow {{ background: {_t.CARD2}; border: 1px solid {_t.BOR_SFT}; border-radius: 10px; }}"
                f"QLabel {{ background: transparent; border: none; }}"
            )
            v = QVBoxLayout(row); v.setContentsMargins(10, 8, 10, 8); v.setSpacing(6)
            h = QHBoxLayout(); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
            name = QLabel(source_names.get(source, source))
            name.setStyleSheet(f"color: {_t.TEXT}; font-size: 11px; font-weight: 600;")
            h.addWidget(name, 1)
            pct = views / total * 100
            value = QLabel(f"{pct:.0f}% · {self._format_compact_number(views)}")
            value.setStyleSheet(f"color: {_t.ACC}; font-size: 10px; font-weight: 700;")
            h.addWidget(value)
            v.addLayout(h)
            import utils.theme as _t
            bar_wrap = QFrame()
            bar_wrap.setFixedHeight(5)
            bar_wrap.setStyleSheet(f"background: {_t.CARD}; border-radius: 2px;")
            bar = QFrame(bar_wrap)
            bar.setGeometry(0, 0, max(4, int(180 * pct / 100)), 5)
            bar.setStyleSheet(f"background: {_t.ACC}; border-radius: 2px;")
            v.addWidget(bar_wrap)
            self._traffic_sources_list.addWidget(row)

    def _fade_in_widget(self, widget):
        """Fade in a widget using opacity animation."""
        eff = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(eff)
        eff.setOpacity(0)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(200)
        anim.setStartValue(0)
        anim.setEndValue(1)
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _fade_out_widget(self, widget, callback=None):
        """Fade out a widget using opacity animation."""
        eff = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(200)
        anim.setStartValue(1)
        anim.setEndValue(0)
        if callback:
            anim.finished.connect(lambda: (widget.setGraphicsEffect(None), callback()))
        else:
            anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_toggle_graphs(self):
        """Toggle the graphs section to cover full tab with animation."""
        checked = self._graphs_toggle.isChecked()
        self._is_graphs_expanded = checked
        if checked:
            self._graphs_toggle.setText("Hide Insights")
            self._graphs_container.setVisible(True)
            if hasattr(self, '_videos_card'):
                self._videos_card.setVisible(False)
            self._graphs_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            # Fade in animation
            self._fade_in_widget(self._graphs_container)
        else:
            self._graphs_toggle.setText("Show Insights")
            if hasattr(self, '_videos_card'):
                self._videos_card.setVisible(True)
            # Fade out animation before hiding
            self._fade_out_widget(self._graphs_container, lambda: self._graphs_container.setVisible(False))
        if self._analytics_data and checked:
            self._update_analytics_ui()

    def _on_refresh_analytics(self):
        """Start the analytics refresh process."""
        if self._analytics_thread and self._analytics_thread.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("Loading...")
        self._loading_lbl.setText("\u23F3 Fetching data from YouTube API...")
        self._analytics_thread = AnalyticsFetchThread(self)
        self._analytics_thread.progress.connect(self._on_analytics_progress)
        self._analytics_thread.finished_ok.connect(self._on_analytics_complete)
        self._analytics_thread.error.connect(self._on_analytics_error)
        self._analytics_thread.start()

    def _on_analytics_progress(self, msg):
        """Update loading status."""
        self._loading_lbl.setText(f"\u23F3 {msg}")

    def _on_analytics_complete(self, data, used_quota, total_quota):
        """Handle successful analytics fetch."""
        self._analytics_data = data
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("\u21bb  Refresh Analytics")
        self._loading_lbl.setText(f"\u2713 Last updated: {datetime.now().strftime('%H:%M:%S')} | Quota: {used_quota:,}/{total_quota:,}")
        self._update_analytics_ui()

    def _on_analytics_error(self, error_msg):
        """Handle analytics fetch error."""
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("\u21bb  Refresh Analytics")
        self._loading_lbl.setText(f"\u2717 Error: {error_msg[:60]}")
        ModernDialog.ask(
            self, "Analytics Error",
            f"Failed to fetch analytics data:\n{error_msg}\n\nPlease ensure you're authenticated via the YouTube Panel.",
            [("OK", "ok", True)],
            icon_char="\u26A0",
            icon_color=RED
        )

    def _update_analytics_ui(self):
        """Update the UI with current analytics data."""
        import utils.theme as _t
        if not self._analytics_data:
            self._show_empty_analytics_state()
            return
        
        data = self._analytics_data
        channel = data.get("channel", {})
        
        # Update header stats
        self._ch_name_lbl.setText(channel.get("title", "Unknown Channel"))
        self._ch_subtitle.setText(f"Channel ID: {channel.get('channel_id', 'N/A')[:20]}...")
        self._ch_subs.setText(f"{self._to_int(channel.get('subscribers', 0)):,}")
        self._ch_views.setText(f"{self._to_int(channel.get('total_views', 0)):,}")
        self._ch_videos.setText(f"{self._to_int(channel.get('total_videos', 0)):,}")
        
        # Calculate watch hours from videos data
        total_watch_hours = sum(self._to_float(v.get("watch_time_minutes", 0)) for v in data.get("videos", [])) / 60
        self._ch_watch_hours.setText(f"{int(total_watch_hours):,}")
        
        used, total = self._get_quota_status()
        self._quota_badge.setText(f"{used:,}/{total:,}")
        
        # Update all charts
        self._update_cumulative_charts(
            data.get("cumulative_views", []),
            data.get("cumulative_subs", [])
        )
        
        self._update_daily_charts(
            data.get("daily_views", []),
            data.get("daily_subs", []),
            data.get("daily_watch", [])
        )
        
        self._update_retention_chart(data.get("retention", {}))
        self._update_top_shorts_list(data.get("top_shorts", []))
        self._update_traffic_sources_list(data.get("traffic_sources", {}))
        self._update_demographics_chart(data.get("demographics", {}))
        self._update_device_chart(data.get("device_data", {}))
        self._update_video_list(data.get("videos", []))

    def _show_empty_analytics_state(self):
        """Show empty state when no data available."""
        import utils.theme as _t
        self._ch_name_lbl.setText("Not Connected")
        self._ch_subtitle.setText("Click 'Refresh Analytics' to fetch YouTube data")
        self._ch_subs.setText("0")
        self._ch_views.setText("0")
        self._ch_videos.setText("0")
        self._ch_watch_hours.setText("0")
        used, total = self._get_quota_status()
        self._quota_badge.setText(f"{used:,}/{total:,}")
        
        # Clear top shorts list
        if hasattr(self, '_top_shorts_list'):
            self._clear_layout(self._top_shorts_list)
            self._top_shorts_list.addWidget(self._empty_list_row("No data yet. Click Refresh Analytics to load your YouTube channel data."))
        if hasattr(self, '_traffic_sources_list'):
            self._clear_layout(self._traffic_sources_list)
            self._traffic_sources_list.addWidget(self._empty_list_row("Traffic sources will appear after analytics refresh."))
        chart_targets = [
            ("_cumulative_views_fig", "_cumulative_views_ax", "_cumulative_views_canvas", "No growth data"),
            ("_cumulative_subs_fig", "_cumulative_subs_ax", "_cumulative_subs_canvas", "No subscriber data"),
            ("_daily_views_fig", "_daily_views_ax", "_daily_views_canvas", "No views data"),
            ("_daily_subs_fig", "_daily_subs_ax", "_daily_subs_canvas", "No subscriber data"),
            ("_daily_watch_fig", "_daily_watch_ax", "_daily_watch_canvas", "No watch-time data"),
            ("_retention_fig", "_retention_ax", "_retention_canvas", "No retention data"),
            ("_demo_fig", "_demo_ax", "_demo_canvas", "No demographic data"),
            ("_device_fig", "_device_ax", "_device_canvas", "No device data"),
        ]
        for fig_name, ax_name, canvas_name, message in chart_targets:
            if hasattr(self, fig_name) and hasattr(self, ax_name) and hasattr(self, canvas_name):
                self._draw_empty_chart(getattr(self, fig_name), getattr(self, ax_name), getattr(self, canvas_name), message)
        if hasattr(self, '_videos_layout'):
            self._update_video_list([])

    def _update_youtube_charts(self):
        """Update the pie charts with YouTube analytics data."""
        if not self._analytics_data:
            return
        self._update_analytics_ui()

    def _update_video_list(self, videos):
        """Update the video list display."""
        self._clear_layout(self._videos_layout)
        if not videos:
            self._videos_layout.addWidget(self._empty_list_row("No videos found on your channel. Refresh analytics after uploading videos."))
            self._videos_layout.addStretch(1)
            return
        videos_sorted = sorted(videos, key=lambda x: self._to_float(x.get("views", 0)), reverse=True)
        header = self._create_video_row(None, is_header=True)
        self._videos_layout.addWidget(header)
        for video in videos_sorted[:50]:
            row = self._create_video_row(video)
            self._videos_layout.addWidget(row)
        self._videos_layout.addStretch(1)

    def _create_video_row(self, video, is_header=False):
        """Create a video row widget."""
        row = QFrame()
        row.setObjectName("analyticsVideoHeader" if is_header else "analyticsVideoRow")
        import utils.theme as _t
        if is_header:
            row.setStyleSheet(
                f"QFrame#analyticsVideoHeader {{ background: {_t.CARD2}; border: 1px solid {_t.BOR}; border-radius: 10px; }}"
                f"QLabel {{ background: transparent; border: none; }}"
            )
        else:
            row.setStyleSheet(
                f"QFrame#analyticsVideoRow {{ background: {_t.CARD}; border: 1px solid {_t.BOR_SFT}; border-radius: 12px; }}"
                f"QFrame#analyticsVideoRow:hover {{ background: {_t.CARD2}; border-color: {_t.BOR}; }}"
                f"QLabel {{ background: transparent; border: none; }}"
            )
        h = QHBoxLayout(row); h.setContentsMargins(12, 8, 12, 8); h.setSpacing(10)
        if is_header:
            headers = [("Video", 3), ("Views", 1), ("Likes", 1), ("Comments", 1), ("Subs", 1), ("Status", 1)]
            for text, stretch in headers:
                lbl = QLabel(text)
                lbl.setStyleSheet(f"color: {_t.SUB}; font-weight: 700; font-size: 11px; letter-spacing: 0.4px;")
                if text != "Video":
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                h.addWidget(lbl, stretch)
            return row
        vid_info = QHBoxLayout(); vid_info.setSpacing(10)

        # Play button that opens video link
        video_id = video.get("id", "")
        thumb = QPushButton("▶")
        thumb.setFixedSize(48, 36)
        thumb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        thumb.setStyleSheet(
            f"QPushButton {{ color: {_t.ACC}; background: {_t.CARD2}; border: 1px solid {_t.BOR_SFT}; "
            f"border-radius: 8px; font-size: 16px; font-weight: 800; }}"
            f"QPushButton:hover {{ background: {_t.ACC}; color: #ffffff; border-color: {_t.ACC}; }}"
        )
        if video_id:
            thumb.setToolTip(f"Open youtube.com/shorts/{video_id}")
            thumb.clicked.connect(lambda checked, vid=video_id: webbrowser.open(f"https://youtube.com/shorts/{vid}"))
        vid_info.addWidget(thumb)
        tcol = QVBoxLayout(); tcol.setSpacing(3)
        full_title = video.get("title", "Untitled")
        title = QLabel(full_title)
        title.setStyleSheet(f"color: {_t.TEXT}; font-size: 12px; font-weight: 650;")
        title.setToolTip(full_title)
        title.setWordWrap(True)
        tcol.addWidget(title)
        cat = video.get("category", "Uncategorized")
        cat_color = {"Funny": _t.YEL, "Badass": _t.RED, "Dramatic": _t.CYAN}.get(cat, _t.SUB_DK)
        date_text = video.get("published_at", "")[:10]
        cat_lbl = QLabel(f"● {cat}" + (f"  ·  {date_text}" if date_text else ""))
        cat_lbl.setStyleSheet(f"color: {cat_color}; font-size: 10px; font-weight: 600;")
        tcol.addWidget(cat_lbl)
        vid_info.addLayout(tcol)
        h.addLayout(vid_info, 3)
        views = QLabel(f"{self._to_int(video.get('views', 0)):,}")
        views.setAlignment(Qt.AlignmentFlag.AlignCenter)
        views.setStyleSheet(f"color: {_t.TEXT}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; font-weight: 700;")
        h.addWidget(views, 1)
        likes = self._to_int(video.get('likes', 0))
        dislikes = self._to_int(video.get('dislikes', 0))
        like_ratio = f"{likes:,}" if dislikes <= 0 else f"{likes:,}/{dislikes:,}"
        likes_lbl = QLabel(like_ratio)
        likes_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        likes_lbl.setStyleSheet(f"color: {_t.GREEN if likes > max(dislikes * 10, 0) else _t.TEXT}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px;")
        likes_lbl.setToolTip(f"Likes: {likes:,} | Dislikes: {dislikes:,}")
        h.addWidget(likes_lbl, 1)
        comments = QLabel(f"{self._to_int(video.get('comments', 0)):,}")
        comments.setAlignment(Qt.AlignmentFlag.AlignCenter)
        comments.setStyleSheet(f"color: {_t.TEXT}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px;")
        h.addWidget(comments, 1)
        subs = self._to_int(video.get('subscribers_gained', 0))
        subs_lbl = QLabel(f"+{subs:,}" if subs > 0 else "0")
        subs_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subs_lbl.setStyleSheet(f"color: {_t.GREEN if subs > 0 else _t.SUB_DK}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; font-weight: 700;")
        h.addWidget(subs_lbl, 1)
        status = video.get('copyright_status', 'Clean')
        status_icon = "\u2713" if status == "Clean" else "\u26A0" if status == "Age Restricted" else "\u2717"
        status_color = _t.GREEN if status == "Clean" else _t.YEL if status == "Age Restricted" else _t.RED
        status_lbl = QLabel(f"{status_icon} {status}")
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_lbl.setStyleSheet(
            f"color: {status_color}; background: {_t.CARD2}; border: 1px solid {_t.BOR_SFT}; "
            f"border-radius: 8px; padding: 4px 8px; font-size: 10px; font-weight: 700;"
        )
        status_lbl.setToolTip(f"Privacy: {video.get('privacy_status', 'unknown')}")
        h.addWidget(status_lbl, 1)
        return row

    # ---------------- YOUTUBE ANALYTICS BACKEND ----------------
    QUOTA_LEDGER_FILE = "quota_ledger.json"
    QUOTA_UNITS = {
        "videos.list": 1,
        "channels.list": 1,
        "youtubeAnalytics.reports.query": 1,
    }

    def _get_youtube_credentials(self):
        """Get authenticated YouTube credentials from token.json."""
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request as GRequest
            SCOPES = [
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/yt-analytics.readonly"
            ]
            creds = None
            user_data_dir = os.path.join(os.path.dirname(__file__), "..", "user_data")
            token_path = os.path.join(user_data_dir, "token.json")
            if os.path.exists(token_path):
                creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(GRequest())
                    with open(token_path, "w") as tf:
                        tf.write(creds.to_json())
                else:
                    return None
            return creds
        except Exception as e:
            print(f"Error getting credentials: {e}")
            return None

    def _load_quota_ledger(self):
        """Load the quota ledger from file."""
        if os.path.exists(self.QUOTA_LEDGER_FILE):
            try:
                with open(self.QUOTA_LEDGER_FILE, "r") as f:
                    return json.load(f)
            except:
                pass
        return {"daily_quota": 10000, "used_today": 0, "last_reset": datetime.now().isoformat(), "operations": []}

    def _save_quota_ledger(self, ledger):
        """Save the quota ledger to file."""
        try:
            with open(self.QUOTA_LEDGER_FILE, "w") as f:
                json.dump(ledger, f, indent=4)
        except Exception as e:
            print(f"Failed to save quota ledger: {e}")

    def _deduct_quota(self, operation, count=1):
        """Deduct quota units for an API operation."""
        ledger = self._load_quota_ledger()
        units = self.QUOTA_UNITS.get(operation, 1) * count
        ledger["used_today"] += units
        ledger["operations"].append({
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "units": units,
            "count": count
        })
        ledger["operations"] = ledger["operations"][-100:]
        self._save_quota_ledger(ledger)
        return ledger["used_today"], ledger["daily_quota"]

    def _get_quota_status(self):
        """Get current quota status."""
        ledger = self._load_quota_ledger()
        return ledger["used_today"], ledger["daily_quota"]

    def _categorize_video(self, video_data):
        """Categorize video into Funny, Badass, or Dramatic based on metadata."""
        title = video_data.get("title", "").lower()
        description = video_data.get("description", "").lower()
        tags = video_data.get("tags", [])
        tags_str = " ".join(tags).lower()
        combined = f"{title} {description} {tags_str}"
        clip_type = video_data.get("clip_type", "")
        if clip_type:
            if clip_type.lower() in ["funny", "comedy", "humor"]:
                return "Funny"
            elif clip_type.lower() in ["badass", "epic", "hype", "cool"]:
                return "Badass"
            elif clip_type.lower() in ["dramatic", "drama", "emotional", "sad"]:
                return "Dramatic"
        if any(kw in combined for kw in ["funny", "lol", "haha", "comedy", "joke", "laugh"]):
            return "Funny"
        elif any(kw in combined for kw in ["epic", "badass", "hype", "savage", "cool", "intense"]):
            return "Badass"
        elif any(kw in combined for kw in ["dramatic", "emotional", "sad", "tears", "deep", "powerful"]):
            return "Dramatic"
        return "Uncategorized"

    def _fetch_channel_stats(self, youtube, analytics):
        """Fetch channel statistics using YouTube Data API."""
        try:
            channels_response = youtube.channels().list(
                part="snippet,statistics,contentDetails",
                mine=True
            ).execute()
            if not channels_response.get("items"):
                return None
            channel = channels_response["items"][0]
            stats = channel["statistics"]
            return {
                "channel_id": channel["id"],
                "title": channel["snippet"].get("title", "Unknown"),
                "subscribers": int(stats.get("subscriberCount", 0)),
                "total_views": int(stats.get("viewCount", 0)),
                "total_videos": int(stats.get("videoCount", 0)),
                "thumbnail": channel["snippet"].get("thumbnails", {}).get("default", {}).get("url", "")
            }
        except Exception as e:
            print(f"Error fetching channel stats: {e}")
            return None

    def _fetch_video_list(self, youtube, channel_id, max_results=50):
        """Fetch list of videos for the channel."""
        try:
            channels_response = youtube.channels().list(
                part="contentDetails",
                id=channel_id
            ).execute()
            if not channels_response.get("items"):
                return []
            uploads_playlist_id = channels_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            videos = []
            next_page_token = None
            while len(videos) < max_results:
                playlist_params = {
                    "part": "snippet,contentDetails",
                    "playlistId": uploads_playlist_id,
                    "maxResults": min(50, max_results - len(videos))
                }
                if next_page_token:
                    playlist_params["pageToken"] = next_page_token
                playlist_response = youtube.playlistItems().list(**playlist_params).execute()
                video_ids = [item["contentDetails"]["videoId"] for item in playlist_response.get("items", [])]
                if video_ids:
                    video_response = youtube.videos().list(
                        part="snippet,statistics,contentDetails,status",
                        id=",".join(video_ids)
                    ).execute()
                    for video in video_response.get("items", []):
                        snippet = video["snippet"]
                        statistics = video["statistics"]
                        status = video["status"]
                        content_details = video["contentDetails"]
                        video_data = {
                            "id": video["id"],
                            "title": snippet.get("title", "Untitled"),
                            "description": snippet.get("description", ""),
                            "published_at": snippet.get("publishedAt", ""),
                            "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                            "tags": snippet.get("tags", []),
                            "views": int(statistics.get("viewCount", 0)),
                            "likes": int(statistics.get("likeCount", 0)),
                            "comments": int(statistics.get("commentCount", 0)),
                            "duration": content_details.get("duration", ""),
                            "privacy_status": status.get("privacyStatus", "unknown"),
                            "upload_status": status.get("uploadStatus", "unknown"),
                            "license": content_details.get("licensedContent", False),
                            "category": snippet.get("categoryId", "")
                        }
                        if "regionRestriction" in content_details:
                            video_data["copyright_status"] = "Region Restricted"
                        elif content_details.get("contentRating", {}).get("ytRating") == "ytAgeRestricted":
                            video_data["copyright_status"] = "Age Restricted"
                        else:
                            video_data["copyright_status"] = "Clean"
                        video_data["clip_type"] = self._get_clip_type_from_local(video["id"])
                        video_data["category"] = self._categorize_video(video_data)
                        videos.append(video_data)
                next_page_token = playlist_response.get("nextPageToken")
                if not next_page_token:
                    break
            return videos
        except Exception as e:
            print(f"Error fetching video list: {e}")
            return []

    def _get_clip_type_from_local(self, video_id):
        """Try to find clip type from local metadata files."""
        try:
            out_folder = S.get("out_folder", "clips_output")
            if not os.path.exists(out_folder):
                return ""
            for movie_dir in os.listdir(out_folder):
                full_dir = os.path.join(out_folder, movie_dir)
                if not os.path.isdir(full_dir):
                    continue
                for fname in os.listdir(full_dir):
                    if fname.endswith("_metadata.txt"):
                        meta_path = os.path.join(full_dir, fname)
                        try:
                            with open(meta_path, "r", encoding="utf-8") as f:
                                content = f.read()
                            if video_id in content:
                                clip_name = fname.replace("_metadata.txt", "")
                                # Check new format: SeriesName_X1_dd.mm.yy_hh.mm where X is F/B/D
                                if "_F" in clip_name:
                                    return "Funny"
                                elif "_B" in clip_name:
                                    return "Badass"
                                elif "_D" in clip_name:
                                    return "Dramatic"
                                # Fallback to old format detection
                                elif "funny" in clip_name.lower():
                                    return "Funny"
                                elif "badass" in clip_name.lower():
                                    return "Badass"
                                elif "dramatic" in clip_name.lower():
                                    return "Dramatic"
                        except:
                            pass
        except:
            pass
        return ""

    def _fetch_analytics_data(self, analytics, video_ids):
        """Fetch advanced analytics (dislikes, subscribers gained) for videos."""
        advanced_data = {}
        if not video_ids:
            return advanced_data
        try:
            batch_size = 200
            for i in range(0, len(video_ids), batch_size):
                batch = video_ids[i:i + batch_size]
                end_date = datetime.now().strftime("%Y-%m-%d")
                start_date = (datetime.now().replace(year=datetime.now().year - 1)).strftime("%Y-%m-%d")
                reports_response = analytics.reports().query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics="dislikes,subscribersGained,estimatedMinutesWatched",
                    dimensions="video",
                    filters=f"video=={','.join(batch)}"
                ).execute()
                if "rows" in reports_response:
                    headers = [h["name"] for h in reports_response.get("columnHeaders", [])]
                    for row in reports_response["rows"]:
                        video_id = row[0]
                        data = dict(zip(headers, row))
                        advanced_data[video_id] = {
                            "dislikes": int(data.get("dislikes", 0)),
                            "subscribers_gained": int(data.get("subscribersGained", 0)),
                            "watch_time_minutes": float(data.get("estimatedMinutesWatched", 0))
                        }
        except Exception as e:
            print(f"Error fetching analytics data: {e}")
        return advanced_data

    def _merge_video_data(self, videos, analytics_data):
        """Merge basic video data with advanced analytics."""
        for video in videos:
            video_id = video["id"]
            if video_id in analytics_data:
                video.update(analytics_data[video_id])
            else:
                video["dislikes"] = 0
                video["subscribers_gained"] = 0
                video["watch_time_minutes"] = 0
        return videos

    def _fetch_analytics_time_series(self, analytics, start_date, end_date, metrics, dimensions="day"):
        """Fetch time series data from YouTube Analytics API."""
        try:
            response = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics=metrics,
                dimensions=dimensions
            ).execute()
            
            if "rows" not in response:
                return []
            
            headers = [h["name"] for h in response.get("columnHeaders", [])]
            return [dict(zip(headers, row)) for row in response["rows"]]
        except Exception as e:
            print(f"Error fetching time series: {e}")
            return []

    def _fetch_demographics_data(self, analytics, start_date, end_date):
        """Fetch demographic data (age, gender) from YouTube Analytics."""
        demographics = {"age": {}, "gender": {}}
        try:
            # Age demographics
            age_response = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="viewerPercentage",
                dimensions="ageGroup"
            ).execute()
            
            if "rows" in age_response:
                for row in age_response["rows"]:
                    age_group = row[0]
                    percentage = float(row[1])
                    demographics["age"][age_group] = percentage
            
            # Gender demographics
            gender_response = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="viewerPercentage",
                dimensions="gender"
            ).execute()
            
            if "rows" in gender_response:
                for row in gender_response["rows"]:
                    gender = row[0]
                    percentage = float(row[1])
                    demographics["gender"][gender] = percentage
                    
        except Exception as e:
            print(f"Error fetching demographics: {e}")
        
        return demographics

    def _fetch_device_data(self, analytics, start_date, end_date):
        """Fetch device type data from YouTube Analytics."""
        devices = {}
        try:
            response = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="views",
                dimensions="deviceType"
            ).execute()
            
            if "rows" in response:
                for row in response["rows"]:
                    device = row[0]
                    views = int(row[1])
                    devices[device] = views
        except Exception as e:
            print(f"Error fetching device data: {e}")
        
        return devices

    def _fetch_shorts_traffic_sources(self, analytics, video_ids, days=7):
        """Fetch traffic sources specifically for Shorts."""
        traffic = {}
        try:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            
            if video_ids:
                batch_size = 200
                for i in range(0, len(video_ids), batch_size):
                    batch = video_ids[i:i + batch_size]
                    
                    response = analytics.reports().query(
                        ids="channel==MINE",
                        startDate=start_date,
                        endDate=end_date,
                        metrics="views",
                        dimensions="insightTrafficSourceType",
                        filters=f"video=={','.join(batch)}"
                    ).execute()
                    
                    if "rows" in response:
                        for row in response["rows"]:
                            source = row[0]
                            views = int(row[1])
                            traffic[source] = traffic.get(source, 0) + views
                            
        except Exception as e:
            print(f"Error fetching traffic sources: {e}")
        
        return traffic

    def _fetch_shorts_retention(self, analytics, video_ids, days=7):
        """Fetch audience retention data for Shorts (approximated via avgViewDuration)."""
        retention = {"stayed": 0, "swiped": 0}
        try:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            
            if video_ids:
                response = analytics.reports().query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics="views,estimatedMinutesWatched",
                    dimensions="video",
                    filters=f"video=={','.join(video_ids[:200])}"
                ).execute()
                
                if "rows" in response:
                    total_views = 0
                    total_minutes = 0
                    
                    for row in response["rows"]:
                        views = int(row[1])
                        minutes = float(row[2])
                        total_views += views
                        total_minutes += minutes
                    
                    # Estimate: if avg view duration > 50% of typical Short length (60s), counted as "stayed"
                    if total_views > 0:
                        avg_duration_seconds = (total_minutes * 60) / total_views
                        retention_rate = min(avg_duration_seconds / 30, 1.0)  # Normalize to 30s avg
                        retention["stayed"] = int(retention_rate * 100)
                        retention["swiped"] = 100 - retention["stayed"]
                        
        except Exception as e:
            print(f"Error fetching retention: {e}")
        
        return retention

    def _fetch_top_shorts_this_week(self, youtube, analytics):
        """Fetch top performing Shorts from the last 7 days."""
        top_shorts = []
        try:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            
            # Get channel's uploads
            channels_response = youtube.channels().list(
                part="contentDetails",
                mine=True
            ).execute()
            
            if not channels_response.get("items"):
                return top_shorts
            
            uploads_playlist_id = channels_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            
            # Get recent videos
            playlist_response = youtube.playlistItems().list(
                part="contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=50
            ).execute()
            
            video_ids = [item["contentDetails"]["videoId"] for item in playlist_response.get("items", [])]
            
            if video_ids:
                # Get video details to identify Shorts (duration < 60s)
                videos_response = youtube.videos().list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(video_ids)
                ).execute()
                
                shorts = []
                for video in videos_response.get("items", []):
                    duration = video["contentDetails"].get("duration", "")
                    # Parse PT#M#S format
                    import re
                    match = re.match(r'PT(?:(\d+)M)?(?:(\d+)S)?', duration)
                    if match:
                        minutes = int(match.group(1) or 0)
                        seconds = int(match.group(2) or 0)
                        total_seconds = minutes * 60 + seconds
                        
                        if total_seconds <= 60:  # Shorts are <= 60 seconds
                            shorts.append({
                                "id": video["id"],
                                "title": video["snippet"]["title"],
                                "thumbnail": video["snippet"].get("thumbnails", {}).get("medium", {}).get("url", ""),
                                "views": int(video["statistics"].get("viewCount", 0))
                            })
                
                # Sort by views and get top 5
                shorts.sort(key=lambda x: x["views"], reverse=True)
                top_shorts = shorts[:5]
                
        except Exception as e:
            print(f"Error fetching top shorts: {e}")
        
        return top_shorts

    def _is_short_video(self, duration_str):
        """Check if video duration indicates it's a Short (<= 60 seconds)."""
        import re
        match = re.match(r'PT(?:(\d+)M)?(?:(\d+)S)?', duration_str)
        if match:
            minutes = int(match.group(1) or 0)
            seconds = int(match.group(2) or 0)
            return (minutes * 60 + seconds) <= 60
        return False

    def _calculate_cumulative_growth(self, daily_data):
        """Calculate cumulative growth from daily data."""
        cumulative = []
        running_total = 0
        
        for day_data in daily_data:
            date = day_data.get("day", "")
            value = int(day_data.get("views", 0)) if "views" in day_data else int(day_data.get("subscribersGained", 0))
            running_total += value
            cumulative.append({"date": date, "value": running_total})
        
        return cumulative

    # ---------------- ENGINE PAGE CHARTS ----------------
    def _counter_card(self, parent_layout, value, label, color):
        f = QFrame(); f.setObjectName("card")
        v = QVBoxLayout(f); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(2)
        val = CountUpLabel(value, color=color)
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(val)
        l = QLabel(label); l.setObjectName("subSm")
        l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(l)
        parent_layout.addWidget(f, 1)
        return val

    def _update_counters(self):
        try:
            self._c_movies.set_value_animated(S.get("total_movies", 0))
            self._c_shorts.set_value_animated(S.get("total_shorts", 0))
            self._c_funny.set_value_animated(S["clip_types"].get("Funny", 0))
            self._c_drama.set_value_animated(S["clip_types"].get("Dramatic", 0))
            self._c_bad.set_value_animated(S["clip_types"].get("Badass", 0))
        except Exception:
            pass

    def _update_charts(self):
        import utils.theme as _t
        self._update_counters()
        chart_bg = _t.CARD
        text_col = _t.TEXT
        grid_col = _t.BOR

        # Update old pie chart if it exists (for Engine page compatibility)
        if hasattr(self, '_pie_ax') and self._pie_ax:
            self._pie_ax.clear()
            ct = S["clip_types"]
            total_c = sum(ct.values())
            if total_c > 0:
                lbls = [k for k, v in ct.items() if v > 0]
                sizes = [v for k, v in ct.items() if v > 0]
                color_map = {"Funny": _t.YEL, "Dramatic": _t.CYAN, "Badass": _t.RED}
                colors = [color_map.get(k, _t.ACC) for k in lbls]
                wedges, texts, autos = self._pie_ax.pie(
                    sizes, labels=lbls, colors=colors, autopct="%1.0f%%",
                    startangle=140, textprops={"color": text_col, "fontsize": 9},
                    wedgeprops={"linewidth": 2, "edgecolor": chart_bg}
                )
                for at in autos:
                    at.set_color(text_col); at.set_fontsize(9)
            else:
                self._pie_ax.text(0, 0, "No clips generated yet", ha="center", va="center", color=_t.SUB_DK, fontsize=10)
                self._pie_ax.axis("off")
            self._pie_ax.set_facecolor(chart_bg)
            self._pie_fig.patch.set_facecolor(chart_bg)
            self._pie_canvas.draw_idle()

        # Update old line chart if it exists (for Engine page compatibility)
        if hasattr(self, '_line_ax') and self._line_ax:
            self._line_ax.clear()
            rt = S["render_times"]
            if rt:
                xs = list(range(1, len(rt) + 1))
                self._line_ax.plot(xs, rt, color=_t.ACC2, linewidth=2.2, marker="o",
                                   markersize=5, markerfacecolor=_t.ACC, markeredgecolor=_t.ACC)
                self._line_ax.fill_between(xs, rt, alpha=0.18, color=_t.ACC)
                avg = sum(rt) / len(rt)
                self._line_ax.axhline(avg, color=_t.YEL, linewidth=1.2, linestyle="--", alpha=0.7)
                self._line_ax.text(xs[-1], avg + 1, f" avg {avg:.0f}s", color=_t.YEL, fontsize=8)
                self._line_ax.set_xlabel("Clip #", color=_t.SUB_DK, fontsize=8)
                self._line_ax.set_ylabel("Seconds", color=_t.SUB_DK, fontsize=8)
                self._line_ax.tick_params(colors=_t.SUB_DK, labelsize=7)
            else:
                self._line_ax.text(0.5, 0.5, "No render data yet", ha="center", va="center",
                                   transform=self._line_ax.transAxes, color=_t.SUB_DK, fontsize=10)
            self._line_ax.set_facecolor(chart_bg)
            self._line_fig.patch.set_facecolor(chart_bg)
            self._line_ax.grid(True, color=_t.SUB_DK, linestyle=":", linewidth=0.7, alpha=0.85)
            self._line_ax.set_axisbelow(True)
            for sp in self._line_ax.spines.values():
                sp.set_edgecolor(grid_col); sp.set_linewidth(0.6)
            self._line_ax.spines["top"].set_visible(False)
            self._line_ax.spines["right"].set_visible(False)
            self._line_canvas.draw_idle()

    def _do_copyright_scan(self):
        self._copy_box.setPlainText("  Scanning...")
        self._scan_thread = ScanThread()
        self._scan_thread.done.connect(self._on_scan_done)
        self._scan_thread.start()

    def _on_scan_done(self, results):
        self._copy_box.clear()
        if not results:
            self._copy_box.setPlainText("  No .mp4 files found in the output folder.\n  Generate clips first.")
            return
        lines = []
        for r in results:
            lines.append(f"  [{r['status']:>8}]  {r['file']}   ({r['size']})")
            for flag in r["flags"]:
                lines.append(f"              \u2514\u2500 {flag}")
        clean = sum(1 for r in results if "Clean" in r["status"])
        flagged = len(results) - clean
        lines.append("")
        lines.append("  " + ("\u2500" * 40))
        lines.append(f"  Total: {len(results)}   Clean: {clean}   Flagged: {flagged}")
        self._copy_box.setPlainText("\n".join(lines))

