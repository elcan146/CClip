#!/usr/bin/env python3
"""Logging utilities for CClip."""

import threading
import queue

# ═══════════════════════════════════════════════════════════════════
#  GLOBAL QUEUES FOR LOGGING
# ═══════════════════════════════════════════════════════════════════
LOG_Q    = queue.Queue()
YT_LOG_Q = queue.Queue()
STATS_Q  = queue.Queue()

# ═══════════════════════════════════════════════════════════════════
#  LOGGING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════
def log(msg, color="normal"):
    LOG_Q.put((msg, color))

def yt_log(msg):
    YT_LOG_Q.put(msg)
