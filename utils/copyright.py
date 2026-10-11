#!/usr/bin/env python3
"""Copyright checking utilities for CClip."""

import os
import subprocess
import json

from config import YEL, GREEN, SUB_DK


def check_copyright(folder):
    results = []
    if not os.path.exists(folder):
        return results

    for root, dirs, files in os.walk(folder):
        for fname in files:
            if not fname.lower().endswith(".mp4"):
                continue

            path = os.path.join(root, fname)
            try:
                cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path]
                r = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)

                probe_data = json.loads(r.stdout)
                tags = probe_data.get("format", {}).get("tags", {})

                bad_words = ["copyright", "(c)", "all rights", "reserved", "dmca"]
                flags = []

                for k, v in tags.items():
                    val_lower = str(v).lower()
                    for word in bad_words:
                        if word in val_lower:
                            flags.append(f"{k}: {str(v)[:55]}")
                            break

                size_mb = os.path.getsize(path) / 1_048_576

                if flags:
                    status = "Flagged"
                    color = YEL
                else:
                    status = "Clean"
                    color = GREEN

                results.append({
                    "file": fname,
                    "size": f"{size_mb:.1f} MB",
                    "status": status,
                    "flags": flags,
                    "color": color
                })

            except Exception as e:
                results.append({
                    "file": fname,
                    "size": "?",
                    "flags": [],
                    "status": f"Error: {e}",
                    "color": SUB_DK
                })

    return results
