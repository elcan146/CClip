#!/usr/bin/env python3
"""System stats thread for CClip."""

from PyQt6.QtCore import QThread

from backend.stats import _stats_worker


class StatsThread(QThread):
    def run(self):
        _stats_worker()
