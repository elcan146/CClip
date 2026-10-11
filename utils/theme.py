#!/usr/bin/env python3
"""Theme and QSS stylesheet generation for CClip."""

# ═══════════════════════════════════════════════════════════════════
#  FLUENT THEME COLORS
# ═══════════════════════════════════════════════════════════════════
BG       = "#1c1c1c"
SURFACE  = "#202020"
CARD     = "#272727"
CARD2    = "#2d2d2d"
CARD3    = "#323232"
SIDEBAR  = "#1f1f1f"
ACC      = "#60cdff"
ACC2     = "#0078d4"
ACC_DK   = "#005a9e"
GREEN    = "#6ccb5f"
RED      = "#ff6b6b"
YEL      = "#fbbf24"
BLUE     = "#4cc2ff"
CYAN     = "#4fd1c5"
TEXT     = "#ffffff"
SUB      = "#b0b0b0"
SUB_DK   = "#8a8a8a"
BOR      = "#3a3a3a"
BOR_SFT  = "#2a2a2a"


def apply_theme(S):
    """Resolve module-level color constants based on S['ui_theme'].
    Always restores the FULL palette so switching Light -> Dark (and vice versa)
    always lands on the full set of colors.
    """
    global BG, SURFACE, CARD, CARD2, CARD3, SIDEBAR, ACC, ACC2, ACC_DK
    global TEXT, SUB, SUB_DK, BOR, BOR_SFT
    theme = S.get("ui_theme", "Dark")
    print(f"apply_theme called: theme={theme}")
    if theme == "Light":
        BG="#f3f3f3"; SURFACE="#fbfbfb"; CARD="#ffffff"
        CARD2="#f5f5f5"; CARD3="#ebebeb"; SIDEBAR="#fafafa"
        ACC="#0078d4"; ACC2="#005a9e"; ACC_DK="#004578"
        TEXT="#1a1a1a"; SUB="#5c5c5c"; SUB_DK="#8a8a8a"
        BOR="#d0d0d0"; BOR_SFT="#e5e5e5"
    elif theme == "Custom":
        # start from Dark defaults, then overlay custom colors
        BG="#1c1c1c"; SURFACE="#202020"; CARD="#272727"
        CARD2="#2d2d2d"; CARD3="#323232"; SIDEBAR="#1f1f1f"
        ACC="#60cdff"; ACC2="#0078d4"; ACC_DK="#005a9e"
        TEXT="#ffffff"; SUB="#b0b0b0"; SUB_DK="#8a8a8a"
        BOR="#3a3a3a"; BOR_SFT="#2a2a2a"
        ct = S.get("custom_theme", {})
        BG = ct.get("BG", BG); SURFACE = ct.get("SURFACE", SURFACE)
        CARD = ct.get("CARD", CARD); CARD2 = ct.get("CARD2", CARD2)
        CARD3 = ct.get("CARD3", CARD3); SIDEBAR = ct.get("SIDEBAR", SIDEBAR)
        ACC = ct.get("ACC", ACC); ACC2 = ct.get("ACC2", ACC2)
        TEXT = ct.get("TEXT", TEXT); SUB = ct.get("SUB", SUB)
        BOR = ct.get("BOR", BOR); BOR_SFT = ct.get("BOR_SFT", BOR_SFT)
    else:  # Dark (default)
        BG="#1c1c1c"; SURFACE="#202020"; CARD="#272727"
        CARD2="#2d2d2d"; CARD3="#323232"; SIDEBAR="#1f1f1f"
        ACC="#60cdff"; ACC2="#0078d4"; ACC_DK="#005a9e"
        TEXT="#ffffff"; SUB="#b0b0b0"; SUB_DK="#8a8a8a"
        BOR="#3a3a3a"; BOR_SFT="#2a2a2a"
    print(f"  -> BG={BG}, TEXT={TEXT}, CARD={CARD}")


def build_qss(S, scale=1.0):
    family = S.get("ui_font", "Segoe UI Variable")
    def s(px): return max(7, int(round(px * scale)))
    raw = f"""
* {{
    color: {TEXT};
    font-family: '{family}', 'Segoe UI', sans-serif;
    font-size: {s(13)}px;
}}
QWidget#root {{ background: {BG}; }}
QWidget#sidebar, QWidget#rightbar {{ background: {SIDEBAR}; }}
QWidget#pageBg {{ background: {BG}; }}

QFrame#card, QFrame#cardSlim {{
    background: {CARD};
    border: 1px solid {BOR_SFT};
    border-radius: 10px;
}}
QFrame#hairline {{ background: {BOR_SFT}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#vhairline {{ background: {BOR}; max-width: 1px; min-width: 1px; border: none; }}

QLabel#h1   {{ font-size: {s(22)}px; font-weight: 600; color: {TEXT}; }}
QLabel#h2   {{ font-size: {s(16)}px; font-weight: 600; color: {TEXT}; }}
QLabel#h3   {{ font-size: {s(13)}px; font-weight: 600; color: {TEXT}; }}
QLabel#sub  {{ color: {SUB}; }}
QLabel#subSm{{ color: {SUB}; font-size: {s(11)}px; }}
QLabel#tiny {{ color: {SUB_DK}; font-size: {s(11)}px; }}
QLabel#brandTitle {{ font-size: {s(22)}px; font-weight: 600; color: {TEXT}; }}
QLabel#brandSub   {{ color: {SUB}; font-size: {s(11)}px; }}
QLabel#counterValue {{ font-size: {s(42)}px; font-weight: 600; }}
QLabel#pctLabel     {{ color: {ACC}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: {s(13)}px; }}

QPushButton {{
    background: {CARD2};
    color: {TEXT};
    border: 1px solid {BOR};
    border-radius: 6px;
    padding: 7px 14px;
}}
QPushButton:hover {{ background: {CARD3}; border-color: {ACC}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: {SUB_DK}; }}

QPushButton#primary {{
    background: {ACC2};
    color: #ffffff;
    border: 1px solid {ACC2};
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {ACC_DK}; border-color: {ACC}; }}
QPushButton#primary:disabled {{ background: {CARD3}; color: {SUB_DK}; border-color: {BOR}; }}

QPushButton#softAction {{
    background: {SURFACE};
    color: {ACC2};
    border: 1px solid {ACC2};
    font-weight: 600;
}}
QPushButton#softAction:hover {{
    background: {CARD3};
    color: {TEXT};
    border-color: {ACC};
}}
QPushButton#softAction:pressed {{
    background: {ACC2};
    color: #ffffff;
    border-color: {ACC2};
}}
QPushButton#softAction:disabled {{
    background: {CARD2};
    color: {ACC2};
    border: 1px solid {ACC2};
}}

QPushButton#danger {{ background: {RED}; color: #ffffff; border: 1px solid {RED}; }}
QPushButton#danger:hover {{ background: {ACC2}; }}
QPushButton#warning {{ background: {YEL}; color: {BG}; border: 1px solid {YEL}; }}

QPushButton#nav {{
    background: transparent;
    border: none;
    color: {SUB};
    text-align: left;
    padding: 9px 14px 9px 22px;
    border-radius: 6px;
    font-size: {s(13)}px;
}}
QPushButton#nav:hover {{ background: {CARD3}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {CARD3}; color: {TEXT}; font-weight: 600; }}

QLineEdit, QPlainTextEdit, QTextEdit {{
    background: {CARD2};
    color: {TEXT};
    border: 1px solid {BOR};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {ACC2};
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {ACC}; }}
QPlainTextEdit#terminal, QPlainTextEdit#ytTerminal, QPlainTextEdit#copyBox {{
    font-family: 'Cascadia Mono', Consolas, monospace;
    font-size: {s(11)}px;
}}

QComboBox {{
    background: {CARD2};
    color: {TEXT};
    border: none;
    border-radius: 10px;
    padding: 8px 12px 8px 12px;
    min-height: 28px;
    outline: none;
}}

QComboBox:hover {{
    background: {CARD3};
}}

QComboBox:focus {{
    background: {CARD};
}}

QComboBox:on {{
    background: {CARD};
}}

/* Hide native drop-down completely */
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 0px;
    border: none;
    background: transparent;
}}

/* Hide native arrow */
QComboBox::down-arrow {{
    image: none;
    width: 0px;
    height: 0px;
    border: none;
}}

/* Dropdown list */
QComboBox QAbstractItemView {{
    background: {CARD};
    color: {TEXT};
    border: none;
    border-radius: 8px;
    padding: 6px;
    outline: none;

    selection-background-color: {ACC2};
    selection-color: #ffffff;
}}

/* Optional: softer item spacing */
QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    border-radius: 4px;
}}

QComboBox QAbstractItemView::item:selected {{
    background: {ACC2};
    color: #ffffff;
}}

QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{
    background: {CARD2}; 
    height: 4px; 
    border-radius: 2px;
    border: 1px solid {BOR_SFT};
    margin: 0 10px;
}}
QSlider::sub-page:horizontal {{
    background: {ACC};
    border-radius: 2px;
    
}}
QSlider::add-page:horizontal {{
    background: transparent; border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: #ffffff;
    border: 1px solid {BOR_SFT};
    width: 14px; height: 14px;
    margin: -6px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    border-color: {ACC};
}}
QSlider::handle:horizontal:pressed {{
    background: {ACC2};
    border-color: {ACC_DK};
}}

QCheckBox {{ spacing: 10px; color: {TEXT}; }}
QCheckBox::indicator {{
    width: 38px; height: 20px;
    border-radius: 10px;
    background: {CARD3};
    border: 1px solid {BOR};
}}
QCheckBox::indicator:checked {{ background: {ACC2}; border: 1px solid {ACC}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px 4px 0; }}
QScrollBar::handle:vertical {{ background: {CARD3}; min-height: 24px; border-radius: 4px; }}
QScrollBar::handle:vertical:hover {{ background: {BOR}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 4px 2px 4px; }}
QScrollBar::handle:horizontal {{ background: {CARD3}; min-width: 24px; border-radius: 4px; }}
QScrollBar::handle:horizontal:hover {{ background: {BOR}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QToolTip {{
    background: {CARD}; color: {TEXT};
    border: 1px solid {BOR}; padding: 4px 8px; border-radius: 4px;
}}

/* ── Custom Title Bar ── */
QWidget#titleBar {{
    background: {BG};
    border-bottom: none;
}}
QLabel#tbTitle {{
    font-size: {s(12)}px;
    font-weight: 400;
    color: {SUB};
}}
"""
    return raw
