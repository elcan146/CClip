#!/usr/bin/env python3
"""Standalone Azerbaijani subtitle generator for 9:16 videos.

- Transcribes audio with Whisper (language="az") and word-level timestamps.
- Builds a modern animated .ass subtitle file (karaoke pop + outline + shadow).
- Burns the subtitles into the video with ffmpeg (no other filters / no reframing).

Usage:
    python az_subtitles.py input.mp4
    python az_subtitles.py input.mp4 -o output.mp4
    python az_subtitles.py input.mp4 --model medium --keep-ass

Requirements:
    pip install openai-whisper torch
    ffmpeg + ffprobe on PATH
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Style (modern, mobile-friendly, 1080x1920)
# ---------------------------------------------------------------------------
STYLE = {
    "font_family": "Montserrat Black",   # fallback handled by libass; pick any installed bold sans
    "font_size": 96,
    "margin_v": 360,                     # distance from bottom (lower-third area, safe of UI)
    "outline_w": 6,
    "shadow_w": 3,
    "primary_hex":   "FFFFFF",  # word color
    "highlight_hex": "FFD60A",  # current spoken word (karaoke)
    "outline_hex":   "000000",
    "back_hex":      "000000",
    "max_words_per_line": 3,     # short punchy lines look modern on 9:16
    "max_chars_per_line": 22,
    "split_gap_sec": 0.45,       # split when speaker pauses longer than this
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hex_to_ass(hex_rgb: str, alpha: str = "00") -> str:
    """Convert RRGGBB to &HAABBGGRR (ASS color order)."""
    hex_rgb = hex_rgb.lstrip("#")
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    return f"&H{alpha}{b}{g}{r}".upper()


def _fmt_ass(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if res.returncode != 0:
        print(res.stdout)
        raise RuntimeError(f"command failed: {' '.join(cmd)}")


def _check_tool(name: str) -> None:
    if shutil.which(name) is None:
        sys.exit(f"ERROR: {name} not found on PATH. Install it and retry.")


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------
def transcribe(video_path: str, model_size: str = "small") -> dict:
    """Run Whisper on the video's audio, forced to Azerbaijani, with word timestamps."""
    try:
        import whisper
        import torch
    except ImportError:
        sys.exit("ERROR: install openai-whisper and torch:  pip install openai-whisper torch")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[whisper] device={device}  model={model_size}")
    model = whisper.load_model(model_size).to(device)

    print("[whisper] transcribing (language=az)...")
    result = model.transcribe(
        video_path,
        language="az",
        word_timestamps=True,
        verbose=False,
        fp16=(device == "cuda"),
    )
    return result


# ---------------------------------------------------------------------------
# ASS building
# ---------------------------------------------------------------------------
def _flatten_words(transcript: dict) -> list[dict]:
    words: list[dict] = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []) or []:
            if "start" in w and "end" in w and str(w.get("word", "")).strip():
                words.append(w)
    return words


def _group_words(words: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    cur_chars = 0

    for i, w in enumerate(words):
        word_text = str(w["word"]).strip()
        cur.append(w)
        cur_chars += len(word_text) + 1

        gap = 999.0
        if i + 1 < len(words):
            gap = float(words[i + 1]["start"]) - float(w["end"])

        ends_sentence = word_text.endswith((".", "!", "?"))
        ends_clause = word_text.endswith((",", ";", ":"))
        too_long_words = len(cur) >= STYLE["max_words_per_line"]
        too_long_chars = cur_chars >= STYLE["max_chars_per_line"]

        if (
            ends_sentence
            or gap > STYLE["split_gap_sec"]
            or too_long_words
            or too_long_chars
            or (ends_clause and len(cur) >= 2)
        ):
            groups.append(cur)
            cur = []
            cur_chars = 0

    if cur:
        groups.append(cur)
    return groups


def build_ass(transcript: dict, ass_path: str, video_w: int = 1080, video_h: int = 1920) -> str:
    c_pri = _hex_to_ass(STYLE["primary_hex"], "00")
    c_sec = _hex_to_ass(STYLE["highlight_hex"], "00")  # karaoke fill color
    c_out = _hex_to_ass(STYLE["outline_hex"], "00")
    c_bak = _hex_to_ass(STYLE["back_hex"], "80")       # soft shadow

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
ScaledBorderAndShadow: yes
WrapStyle: 2
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{STYLE['font_family']},{STYLE['font_size']},{c_pri},{c_sec},{c_out},{c_bak},-1,0,0,0,100,100,1,0,1,{STYLE['outline_w']},{STYLE['shadow_w']},2,60,60,{STYLE['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Pop-in animation + per-word karaoke fill highlight
    pop_effect = r"{\fad(80,60)\t(0,90,\fscx115\fscy115)\t(90,170,\fscx100\fscy100)}"

    words = _flatten_words(transcript)
    groups = _group_words(words)

    events: list[str] = []
    for group in groups:
        line_start = float(group[0]["start"])
        line_end = max(line_start + 0.18, float(group[-1]["end"]))

        parts: list[str] = []
        for w in group:
            raw = str(w["word"]).strip()
            # keep Azerbaijani letters (including ə, ı, ö, ü, ş, ç, ğ); strip surrounding punctuation
            cleaned = re.sub(r"^[\W_]+|[\W_]+$", "", raw, flags=re.UNICODE)
            if not cleaned:
                continue
            cs = max(1, int((float(w["end"]) - float(w["start"])) * 100))
            # \kf = fill karaoke (smooth color sweep using SecondaryColour)
            parts.append(rf"{{\kf{cs}}}{cleaned}")

        if not parts:
            continue

        text = " ".join(parts)
        events.append(
            f"Dialogue: 0,{_fmt_ass(line_start)},{_fmt_ass(line_end)},Caption,,0,0,0,,{pop_effect}{text}\n"
        )

    Path(ass_path).parent.mkdir(parents=True, exist_ok=True)
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "".join(events))
    print(f"[ass] wrote {len(events)} lines -> {ass_path}")
    return ass_path


# ---------------------------------------------------------------------------
# Burn-in (no other filters)
# ---------------------------------------------------------------------------
def _ffmpeg_subtitle_path(p: str) -> str:
    """Escape a Windows path so it survives ffmpeg's subtitles= filter."""
    abspath = os.path.abspath(p).replace("\\", "/")
    # On Windows the drive colon must be escaped: C\:/path/file.ass
    abspath = re.sub(r"^([A-Za-z]):/", r"\1\\:/", abspath)
    return abspath


def burn(video_path: str, ass_path: str, output_path: str) -> None:
    sub_arg = _ffmpeg_subtitle_path(ass_path)
    vf = f"subtitles='{sub_arg}'"
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "copy",
        output_path,
    ]
    _run(cmd)
    print(f"[done] {output_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Burn modern animated Azerbaijani subtitles onto a 9:16 video.")
    ap.add_argument("video", help="Input video file (9:16).")
    ap.add_argument("-o", "--output", help="Output video path. Default: <input>_subbed.mp4")
    ap.add_argument("--model", default="small",
                    choices=["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"],
                    help="Whisper model size (medium/large recommended for Azerbaijani).")
    ap.add_argument("--ass-only", action="store_true", help="Only generate the .ass file, skip burn-in.")
    ap.add_argument("--keep-ass", action="store_true", help="Keep the .ass file next to the output.")
    args = ap.parse_args()

    _check_tool("ffmpeg")

    video = os.path.abspath(args.video)
    if not os.path.isfile(video):
        sys.exit(f"ERROR: video not found: {video}")

    stem = Path(video).stem
    out_dir = Path(video).parent
    output = args.output or str(out_dir / f"{stem}_subbed.mp4")
    ass_path = str(out_dir / f"{stem}.az.ass")

    transcript = transcribe(video, model_size=args.model)
    build_ass(transcript, ass_path)

    if args.ass_only:
        print(f"[ass-only] {ass_path}")
        return

    try:
        burn(video, ass_path, output)
    finally:
        if not args.keep_ass and os.path.exists(ass_path):
            try:
                os.remove(ass_path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
