#!/usr/bin/env python3
"""Helper functions for CClip."""

import subprocess
import json

from config import log


def _audio_dur(path):
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return float(result.stdout.strip())
    except:
        return 8.0


def _get_audio_track(path):
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path]
        r = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        probe_data = json.loads(r.stdout)
        audio_streams = [s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"]
        for i, stream in enumerate(audio_streams):
            tags = stream.get("tags", {})
            lang = tags.get("language", "").lower()
            title = tags.get("title", "").lower()
            if "eng" in lang or "english" in title:
                return i
        return 0
    except:
        return 0


def _fmt_ass(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
