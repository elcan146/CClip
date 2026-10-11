#!/usr/bin/env python3
"""Settings management for CClip."""

import os
import json
import copy

# ═══════════════════════════════════════════════════════════════════
#  DEFAULT AI PROMPT
# ═══════════════════════════════════════════════════════════════════
DEFAULT_PROMPT = """You are an expert TikTok/Shorts video editor. Analyze this movie transcript.
Find the best moments and return ONLY a valid JSON object matching this exact structure.

{
  "funny_1": {
     "start": 0.0,
     "end": 0.0,
     "title": "A short, catchy 4-6 word explanatory title",
     "script": "A short 1-sentence joke.",
     "yt_title": "A catchy title under 55 chars #shorts #youtubeshorts #viral #trending",
     "yt_desc": "A catchy 2-sentence description of the scene. \\n\\n#funny #clips",
     "hook_start": 0.0,
     "hook_end": 0.0,
     "impact_time": 0.0,
     "impact_vo": "Two sentences explaining the dramatic context at this moment."
  },
  "funny_2": {"start": 0.0, "end": 0.0, "title": "...", "script": "...", "yt_title": "...", "yt_desc": "...", "hook_start": 0.0, "hook_end": 0.0, "impact_time": 0.0, "impact_vo": "..."},
  "dramatic_1": {"start": 0.0, "end": 0.0, "title": "...", "script": "...", "yt_title": "...", "yt_desc": "...", "hook_start": 0.0, "hook_end": 0.0, "impact_time": 0.0, "impact_vo": "..."},
  "dramatic_2": {"start": 0.0, "end": 0.0, "title": "...", "script": "...", "yt_title": "...", "yt_desc": "...", "hook_start": 0.0, "hook_end": 0.0, "impact_time": 0.0, "impact_vo": "..."},
  "badass_1": {"start": 0.0, "end": 0.0, "title": "...", "script": "...", "yt_title": "...", "yt_desc": "...", "hook_start": 0.0, "hook_end": 0.0, "impact_time": 0.0, "impact_vo": "..."},
  "badass_2": {"start": 0.0, "end": 0.0, "title": "...", "script": "...", "yt_title": "...", "yt_desc": "...", "hook_start": 0.0, "hook_end": 0.0, "impact_time": 0.0, "impact_vo": "..."}
}

BLIND METADATA RULES:
1. Write "blind" titles and descriptions — NEVER mention the actual movie title, actor names, or character names.
   Use generic terms like "this guy", "the villain", "our hero", "she", "him", etc.
2. EVERY single 'yt_title' MUST end with EXACTLY these four hashtags: #shorts #youtubeshorts #viral #trending
3. The core text of the 'yt_title' MUST be under 55 characters so the total length never exceeds YouTube's 100 character limit.
4. ABSOLUTELY NO hashtags related to the movie name, actor name, or character name in either the title or description. Keep it generic (e.g., #funny, #drama, #clips).
5. Ensure the start and end times represent 30 to 60-second clips.

HOOK & IMPACT ANALYSIS:
6. For each clip, pick the single most engaging out-of-context 3-second quote within that clip's range.
   Set 'hook_start' and 'hook_end' to those timestamps (must be within start..end, ~3s apart).
7. 'impact_time' = the timestamp of the most dramatic/funny/intense single moment in the clip.
8. 'impact_vo' = a punchy 2-sentence voiceover script that explains the context at that moment
   to someone who has never seen the movie. Again, do NOT use real names."""

# ═══════════════════════════════════════════════════════════════════
#  SETTINGS DICTIONARY
# ═══════════════════════════════════════════════════════════════════
S = {
    "in_folder":   os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "movies_input")),
    "out_folder":  os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "clips_output")),
    "mus_folder":  os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "music")),
    "gemini_key":  "",
    "test_mode":   False,
    "pub_mode":    False,
    "resolution":  "1080x1920 (Shorts)",
    "audio_track": "Auto (English)",
    "total_movies":  0,
    "total_shorts":  0,
    "clip_types":    {"Funny": 0, "Dramatic": 0, "Badass": 0},
    "render_times":  [],
    "yt_authed":     False,
    "ui_theme":       "Dark",
    "ui_scale":       1.0,
    "ui_font":        "Segoe UI Variable",
    "sub_font":       "Readfine",
    "custom_theme_font": "Segoe UI Variable",
    "custom_theme":   {
        "BG": "#1c1c1c", "SURFACE": "#202020", "CARD": "#272727",
        "CARD2": "#2d2d2d", "CARD3": "#323232", "SIDEBAR": "#1f1f1f",
        "ACC": "#60cdff", "ACC2": "#0078d4",
        "TEXT": "#ffffff", "SUB": "#b0b0b0",
        "BOR": "#3a3a3a", "BOR_SFT": "#2a2a2a"
    },
    "vol_movie_base": 1.0,
    "vol_movie_duck": 0.6,
    "vol_vo_base":    1.0,
    "vol_vo_duck":    0.06,
    "vol_bgm":        0.05,
    "sub_size":       92,
    "sub_pri":        "#FFFFFF",
    "sub_sec":        "#FFFF00",
    "sub_out":        "#000000",
    "sub_bak":        "#000000",
    "sub_out_w":      3,
    "sub_sha_w":      3,
    "sub_margin_v":   430,
    "v_speed":        1.1,
    "v_blur":         30,
    "v_bg_bri":       -0.03,
    "v_bg_sat":       0.92,
    "v_fg_con":       1.08,
    "v_fg_sat":       1.12,
    "v_fg_bri":       0.01,
    "ai_prompt":      DEFAULT_PROMPT,
    "fill_strategy":       "Blur Padding",
    "enable_cold_open":    False,
    "enable_impact_vo":    False,
    "enable_auto_ducking": False,
    "enable_silence_cut":  False,
    "enable_mirror_cuts":  False,
    "enable_motion_blur":  False,
}

SETTINGS_FILE = "CClip_settings.json"
DEFAULT_SETTINGS = copy.deepcopy(S)

# Load saved settings if file exists
if os.path.exists(SETTINGS_FILE):
    try:
        with open(SETTINGS_FILE, "r") as f:
            saved_data = json.load(f)
            # If saved prompt is outdated (missing new AI fields), discard it
            saved_prompt = saved_data.get("ai_prompt", "")
            if saved_prompt and "hook_start" not in saved_prompt:
                saved_data.pop("ai_prompt", None)
            S.update(saved_data)
    except Exception as e:
        print(f"Failed to load settings: {e}")

# ═══════════════════════════════════════════════════════════════════
#  SETTINGS FUNCTIONS
# ═══════════════════════════════════════════════════════════════════
def save_settings():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(S, f, indent=4)
    except Exception as e:
        print(f"Failed to save settings: {e}")

def update_s(key, val, trigger_save_popup=True):
    print(f"update_s called: key={key}, val={val[:20] if isinstance(val, str) and len(val) > 20 else val}, trigger={trigger_save_popup}, APP_INSTANCE={APP_INSTANCE is not None}")
    if S.get(key) == val:
        print(f"  -> Value unchanged, skipping")
        return
    S[key] = val
    if trigger_save_popup and APP_INSTANCE:
        print(f"  -> Triggering save popup")
        APP_INSTANCE._mark_dirty(key)
        APP_INSTANCE.show_save_popup_safe()
    else:
        print(f"  -> No popup: trigger={trigger_save_popup}, has_instance={APP_INSTANCE is not None}")

# APP_INSTANCE will be set by the main application
APP_INSTANCE = None
