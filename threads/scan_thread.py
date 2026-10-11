#!/usr/bin/env python3
"""Copyright scan thread for CClip."""

from PyQt6.QtCore import QThread, pyqtSignal

from config import S
from utils.copyright import check_copyright


class ScanThread(QThread):
    done = pyqtSignal(list)

    def run(self):
        self.done.emit(check_copyright(S["out_folder"]))
