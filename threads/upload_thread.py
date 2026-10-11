#!/usr/bin/env python3
"""YouTube upload thread for CClip."""

from PyQt6.QtCore import QThread, pyqtSignal

from backend.youtube import run_youtube_upload


class UploadThread(QThread):
    finished_ok = pyqtSignal(bool)

    def run(self):
        run_youtube_upload(lambda ok: self.finished_ok.emit(bool(ok)))
