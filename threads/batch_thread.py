#!/usr/bin/env python3
"""Batch processing thread for CClip."""

from PyQt6.QtCore import QThread, pyqtSignal

from backend.pipeline import run_batch


class BatchThread(QThread):
    progress = pyqtSignal(int, int, str)
    macro = pyqtSignal(int, int, str)
    finished_ok = pyqtSignal(bool)
    error_signal = pyqtSignal(str)

    def run(self):
        try:
            run_batch(
                lambda c, t, l: self.progress.emit(c, t, l),
                lambda c, t, n: self.macro.emit(c, t, n),
                lambda ok: self.finished_ok.emit(bool(ok)),
            )
        except Exception as e:
            import traceback
            error_msg = f"Batch crash: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            self.error_signal.emit(error_msg)
            self.progress.emit(0, 100, f"ERROR: {str(e)[:50]}")
            self.finished_ok.emit(False)
