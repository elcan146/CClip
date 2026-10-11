#!/usr/bin/env python3
"""Transcription functions for CClip using Whisper."""

import threading
import time

from config import log
from utils.helpers import _audio_dur


def _transcribe(wav_path, progress_cb):
    try:
        import whisper
        import torch
        
        audio_dur = _audio_dur(wav_path)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        
        progress_cb(5, 100, "Loading Whisper model...")
        model = whisper.load_model("small").to(device)
        
        # Use a threading approach to update progress while transcribing
        stop_progress = threading.Event()
        start_time = time.time()
        
        def update_progress():
            # Simulate progress based on typical transcription speed
            # Whisper typically processes at 2-5x real-time depending on GPU
            estimated_process_time = audio_dur / 4.0  # Assume 4x speedup
            while not stop_progress.is_set():
                elapsed = time.time() - start_time
                if estimated_process_time > 0:
                    pct = min(95, int((elapsed / estimated_process_time) * 100))
                else:
                    pct = 50
                progress_cb(pct, 100, f"Transcribing... {pct}%  (elapsed: {int(elapsed)}s)")
                time.sleep(0.5)  # Update every 500ms
        
        # Start progress thread
        progress_thread = threading.Thread(target=update_progress, daemon=True)
        progress_thread.start()
        
        try:
            result = model.transcribe(wav_path, language="en", word_timestamps=True, 
                                       verbose=False, fp16=(device == "cuda"))
        finally:
            stop_progress.set()
            progress_thread.join(timeout=1.0)
        
        progress_cb(100, 100, "Transcription complete")
        return result
    except Exception as e:
        log(f"Whisper error: {e}", "error")
        return {"segments": []}


def _mock_transcript():
    return {
        "segments": [
            {
                "start": 120.0, "end": 130.0, "text": "This is a system test.",
                "words": [
                    {"word": "This", "start": 120.0, "end": 121.0},
                    {"word": "is", "start": 121.0, "end": 122.0},
                    {"word": "a", "start": 122.0, "end": 123.0},
                    {"word": "test.", "start": 123.0, "end": 124.5}
                ]
            }
        ]
    }
