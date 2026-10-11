#!/usr/bin/env python3
"""Subtitle generation functions for CClip."""

import os
import re

from config import S, hex_to_ass
from utils.helpers import _fmt_ass


def _build_subs(transcript, start, end, clip_name, title, out_dir):
    ass_path = os.path.join(out_dir, f"temp_subs_{clip_name}.ass")
    duration = end - start

    font_family = S['sub_font']
    font_size = S['sub_size']
    margin_v = S['sub_margin_v']
    out_w = S['sub_out_w']
    sha_w = S['sub_sha_w']

    c_pri = hex_to_ass(S['sub_pri'], alpha="00")
    c_sec = hex_to_ass(S['sub_sec'], alpha="00")
    c_out = hex_to_ass(S['sub_out'], alpha="00")
    c_bak = hex_to_ass(S['sub_bak'], alpha="80")

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font_family},{font_size},{c_pri},{c_sec},{c_out},{c_bak},-1,0,0,0,100,100,0,0,1,{out_w},{sha_w},2,40,40,{margin_v},1
Style: Headline,{font_family},78,&H00E0E0E0,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,1,2,8,40,40,170,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []

    safe_title = title.upper().replace('"', '').replace("'", "")
    headline_effect = r"{\t(0,500,\fscx103\fscy103)\t(500,1000,\fscx100\fscy100)}"
    events.append(f"Dialogue: 1,0:00:00.00,{_fmt_ass(duration)},Headline,,0,0,0,,{headline_effect}{safe_title}\n")

    words_in_clip = []
    for segment in transcript.get("segments", []):
        for word in segment.get("words", []):
            if word["start"] >= start and word["end"] <= end + 2:
                words_in_clip.append(word)

    groups = []
    current_group = []

    for i, word in enumerate(words_in_clip):
        current_group.append(word)
        if i + 1 < len(words_in_clip):
            next_word = words_in_clip[i + 1]
            gap = float(next_word["start"]) - float(word["end"])
        else:
            gap = 999.0

        word_text = str(word["word"])

        if len(current_group) >= 3 or gap > 0.45 or word_text.endswith((".", "!", "?", ",")):
            groups.append(current_group)
            current_group = []

    if current_group:
        groups.append(current_group)

    caption_effect = r"{\t(0,90,\fscx118\fscy118)\t(90,160,\fscx100\fscy100)}"

    for group in groups:
        line_start = max(0.0, float(group[0]["start"]) - start)
        line_end = max(line_start + 0.12, float(group[-1]["end"]) - start)

        parts = []
        for w in group:
            raw_word = str(w['word'])
            cleaned_word = re.sub(r'[^\w\s\.,!?\']', '', raw_word)
            cleaned_word = cleaned_word.strip('.,!?;:"\'')
            if not cleaned_word:
                continue
            centiseconds = max(1, int((float(w['end']) - float(w['start'])) * 100))
            parts.append(rf"{{\k{centiseconds}}}{cleaned_word}")

        if parts:
            parts_str = ' '.join(parts)
            events.append(f"Dialogue: 0,{_fmt_ass(line_start)},{_fmt_ass(line_end)},Caption,,0,0,0,,{caption_effect}{parts_str}\n")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "".join(events))

    return ass_path
