#!/usr/bin/env python3
"""AI functions for CClip using Gemini."""

import json

from config import S, log

# ═══════════════════════════════════════════════════════════════════
#  EXPECTED CLIP SCHEMA (per clip key)
# ═══════════════════════════════════════════════════════════════════
_CLIP_SCHEMA_KEYS = {
    "start":       float,
    "end":         float,
    "title":       str,
    "script":      str,
    "yt_title":    str,
    "yt_desc":     str,
    "hook_start":  float,
    "hook_end":    float,
    "impact_time": float,
    "impact_vo":   str,
}


def _validate_clip(name, data):
    """Ensure every expected key exists with a sane default if missing."""
    defaults = {
        "start": 0.0, "end": 10.0,
        "title": "CLIP", "script": "Wow.",
        "yt_title": "Epic moment #shorts #youtubeshorts #viral #trending",
        "yt_desc": "You won't believe this.\n\n#clips",
        "hook_start": data.get("start", 0.0),
        "hook_end": data.get("start", 0.0) + 3.0,
        "impact_time": data.get("start", 0.0) + (data.get("end", 10.0) - data.get("start", 0.0)) / 2,
        "impact_vo": "This is the moment everything changed. Nobody saw it coming.",
    }
    for key, expected_type in _CLIP_SCHEMA_KEYS.items():
        if key not in data:
            data[key] = defaults.get(key)
            log(f"    AI clip '{name}' missing '{key}' — using default.", "warn")
        else:
            try:
                data[key] = expected_type(data[key])
            except (ValueError, TypeError):
                data[key] = defaults.get(key)
    # Clamp hook within clip range
    clip_start = data["start"]
    clip_end = data["end"]
    data["hook_start"] = max(clip_start, min(data["hook_start"], clip_end - 1.0))
    data["hook_end"] = max(data["hook_start"] + 1.0, min(data["hook_end"], clip_end))
    data["impact_time"] = max(clip_start, min(data["impact_time"], clip_end))
    return data


def _ai_clips(client, transcript, type_config):
    transcript_text = ""
    for segment in transcript.get("segments", []):
        start = segment['start']
        end = segment['end']
        text = segment['text']
        transcript_text += f"[{start:.1f}-{end:.1f}] {text}\n"
    transcript_text = transcript_text[:500_000]

    prompt_payload = f"{S['ai_prompt']}\n\nTranscript:\n{transcript_text}"
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt_payload,
        config=type_config.GenerateContentConfig(response_mime_type="application/json")
    )
    raw = json.loads(response.text)

    # Validate every clip against the schema
    for clip_name, clip_data in raw.items():
        if isinstance(clip_data, dict):
            raw[clip_name] = _validate_clip(clip_name, clip_data)
    return raw


def _mock_clips():
    return {
        "funny_1": {
            "start": 120.0,
            "end": 150.0,
            "title": "FUNNY TEST",
            "script": "Just a test!",
            "yt_title": "Test funny clip #shorts #youtubeshorts #viral #trending",
            "yt_desc": "Testing the pipeline!\n\n#test #clips",
            "hook_start": 121.0,
            "hook_end": 124.0,
            "impact_time": 135.0,
            "impact_vo": "This is the moment everything changed. Nobody saw it coming."
        }
    }
