#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║          CClip  OPUS  —  Configuration & Global State                  ║
# ╚══════════════════════════════════════════════════════════════════════════╝

import os
import threading

# ═══════════════════════════════════════════════════════════════════
#  FONT LISTS
# ═══════════════════════════════════════════════════════════════════
UI_FONTS = [
    "Segoe UI Variable", "Segoe UI", "Inter", "Roboto",
    "Cascadia Code", "JetBrains Mono", "Verdana", "Tahoma",
    "SF Pro Text", "Open Sans",
]
SUB_FONTS = [
    "Readfine", "Impact", "Arial Black", "Bebas Neue",
    "Oswald", "Anton", "Montserrat", "Roboto Black",
    "Segoe UI Black",
]

# ═══════════════════════════════════════════════════════════════════
#  GPU LIBRARY DETECTION
# ═══════════════════════════════════════════════════════════════════
try:
    import GPUtil
    GPU_LIB = True
except ImportError:
    GPU_LIB = False

# ═══════════════════════════════════════════════════════════════════
#  IMPORT FROM UTILS MODULES
# ═══════════════════════════════════════════════════════════════════
from utils.theme import (
    BG, SURFACE, CARD, CARD2, CARD3, SIDEBAR, ACC, ACC2, ACC_DK,
    GREEN, RED, YEL, BLUE, CYAN, TEXT, SUB, SUB_DK, BOR, BOR_SFT,
    apply_theme, build_qss
)
from utils.settings import (
    S, DEFAULT_SETTINGS, DEFAULT_PROMPT, SETTINGS_FILE,
    save_settings, update_s, APP_INSTANCE
)
from utils.logging import LOG_Q, YT_LOG_Q, STATS_Q, log, yt_log

# ═══════════════════════════════════════════════════════════════════
#  GLOBAL STATE & PERSISTENCE
# ═════════════════════════════════════════════════════════════════
CANCEL = threading.Event()

# ═══════════════════════════════════════════════════════════════════
#  COLOR HELPER
# ═══════════════════════════════════════════════════════════════════
def hex_to_ass(hex_color, alpha="00"):
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        return f"&H{alpha}FFFFFF"
    r = hex_color[0:2]
    g = hex_color[2:4]
    b = hex_color[4:6]
    return f"&H{alpha}{b}{g}{r}".upper()
