#!/usr/bin/env python3
"""Voiceover generation for CClip using Edge-TTS."""

import os
import subprocess

from config import log


def _gen_voiceover(text, output_path):
    try:
        cmd = ["edge-tts", "--voice", "en-US-GuyNeural", "--text", text, "--write-media", output_path]
        subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        log(f"    Voiceover saved: {os.path.basename(output_path)}", "success")
    except Exception as e:
        log(f"    Voiceover error: {e}", "warn")
