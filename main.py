#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║          CClip  OPUS  —  PyQt6  ·  Fluent Edition                     ║
# ║          Smooth · Animated · Microsoft-Inspired UI                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝
"""
Full PyQt6 rewrite of CClip.
Backend pipeline (Whisper -> Gemini -> ASS subs -> FFmpeg -> YouTube upload) is
preserved verbatim from the original; only the UI layer is replaced.
"""

import os
import sys
import time
import json
import threading
import subprocess
import re
import queue
from datetime import datetime

# Suppress Qt multimedia FFmpeg warnings
os.environ["QT_LOGGING_RULES"] = "qt.multimedia.ffmpeg=false"

import psutil
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QAbstractAnimation,
    QObject, pyqtSignal, QThread, QVariantAnimation, pyqtProperty, QSize, QRect,
)
from PyQt6.QtGui import (
    QColor, QCursor, QPainter, QBrush, QPen, QFont, QFontDatabase,
    QPainterPath, QLinearGradient, QIcon, QMouseEvent,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QPlainTextEdit, QSlider,
    QCheckBox, QComboBox, QStackedWidget, QFrame, QScrollArea, QFileDialog,
    QMessageBox, QGraphicsOpacityEffect, QProgressBar,
    QStyleFactory, QDialog, QGraphicsDropShadowEffect,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtCore import QUrl
import copy

# ═══════════════════════════════════════════════════════════════════
#  MAC-LEVEL BUTTON ANIMATION  (shadows QPushButton module-wide)
# ═══════════════════════════════════════════════════════════════════
_OrigQPushButton = QPushButton

class QPushButton(_OrigQPushButton):
    """Drop-in QPushButton with macOS spring-press scale animation on every click."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bscale = 1.0
        self._bpa = QPropertyAnimation(self, b"_bscale_prop", self)
        self._bpa.setDuration(70)
        self._bpa.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._bra = QPropertyAnimation(self, b"_bscale_prop", self)
        self._bra.setDuration(420)
        self._bra.setEasingCurve(QEasingCurve.Type.OutBack)

    def _get_bscale(self): return self._bscale
    def _set_bscale(self, v):
        self._bscale = float(v)
        self.update()
    _bscale_prop = pyqtProperty(float, _get_bscale, _set_bscale)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._bra.stop()
            self._bpa.setStartValue(self._bscale)
            self._bpa.setEndValue(0.91)
            self._bpa.start()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._bpa.stop()
        self._bra.setStartValue(self._bscale)
        self._bra.setEndValue(1.0)
        self._bra.start()
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        if abs(self._bscale - 1.0) > 0.003:
            from PyQt6.QtWidgets import QStyleOptionButton
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            cx, cy = self.width() / 2.0, self.height() / 2.0
            p.translate(cx, cy)
            p.scale(self._bscale, self._bscale)
            p.translate(-cx, -cy)
            opt = QStyleOptionButton()
            self.initStyleOption(opt)
            self.style().drawControl(
                self.style().ControlElement.CE_PushButton, opt, p, self)
        else:
            super().paintEvent(e)


UI_FONTS = [
    "Segoe UI Variable", "Segoe UI", "Inter", "Roboto",
    "Cascadia Code", "JetBrains Mono", "Verdana", "Tahoma",
    "SF Pro Text", "Open Sans",
]
SUB_FONTS = [
    "Readfine", "Impact", "Arial Black", "Bebas Neue",
    "Oswald", "Anton", "Montserrat", "Roboto Black",
    "Segoe UI Black",
]

try:
    import GPUtil
    GPU_LIB = True
except ImportError:
    GPU_LIB = False

# ═══════════════════════════════════════════════════════════════════
#  FLUENT THEME
# ═══════════════════════════════════════════════════════════════════
BG       = "#1c1c1c"
SURFACE  = "#202020"
CARD     = "#272727"
CARD2    = "#2d2d2d"
CARD3    = "#323232"
SIDEBAR  = "#1f1f1f"
ACC      = "#60cdff"
ACC2     = "#0078d4"
ACC_DK   = "#005a9e"
GREEN    = "#6ccb5f"
RED      = "#ff6b6b"
YEL      = "#fbbf24"
BLUE     = "#4cc2ff"
CYAN     = "#4fd1c5"
TEXT     = "#ffffff"
SUB      = "#b0b0b0"
SUB_DK   = "#8a8a8a"
BOR      = "#3a3a3a"
BOR_SFT  = "#2a2a2a"

# ═══════════════════════════════════════════════════════════════════
#  GLOBAL STATE & PERSISTENCE
# ═══════════════════════════════════════════════════════════════════
CANCEL   = threading.Event()
LOG_Q    = queue.Queue()
YT_LOG_Q = queue.Queue()
STATS_Q  = queue.Queue()
APP_INSTANCE = None

DEFAULT_PROMPT = """You are an expert TikTok/Shorts video editor. Analyze this movie transcript.
Find the best moments and return ONLY a valid JSON object matching this exact structure.

{
  "funny_1": {
     "start": 0.0, 
     "end": 0.0, 
     "title": "A short, catchy 4-6 word explanatory title", 
     "script": "A short 1-sentence joke.",
     "yt_title": "A catchy title under 55 chars #shorts #youtubeshorts #viral #trending",
     "yt_desc": "A catchy 2-sentence description of the scene. \\n\\n#funny #clips"
  },
  "funny_2": {"start": 0.0, "end": 0.0, "title": "A short, catchy 4-6 word explanatory title", "script": "A short 1-sentence joke.", "yt_title": "...", "yt_desc": "..."},
  "dramatic_1": {"start": 0.0, "end": 0.0, "title": "A short, catchy 4-6 word explanatory title", "script": "A dramatic 1-sentence observation.", "yt_title": "...", "yt_desc": "..."},
  "dramatic_2": {"start": 0.0, "end": 0.0, "title": "A short, catchy 4-6 word explanatory title", "script": "A dramatic 1-sentence observation.", "yt_title": "...", "yt_desc": "..."},
  "badass_1": {"start": 0.0, "end": 0.0, "title": "A short, catchy 4-6 word explanatory title", "script": "A hype, 1-sentence reaction.", "yt_title": "...", "yt_desc": "..."},
  "badass_2": {"start": 0.0, "end": 0.0, "title": "A short, catchy 4-6 word explanatory title", "script": "A hype, 1-sentence reaction.", "yt_title": "...", "yt_desc": "..."}
}

CRITICAL RULES FOR METADATA:
1. EVERY single 'yt_title' MUST end with EXACTLY these four hashtags: #shorts #youtubeshorts #viral #trending
2. The core text of the 'yt_title' MUST be under 55 characters so the total length never exceeds YouTube's 100 character limit.
3. ABSOLUTELY NO hashtags related to the movie name, actor name, or character name in either the title or description. Keep it generic (e.g., #funny, #drama, #clips).
4. Ensure the start and end times represent 30 to 60-second clips."""

S = {
    "in_folder":   os.path.abspath("movies_input"),
    "out_folder":  os.path.abspath("clips_output"),
    "mus_folder":  os.path.abspath("music"),
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
    "sub_font":       "Readfine",
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
    "ai_prompt":      DEFAULT_PROMPT
}

SETTINGS_FILE = "CClip_settings.json"
DEFAULT_SETTINGS = copy.deepcopy(S)

if os.path.exists(SETTINGS_FILE):
    try:
        with open(SETTINGS_FILE, "r") as f:
            saved_data = json.load(f)
            S.update(saved_data)
    except Exception as e:
        print(f"Failed to load settings: {e}")

def save_settings():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(S, f, indent=4)
    except Exception as e:
        print(f"Failed to save settings: {e}")

def update_s(key, val, trigger_save_popup=True):
    if S.get(key) == val:
        return
    S[key] = val
    if trigger_save_popup and APP_INSTANCE:
        APP_INSTANCE._mark_dirty(key)
        APP_INSTANCE.show_save_popup_safe()

def apply_theme():
    """Resolve module-level color constants based on S['ui_theme'].
    Always restores the FULL palette so switching Light -> Dark (and vice versa)
    always lands on the full set of colors.
    """
    global BG, SURFACE, CARD, CARD2, CARD3, SIDEBAR, ACC, ACC2, ACC_DK
    global TEXT, SUB, SUB_DK, BOR, BOR_SFT
    theme = S.get("ui_theme", "Dark")
    if theme == "Light":
        BG="#f3f3f3"; SURFACE="#fbfbfb"; CARD="#ffffff"
        CARD2="#f5f5f5"; CARD3="#ebebeb"; SIDEBAR="#fafafa"
        ACC="#0078d4"; ACC2="#005a9e"; ACC_DK="#004578"
        TEXT="#1a1a1a"; SUB="#5c5c5c"; SUB_DK="#8a8a8a"
        BOR="#d0d0d0"; BOR_SFT="#e5e5e5"
    elif theme == "Custom":
        # start from Dark defaults, then overlay custom colors
        BG="#1c1c1c"; SURFACE="#202020"; CARD="#272727"
        CARD2="#2d2d2d"; CARD3="#323232"; SIDEBAR="#1f1f1f"
        ACC="#60cdff"; ACC2="#0078d4"; ACC_DK="#005a9e"
        TEXT="#ffffff"; SUB="#b0b0b0"; SUB_DK="#8a8a8a"
        BOR="#3a3a3a"; BOR_SFT="#2a2a2a"
        ct = S.get("custom_theme", {})
        BG = ct.get("BG", BG); SURFACE = ct.get("SURFACE", SURFACE)
        CARD = ct.get("CARD", CARD); CARD2 = ct.get("CARD2", CARD2)
        CARD3 = ct.get("CARD3", CARD3); SIDEBAR = ct.get("SIDEBAR", SIDEBAR)
        ACC = ct.get("ACC", ACC); ACC2 = ct.get("ACC2", ACC2)
        TEXT = ct.get("TEXT", TEXT); SUB = ct.get("SUB", SUB)
        BOR = ct.get("BOR", BOR); BOR_SFT = ct.get("BOR_SFT", BOR_SFT)
    else:  # Dark (default)
        BG="#1c1c1c"; SURFACE="#202020"; CARD="#272727"
        CARD2="#2d2d2d"; CARD3="#323232"; SIDEBAR="#1f1f1f"
        ACC="#60cdff"; ACC2="#0078d4"; ACC_DK="#005a9e"
        TEXT="#ffffff"; SUB="#b0b0b0"; SUB_DK="#8a8a8a"
        BOR="#3a3a3a"; BOR_SFT="#2a2a2a"

def log(msg, color="normal"):
    LOG_Q.put((msg, color))

def yt_log(msg):
    YT_LOG_Q.put(msg)

def hex_to_ass(hex_color, alpha="00"):
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        return f"&H{alpha}FFFFFF"
    r = hex_color[0:2]
    g = hex_color[2:4]
    b = hex_color[4:6]
    return f"&H{alpha}{b}{g}{r}".upper()

# ═══════════════════════════════════════════════════════════════════
#  PIPELINE HELPERS  (UNCHANGED FROM ORIGINAL)
# ═══════════════════════════════════════════════════════════════════
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

def _extract_audio(video_path, output_wav_path, track_index):
    cmd = ["ffmpeg", "-y", "-i", video_path, "-map", f"0:a:{track_index}", "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", output_wav_path]
    subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

def _transcribe(wav_path, progress_cb):
    try:
        import whisper
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = whisper.load_model("small").to(device)
        result = model.transcribe(wav_path, language="en", word_timestamps=True)
        progress_cb(100, 100, "Transcription complete")
        return result
    except Exception as e:
        log(f"Whisper error: {e}", "error")
        return {"segments": []}

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
    return json.loads(response.text)

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

def _mock_clips():
    return {
        "funny_1": {
            "start": 120.0,
            "end": 130.0,
            "title": "FUNNY TEST",
            "script": "Just a test!",
            "yt_title": "Test funny clip #shorts #youtubeshorts #viral #trending",
            "yt_desc": "Testing the pipeline!\n\n#test #clips"
        }
    }

def _gen_voiceover(text, output_path):
    try:
        cmd = ["edge-tts", "--voice", "en-US-GuyNeural", "--text", text, "--write-media", output_path]
        subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        log(f"    Voiceover saved: {os.path.basename(output_path)}", "success")
    except Exception as e:
        log(f"    Voiceover error: {e}", "warn")

def _fmt_ass(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

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

def _render(video_path, sub_path, vo_path, music_path, start, end, out_path, audio_track, progress_cb, duration):
    safe_sub = sub_path.replace("\\", "/").replace(":", "\\:")
    fade_out = max(duration - 0.18, 0)
    vo_dur   = _audio_dur(vo_path)

    v_blur = S['v_blur']
    v_bg_bri = S['v_bg_bri']
    v_bg_sat = S['v_bg_sat']
    v_fg_con = S['v_fg_con']
    v_fg_sat = S['v_fg_sat']
    v_fg_bri = S['v_fg_bri']
    v_speed = S['v_speed']

    v_m_base = S['vol_movie_base']
    v_m_duck = S['vol_movie_duck']
    v_vo_base = S['vol_vo_base']
    v_vo_duck = S['vol_vo_duck']
    v_bgm = S['vol_bgm']

    video_filter = (
        "[0:v]hflip,split=2[v_bg][v_fg];"
        f"[v_bg]setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,boxblur={v_blur}:2,eq=brightness={v_bg_bri}:saturation={v_bg_sat}[bg];"
        "[v_fg]setpts=PTS-STARTPTS,crop=w='min(iw\\,ih)':h='min(iw\\,ih)',"
        "scale=1080:1080,setsar=1[fg];"
        "[bg][fg]overlay=0:420[composed];"
        "[composed]"
        f"eq=contrast={v_fg_con}:saturation={v_fg_sat}:brightness={v_fg_bri},"
        "unsharp=5:5:0.55:3:3:0.20,vignette=PI/7,"
        "fade=t=in:st=0:d=0.12,"
        f"fade=t=out:st={fade_out}:d=0.18,"
        f"ass='{safe_sub}',setpts=PTS/{v_speed}[final_v]"
    )

    audio_filter = (
        f"[0:a:{audio_track}]volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
        "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[movie_a];"
        f"[1:a]volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
        "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"
        f"[2:a]volume={v_bgm},"
        "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"
        "[movie_a][voice_a][bgm_a]amix=inputs=3:duration=first:dropout_transition=0,"
        "loudnorm=I=-14:LRA=11:TP=-1.5,"
        "bass=g=4:f=100,treble=g=3:f=7500,"
        "acompressor=threshold=-15dB:ratio=3:makeup=3,"
        f"extrastereo=m=1.3,atempo={v_speed}[final_a]"
    )

    command = [
        "ffmpeg", "-y", "-ss", str(start), "-to", str(end),
        "-i", video_path,
        "-stream_loop", "-1", "-i", vo_path,
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex_threads", "1",
        "-filter_complex", f"{video_filter};{audio_filter}",
        "-map", "[final_v]", "-map", "[final_a]",
        "-c:v", "h264_nvenc", "-preset", "p4",
        "-pix_fmt", "yuv420p", "-c:a", "aac", out_path
    ]

    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW
    )

    time_regex = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.\d+")
    error_log = ""

    for line in process.stdout:
        error_log += line
        log(line.rstrip(), "mono")
        match = time_regex.search(line)
        if match:
            h = int(match.group(1))
            m = int(match.group(2))
            s = int(match.group(3))
            current_seconds = h * 3600 + m * 60 + s
            percent = min(int(current_seconds / max(duration, 1) * 100), 99)
            progress_cb(percent, 100, f"Rendering  {current_seconds:.0f}s / {duration:.0f}s")

    process.wait()

    if process.returncode != 0:
        log("FFmpeg error! Last lines:", "error")
    else:
        log(f"Render complete  ->  {os.path.basename(out_path)}", "success")

    progress_cb(100, 100, "Render complete")

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

def run_batch(progress_cb, macro_cb, finish_cb):
    import warnings
    warnings.filterwarnings("ignore")

    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        log("Missing: google-genai  --  pip install google-genai", "error")
        finish_cb(False)
        return

    if not S["test_mode"] and not S["gemini_key"]:
        log("Gemini API key is empty!", "error")
        finish_cb(False)
        return

    valid_extensions = ('.mp4', '.mkv', '.mov', '.avi')
    movies = []

    for f in os.listdir(S["in_folder"]):
        if f.lower().endswith(valid_extensions):
            movies.append(f)

    if not movies:
        log(f"No movies found in: {S['in_folder']}", "warn")
        finish_cb(False)
        return

    if not S["test_mode"]:
        client = genai.Client(api_key=S["gemini_key"])
    else:
        client = None

    total_movies = len(movies)
    update_s("total_movies", S["total_movies"] + total_movies, trigger_save_popup=False)

    for idx, movie_file in enumerate(movies, 1):
        if CANCEL.is_set():
            log("Batch cancelled by user.", "warn")
            break

        try:
            movie_path = os.path.join(S["in_folder"], movie_file)
            movie_name_no_ext = os.path.splitext(movie_file)[0]
            movie_out_dir = os.path.join(S["out_folder"], movie_name_no_ext)
            os.makedirs(movie_out_dir, exist_ok=True)

            macro_cb(idx, total_movies, movie_file)
            log(f"\n{'-'*58}\n  [{idx}/{total_movies}]  {movie_file}\n{'-'*58}", "success")

            audio_track = _get_audio_track(movie_path)

            if S["test_mode"]:
                log("  TEST MODE -- using mock data", "warn")
                full_transcript = _mock_transcript()
                clips_data = _mock_clips()
            else:
                temp_wav = os.path.join(movie_out_dir, "temp_audio.wav")
                _extract_audio(movie_path, temp_wav, audio_track)

                progress_cb(0, 100, "Whisper transcribing...")
                full_transcript = _transcribe(temp_wav, progress_cb)

                if os.path.exists(temp_wav):
                    os.remove(temp_wav)

                try:
                    clips_data = _ai_clips(client, full_transcript, types)
                except Exception as e:
                    log(f"  Gemini error: {e}", "error")
                    continue

            total_clips = len(clips_data)

            for ci, (clip_name, data) in enumerate(clips_data.items(), 1):
                if CANCEL.is_set():
                    break

                log(f"\n  Clip [{ci}/{total_clips}]: {clip_name.upper()}", "info")
                start = data.get("start", 0.0)
                end = data.get("end", 10.0)

                prefix = clip_name.split("_")[0]

                music_filename = "badass.mp3"
                if prefix == "funny":
                    music_filename = "funny.mp3"
                elif prefix == "dramatic":
                    music_filename = "sad.mp3"

                music_path = os.path.join(S["mus_folder"], music_filename)

                meta_path = os.path.join(movie_out_dir, f"{clip_name}_metadata.txt")
                with open(meta_path, "w", encoding="utf-8") as mf:
                    yt_title = data.get('yt_title', 'Clip #shorts')
                    yt_desc = data.get('yt_desc', '#clips')
                    mf.write(f"TITLE:\n{yt_title}\n\nDESCRIPTION:\n{yt_desc}")

                vo_path = os.path.join(movie_out_dir, f"vo_{clip_name}.mp3")
                script_text = data.get("script", "Wow.")
                _gen_voiceover(script_text, vo_path)

                clip_title = data.get("title", "EPIC")
                sub_path = _build_subs(full_transcript, start, end, clip_name, clip_title, movie_out_dir)

                output_mp4 = os.path.join(movie_out_dir, f"{clip_name}.mp4")

                render_start_time = time.time()
                _render(movie_path, sub_path, vo_path, music_path, start, end, output_mp4, audio_track, progress_cb, end - start)
                render_elapsed = time.time() - render_start_time

                S["render_times"].append(round(render_elapsed, 1))
                update_s("total_shorts", S["total_shorts"] + 1, trigger_save_popup=False)

                cat_key = "Badass"
                if prefix == "funny":
                    cat_key = "Funny"
                elif prefix == "dramatic":
                    cat_key = "Dramatic"

                S["clip_types"][cat_key] = S["clip_types"].get(cat_key, 0) + 1
                save_settings()
        except Exception as e:
            import traceback
            log(f"  FAILED to process {movie_file}: {e}", "error")
            log(traceback.format_exc(), "error")
            continue

    log(f"\n{'-'*58}\n  BATCH COMPLETE\n{'-'*58}", "success")
    finish_cb(True)

def run_youtube_upload(finish_cb):
    try:
        from googleapiclient.discovery import build as yt_build
        from googleapiclient.http import MediaFileUpload
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request as GRequest
        from google.oauth2.credentials import Credentials
    except ImportError as e:
        msg = f"Google API lib missing: {e}"
        log(msg, "error")
        yt_log(msg)
        finish_cb(False)
        return

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
    creds = None

    user_data_dir = os.path.join(os.path.dirname(__file__), "user_data")
    token_path = os.path.join(user_data_dir, "token.json")
    secrets_path = os.path.join(user_data_dir, "client_secrets.json")

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GRequest())
        else:
            if not os.path.exists(secrets_path):
                log("client_secrets.json not found in user_data/", "error")
                return
            flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as tf:
            tf.write(creds.to_json())

    update_s("yt_authed", True, trigger_save_popup=False)

    youtube = yt_build("youtube", "v3", credentials=creds)
    privacy = "public" if S["pub_mode"] else "private"
    uploaded_count = 0

    for movie_dir in os.listdir(S["out_folder"]):
        full_dir = os.path.join(S["out_folder"], movie_dir)

        if not os.path.isdir(full_dir):
            continue

        for fname in os.listdir(full_dir):
            if not fname.endswith(".mp4"):
                continue

            clip_name = os.path.splitext(fname)[0]
            video_path = os.path.join(full_dir, fname)
            meta_path = os.path.join(full_dir, f"{clip_name}_metadata.txt")

            title = "Awesome YouTube Short"
            desc = "#shorts #video"

            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as mf:
                    content = mf.read()

                if "TITLE:" in content and "DESCRIPTION:" in content:
                    parts = content.split("DESCRIPTION:")
                    if parts[0].replace("TITLE:", "").strip():
                        title = parts[0].replace("TITLE:", "").strip()[:100]
                    if parts[1].strip():
                        desc = parts[1].strip()

            yt_log(f"\nUploading: {title[:60]}... (Mode: {privacy.upper()})")

            body = {
                "snippet": {
                    "title": title,
                    "description": desc,
                    "categoryId": "24"
                },
                "status": {
                    "privacyStatus": privacy,
                    "selfDeclaredMadeForKids": False
                }
            }

            try:
                request = youtube.videos().insert(
                    part=",".join(body.keys()),
                    body=body,
                    media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True)
                )
                response = request.execute()

                yt_log(f"Uploaded! https://youtu.be/{response['id']}")
                uploaded_count += 1
                time.sleep(5)

            except Exception as e:
                yt_log(f"Upload failed: {e}")
                if "quotaExceeded" in str(e):
                    yt_log("Daily 10,000 quota exceeded. Try tomorrow!")

    yt_log(f"\nUpload session complete -- {uploaded_count} video(s) uploaded.")
    finish_cb(True)

def _stats_worker():
    while True:
        cpu = psutil.cpu_percent(interval=1.0)
        ram = psutil.virtual_memory().percent

        current_drive = os.path.abspath('.')
        try:
            dsk = psutil.disk_usage(current_drive).percent
        except:
            dsk = 0.0

        gp, gt, gm = 0.0, 0.0, 0.0
        if GPU_LIB:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    gp = gpus[0].load * 100
                    gt = gpus[0].temperature
                    gm = gpus[0].memoryUtil * 100
            except:
                pass
        else:
            try:
                cmd = [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits"
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=2, creationflags=subprocess.CREATE_NO_WINDOW)
                if r.returncode == 0:
                    parts = r.stdout.strip().split(",")
                    gp = float(parts[0])
                    gt = float(parts[1])
                    gm = float(parts[2]) / max(float(parts[3]), 1) * 100
            except:
                pass

        STATS_Q.put((cpu, ram, gp, gt, gm, dsk))

# ═══════════════════════════════════════════════════════════════════
#  GLOBAL QSS  (Fluent stylesheet)
# ═══════════════════════════════════════════════════════════════════
def build_qss(scale=1.0):
    family = S.get("ui_font", "Segoe UI Variable")
    def s(px): return max(7, int(round(px * scale)))
    raw = f"""
* {{
    color: {TEXT};
    font-family: '{family}', 'Segoe UI', sans-serif;
    font-size: {s(13)}px;
}}
QWidget#root {{ background: {BG}; }}
QWidget#sidebar, QWidget#rightbar {{ background: {SIDEBAR}; }}
QWidget#pageBg {{ background: {BG}; }}

QFrame#card, QFrame#cardSlim {{
    background: {CARD};
    border: 1px solid {BOR_SFT};
    border-radius: 10px;
}}
QFrame#hairline {{ background: {BOR_SFT}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#vhairline {{ background: {BOR}; max-width: 1px; min-width: 1px; border: none; }}

QLabel#h1   {{ font-size: {s(22)}px; font-weight: 600; color: {TEXT}; }}
QLabel#h2   {{ font-size: {s(16)}px; font-weight: 600; color: {TEXT}; }}
QLabel#h3   {{ font-size: {s(13)}px; font-weight: 600; color: {TEXT}; }}
QLabel#sub  {{ color: {SUB}; }}
QLabel#subSm{{ color: {SUB}; font-size: {s(11)}px; }}
QLabel#tiny {{ color: {SUB_DK}; font-size: {s(11)}px; }}
QLabel#brandTitle {{ font-size: {s(22)}px; font-weight: 600; color: {TEXT}; }}
QLabel#brandSub   {{ color: {SUB}; font-size: {s(11)}px; }}
QLabel#counterValue {{ font-size: {s(42)}px; font-weight: 600; }}
QLabel#pctLabel     {{ color: {ACC}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: {s(13)}px; }}

QPushButton {{
    background: {CARD2};
    color: {TEXT};
    border: 1px solid {BOR};
    border-radius: 6px;
    padding: 7px 14px;
}}
QPushButton:hover {{ background: {CARD3}; border-color: {ACC}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: {SUB_DK}; }}

QPushButton#primary {{
    background: {ACC2};
    color: #ffffff;
    border: 1px solid {ACC2};
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {ACC_DK}; border-color: {ACC}; }}
QPushButton#primary:disabled {{ background: {CARD3}; color: {SUB_DK}; border-color: {BOR}; }}

QPushButton#softAction {{
    background: {SURFACE};
    color: {ACC2};
    border: 1px solid {ACC2};
    font-weight: 600;
}}
QPushButton#softAction:hover {{
    background: {CARD3};
    color: {TEXT};
    border-color: {ACC};
}}
QPushButton#softAction:pressed {{
    background: {ACC2};
    color: #ffffff;
    border-color: {ACC2};
}}
QPushButton#softAction:disabled {{
    background: {CARD2};
    color: {ACC2};
    border: 1px solid {ACC2};
}}

QPushButton#danger {{ background: {RED}; color: white; border: 1px solid {RED}; }}
QPushButton#danger:hover {{ background: #d94545; }}
QPushButton#warning {{ background: {YEL}; color: #1c1c1c; border: 1px solid {YEL}; }}

QPushButton#nav {{
    background: transparent;
    border: none;
    color: {SUB};
    text-align: left;
    padding: 9px 14px 9px 22px;
    border-radius: 6px;
    font-size: {s(13)}px;
}}
QPushButton#nav:hover {{ background: {CARD3}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {CARD3}; color: {TEXT}; font-weight: 600; }}

QLineEdit, QPlainTextEdit, QTextEdit {{
    background: {CARD2};
    color: {TEXT};
    border: 1px solid {BOR};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {ACC2};
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {ACC}; }}
QPlainTextEdit#terminal, QPlainTextEdit#ytTerminal, QPlainTextEdit#copyBox {{
    font-family: 'Cascadia Mono', Consolas, monospace;
    font-size: {s(11)}px;
}}

QComboBox {{
    background: {CARD2};
    color: {TEXT};
    border: none;
    border-radius: 10px;
    padding: 8px 12px 8px 12px;
    min-height: 28px;
    outline: none;
}}

QComboBox:hover {{
    background: {CARD3};
}}

QComboBox:focus {{
    background: {CARD};
}}

QComboBox:on {{
    background: {CARD};
}}

/* Hide native drop-down completely */
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 0px;
    border: none;
    background: transparent;
}}

/* Hide native arrow */
QComboBox::down-arrow {{
    image: none;
    width: 0px;
    height: 0px;
    border: none;
}}

/* Dropdown list */
QComboBox QAbstractItemView {{
    background: {CARD};
    color: {TEXT};
    border: none;
    border-radius: 8px;
    padding: 6px;
    outline: none;

    selection-background-color: {ACC2};
    selection-color: #ffffff;
}}

/* Optional: softer item spacing */
QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    border-radius: 4px;
}}

QComboBox QAbstractItemView::item:selected {{
    background: {ACC2};
    color: #ffffff;
}}

QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{
    background: {CARD2}; 
    height: 4px; 
    border-radius: 2px;
    border: 1px solid {BOR_SFT};
    margin: 0 10px;
}}
QSlider::sub-page:horizontal {{
    background: {ACC};
    border-radius: 2px;
    
}}
QSlider::add-page:horizontal {{
    background: transparent; border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: #ffffff;
    border: 1px solid {BOR_SFT};
    width: 14px; height: 14px;
    margin: -6px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    border-color: {ACC};
}}
QSlider::handle:horizontal:pressed {{
    background: {ACC2};
    border-color: {ACC_DK};
}}

QCheckBox {{ spacing: 10px; color: {TEXT}; }}
QCheckBox::indicator {{
    width: 38px; height: 20px;
    border-radius: 10px;
    background: {CARD3};
    border: 1px solid {BOR};
}}
QCheckBox::indicator:checked {{ background: {ACC2}; border: 1px solid {ACC}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px 4px 0; }}
QScrollBar::handle:vertical {{ background: {CARD3}; min-height: 24px; border-radius: 4px; }}
QScrollBar::handle:vertical:hover {{ background: {BOR}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 4px 2px 4px; }}
QScrollBar::handle:horizontal {{ background: {CARD3}; min-width: 24px; border-radius: 4px; }}
QScrollBar::handle:horizontal:hover {{ background: {BOR}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QToolTip {{
    background: {CARD}; color: {TEXT};
    border: 1px solid {BOR}; padding: 4px 8px; border-radius: 4px;
}}

/* ── Custom Title Bar ── */
QWidget#titleBar {{
    background: {BG};
    border-bottom: none;
}}
QLabel#tbTitle {{
    font-size: {s(12)}px;
    font-weight: 400;
    color: {SUB};
}}
"""
    return raw



# ═══════════════════════════════════════════════════════════════════
#  CUSTOM TITLE BAR
# ═══════════════════════════════════════════════════════════════════
class _MacDot(QWidget):
    """Circular macOS-style window button. Symbol appears only on group hover."""
    clicked = pyqtSignal()

    _D = 12  # diameter

    def __init__(self, idle_color: str, hover_color: str, symbol: str, title_bar, parent=None):
        super().__init__(parent)
        self._idle  = QColor(idle_color)
        self._hover = QColor(hover_color)
        self._sym   = symbol
        self._bar   = title_bar
        self._pressed = False
        d = self._D + 4
        self.setFixedSize(d, d)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._idle)
        if self._pressed:
            color = color.darker(150)
        elif self.underMouse():
            color = color.darker(125)
        cx = self.width() / 2
        cy = self.height() / 2
        r  = self._D / 2
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(cx - r), int(cy - r), self._D, self._D)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(e.position().toPoint()):
                self.clicked.emit()

    def enterEvent(self, e):
        self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self.update(); super().leaveEvent(e)


class CustomTitleBar(QWidget):
    """Frameless-compatible title bar: drag, minimize, maximize/restore, close.
    Attach to the top of the main window's central widget layout."""

    _RESIZE_MARGIN = 6

    def __init__(self, window: QWidget, parent=None):
        super().__init__(parent)
        self._win = window
        self._drag_pos = None
        self._resize_edge = None
        self._hovering_controls = False

        self.setObjectName("titleBar")
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 12, 0)
        h.setSpacing(0)

        self._min_btn = _MacDot("#febc2e", "#febc2e", "\u2212", self)
        self._min_btn.setToolTip("Minimize")
        self._min_btn.clicked.connect(self._win.showMinimized)

        self._max_btn = _MacDot("#28c840", "#28c840", "\u2b0c", self)
        self._max_btn.setToolTip("Maximize")
        self._max_btn.clicked.connect(self._toggle_max)

        self._close_btn = _MacDot("#ff5f57", "#ff5f57", "\u00d7", self)
        self._close_btn.setToolTip("Close")
        self._close_btn.clicked.connect(self._win.close)

        h.addStretch(1)

        self._title_lbl = QLabel("CClip")
        self._title_lbl.setObjectName("tbTitle")
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(self._title_lbl)

        h.addStretch(1)

        dots_layout = QHBoxLayout()
        dots_layout.setContentsMargins(0, 0, 8, 0)
        dots_layout.setSpacing(8)
        dots_layout.addWidget(self._min_btn)
        dots_layout.addWidget(self._max_btn)
        dots_layout.addWidget(self._close_btn)
        h.addLayout(dots_layout)

        self.setMouseTracking(True)

    def refresh_style(self):
        self._title_lbl.setStyleSheet(f"color: {SUB};")
        self.setStyleSheet(f"QWidget#titleBar {{ background: {BG}; border-bottom: none; }}")
        for dot in (self._close_btn, self._min_btn, self._max_btn):
            dot.update()

    def _toggle_max(self):
        if self._win.isMaximized():
            self._win.showNormal()
            self._max_btn.setToolTip("Maximize")
        else:
            self._win.showMaximized()
            self._max_btn.setToolTip("Restore")

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            if self._win.isMaximized():
                self._win.showNormal()
                self._max_btn.setToolTip("Maximize")
                self._drag_pos = QPoint(self._win.width() // 2, self.height() // 2)
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None

# ═══════════════════════════════════════════════════════════════════
#  CUSTOM WIDGETS
# ═══════════════════════════════════════════════════════════════════
class Card(QFrame):
    def __init__(self, title="", subtitle="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)
        if title:
            t = QLabel(title); t.setObjectName("h3"); v.addWidget(t)
        if subtitle:
            s = QLabel(subtitle); s.setObjectName("subSm"); v.addWidget(s)
        if title or subtitle:
            line = QFrame(); line.setObjectName("hairline")
            v.addWidget(line)
        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 4, 0, 0)
        self._body.setSpacing(2)
        v.addLayout(self._body)

    def body(self):
        return self._body


class AnimatedProgressBar(QProgressBar):
    def __init__(self, parent=None, color=ACC, height=8):
        super().__init__(parent)
        self.setRange(0, 1000)
        self.setValue(0)
        self.setTextVisible(False)
        self.setFixedHeight(height)
        radius = max(2, height // 2)
        self.setStyleSheet(f"""
            QProgressBar {{ background: {CARD2}; border: none; border-radius: {radius}px; }}
            QProgressBar::chunk {{ background: {color}; border-radius: {radius}px; }}
        """)
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def animate_to(self, fraction):
        f = max(0.0, min(1.0, float(fraction)))
        target = int(f * 1000)
        if self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()
        self._anim.setStartValue(self.value())
        self._anim.setEndValue(target)
        self._anim.start()


class CountUpLabel(QLabel):
    def __init__(self, value=0, color=ACC, parent=None):
        super().__init__("0", parent)
        self.setObjectName("counterValue")
        self.setStyleSheet(f"color: {color};")
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(900)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.setText(str(int(v))))
        self.set_value_animated(value)

    def set_value_animated(self, value):
        try:
            cur = int(self.text())
        except Exception:
            cur = 0
        target = int(value or 0)
        if cur == target:
            self.setText(str(target)); return
        if self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()
        self._anim.setStartValue(cur)
        self._anim.setEndValue(target)
        self._anim.start()


class NavButton(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, key, icon_text, label, parent=None):
        super().__init__(parent)
        self.key = key
        self._checked = False
        self._hover = False
        self.setFixedHeight(40)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 0, 14, 0)
        h.setSpacing(14)
        self._icon = QLabel(icon_text)
        self._icon.setFixedWidth(22)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl = QLabel(label)
        h.addWidget(self._icon)
        h.addWidget(self._lbl)
        h.addStretch(1)
        self._update_style()

    def setChecked(self, v):
        v = bool(v)
        if self._checked == v:
            return
        self._checked = v
        self._update_style()

    def isChecked(self):
        return self._checked

    def enterEvent(self, e):
        self._hover = True
        self._update_style()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._update_style()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(e)

    def _update_style(self):
        if self._checked:
            bg = CARD3; tc = TEXT; ic = ACC; weight = "600"
        elif self._hover:
            bg = CARD3; tc = TEXT; ic = TEXT; weight = "500"
        else:
            bg = "transparent"; tc = SUB; ic = SUB; weight = "500"
        self.setStyleSheet(f"NavButton {{ background: {bg}; border-radius: 6px; }}")
        self._icon.setStyleSheet(
            f"color: {ic}; font-size: 18px; font-family: 'Segoe UI Symbol','Segoe UI Variable',sans-serif; background: transparent;"
        )
        self._lbl.setStyleSheet(
            f"color: {tc}; font-size: 13px; font-weight: {weight}; background: transparent;"
        )


class ToggleSwitch(QWidget):
    """Modern Fluent-style toggle switch with animated knob.
    Knob is a perfect solid circle — no clipping artifacts."""
    toggled = pyqtSignal(bool)

    _KNOB_OFF  = 4.0    # left resting position (px)
    _KNOB_SIZE = 18     # diameter (px)

    def __init__(self, on_color=ACC2, parent=None):
        super().__init__(parent)
        self._checked = False
        self._on_color = on_color
        self._knob_pos = self._KNOB_OFF
        self.setFixedSize(50, 26)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._anim = QPropertyAnimation(self, b"knob_pos", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)

    def isChecked(self):
        return self._checked

    def setChecked(self, v):
        v = bool(v)
        if self._checked == v:
            return
        self._checked = v
        if not self.isVisible():
            self._knob_pos = float(self.width() - self._KNOB_SIZE - self._KNOB_OFF) if v else self._KNOB_OFF
            self.update()
        else:
            self._animate()
        self.toggled.emit(v)

    def _animate(self):
        target = float(self.width() - self._KNOB_SIZE - self._KNOB_OFF) if self._checked else self._KNOB_OFF
        if self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()
        self._anim.setStartValue(float(self._knob_pos))
        self._anim.setEndValue(target)
        self._anim.start()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)

    def get_knob(self):
        return self._knob_pos

    def set_knob(self, v):
        self._knob_pos = float(v)
        self.update()

    knob_pos = pyqtProperty(float, get_knob, set_knob)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # ── Track ───────────────────────────────────────────────────
        if self._checked:
            bg_col     = QColor(self._on_color)
            border_col = QColor(self._on_color)
        else:
            bg_col     = QColor(CARD3)
            border_col = QColor(BOR)
        p.setBrush(QBrush(bg_col))
        p.setPen(QPen(border_col, 1))
        # radius=10 ensures the knob (at y=4, h=18) stays within the track
        p.drawRoundedRect(0, 0, self.width() - 1, self.height() - 1, 10, 10)
        # ── Knob shadow (slightly offset, no pen) ───────────────────
        p.setBrush(QBrush(QColor(0, 0, 0, 35)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(self._knob_pos) + 1, 5, self._KNOB_SIZE, self._KNOB_SIZE)
        # ── Knob (perfect circle, fully inside track) ────────────────
        p.setBrush(QBrush(QColor("#ffffff")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(self._knob_pos), 4, self._KNOB_SIZE, self._KNOB_SIZE)


class ToastPopup(QFrame):
    def __init__(self, parent, on_save, on_discard):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(
            f"QFrame#card {{ background: {CARD}; border: 1px solid {ACC}; border-radius: 10px; }}"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 8, 10, 8); h.setSpacing(10)
        dot = QLabel("●"); dot.setStyleSheet(f"color: {YEL}; font-size: 16px; border: none;")
        h.addWidget(dot)
        lbl = QLabel("Unsaved changes"); lbl.setObjectName("h3")
        lbl.setStyleSheet("border: none;")
        h.addWidget(lbl)
        h.addSpacing(8)
        discard_btn = QPushButton("Discard"); discard_btn.setFixedHeight(28)
        discard_btn.clicked.connect(on_discard)
        h.addWidget(discard_btn)
        save_btn = QPushButton("Save"); save_btn.setObjectName("primary"); save_btn.setFixedHeight(28)
        save_btn.clicked.connect(on_save)
        h.addWidget(save_btn)
        self.adjustSize()
        self._shown = False
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(260)
        self.hide()

    def slide_in(self):
        parent = self.parentWidget()
        if not parent:
            return
        self.adjustSize()
        # Bottom-right of the main content area, left of the right sidebar clock panel.
        RIGHT_SIDEBAR_W = 266  # matches self.right_bar.setFixedWidth(266)
        right_edge = parent.width() - RIGHT_SIDEBAR_W - 20
        end_pos = QPoint(max(24, right_edge - self.width()), parent.height() - self.height() - 24)
        if self._shown:
            self.move(end_pos); return
        self._shown = True
        start_pos = QPoint(end_pos.x(), parent.height() + 20)
        self.move(start_pos)
        self.show(); self.raise_()
        self._anim.stop()
        try: self._anim.finished.disconnect()
        except TypeError: pass
        self._anim.setStartValue(start_pos)
        self._anim.setEndValue(end_pos)
        self._anim.start()

    def slide_out(self):
        if not self._shown:
            return
        parent = self.parentWidget()
        if not parent:
            self.hide(); self._shown = False; return
        start_pos = self.pos()
        end_pos = QPoint(start_pos.x(), parent.height() + 20)
        self._anim.stop()
        try: self._anim.finished.disconnect()
        except TypeError: pass
        self._anim.setStartValue(start_pos)
        self._anim.setEndValue(end_pos)
        def done():
            self.hide(); self._shown = False
        self._anim.finished.connect(done)
        self._anim.start()


# ═══════════════════════════════════════════════════════════════════
#  THREAD WRAPPERS
# ═══════════════════════════════════════════════════════════════════
class ModernComboBox(QComboBox):
    """QComboBox with a stable, theme-painted chevron overlay.
    Popup has NO drop-shadow and slides in with a fade+translate animation.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMinimumHeight(36)
        self.setFrame(False)
        # Remove blue focus line on Windows
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Hide native arrow indicator
        self.setStyleSheet("""
            QComboBox { outline: none; border: none; padding-right: 8px; }
            QComboBox::drop-down { width: 0px; border: none; background: transparent; }
            QComboBox::down-arrow { image: none; width: 0px; height: 0px; border: none; }
        """)

    def showPopup(self):
        # Remove drop-shadow before popup appears
        view = self.view()
        win = view.window()
        win.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        # Fix white background around popup
        win.setStyleSheet(f"background: {CARD}; border: none;")
        super().showPopup()
        # Fade-in animation on the popup
        popup_win = view.window()
        try:
            popup_win.setWindowOpacity(0.0)
            anim = QPropertyAnimation(popup_win, b"windowOpacity", self)
            anim.setDuration(180)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.start()
            self._popup_fade_anim = anim
        except Exception:
            pass
    def hidePopup(self):
        super().hidePopup()


class FontOptionRow(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, family, active=False, available=True, parent=None):
        super().__init__(parent)
        self._family = family
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(40)
        self.setStyleSheet(self._style(active))

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 14, 0)
        h.setSpacing(12)

        name = QLabel(family)
        name_col = "#ffffff" if active else (TEXT if available else SUB_DK)
        name.setStyleSheet(f"background: transparent; border: none; color: {name_col}; font-family: '{family}'; font-size: 13px;")
        h.addWidget(name, 0)

        sample = QLabel("Lorem ipsum")
        sample.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        sample_col = "#ffffff" if active else (TEXT if available else SUB_DK)
        sample.setStyleSheet(f"background: transparent; border: none; color: {sample_col}; font-family: '{family}'; font-size: 18px;")
        if not available:
            sample.setToolTip("Font is not installed on this Windows account, so Qt previews it with a fallback.")
        h.addWidget(sample, 1)

        if not available:
            tag = QLabel("not installed")
            tag.setStyleSheet(
                f"background: {CARD2}; color: {SUB_DK}; border: 1px solid {BOR_SFT}; "
                f"border-radius: 5px; padding: 2px 6px; font-size: 10px;"
            )
            h.addWidget(tag)

    def _style(self, active=False):
        if active:
            return (
                f"QFrame {{ background: {ACC2}; color: #ffffff; border: none; border-radius: 6px; }}"
                f"QLabel {{ color: #ffffff; }}"
            )
        return (
            f"QFrame {{ background: transparent; color: {TEXT}; border: none; border-radius: 6px; }}"
            f"QFrame:hover {{ background: {CARD3}; }}"
            f"QLabel {{ color: {TEXT}; }}"
        )

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._family)
        super().mousePressEvent(e)


class BatchThread(QThread):
    progress = pyqtSignal(int, int, str)
    macro    = pyqtSignal(int, int, str)
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


class UploadThread(QThread):
    finished_ok = pyqtSignal(bool)
    def run(self):
        run_youtube_upload(lambda ok: self.finished_ok.emit(bool(ok)))


class StatsThread(QThread):
    def run(self):
        _stats_worker()


class ScanThread(QThread):
    done = pyqtSignal(list)
    def run(self):
        self.done.emit(check_copyright(S["out_folder"]))


class AuthThread(QThread):
    success = pyqtSignal()
    error   = pyqtSignal(str)

    def run(self):
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request as GRequest
            from google_auth_oauthlib.flow import InstalledAppFlow

            SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
            creds = None
            if os.path.exists("token.json"):
                creds = Credentials.from_authorized_user_file("token.json", SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(GRequest())
                else:
                    if not os.path.exists("client_secrets.json"):
                        self.error.emit("client_secrets.json not found!")
                        return
                    flow = InstalledAppFlow.from_client_secrets_file("client_secrets.json", SCOPES)
                    creds = flow.run_local_server(port=0)
                with open("user_data/token.json", "w") as tf:
                    tf.write(creds.to_json())
            self.success.emit()
        except Exception as e:
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════
#  MODERN WIDGETS (in-app dialogs, color picker, font picker, calendar)
# ═══════════════════════════════════════════════════════════════════
class _OverlayShade(QWidget):
    """Semi-opaque backdrop drawn behind modal dialogs. Lives in the main window."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: rgba(0, 0, 0, 130);")
        self.hide()


class ModernDialog(QDialog):
    """Frameless, rounded, in-app modal. Returns the role string of the clicked button."""

    def __init__(self, parent, title, message, icon_char="\u26A0",
                 icon_color=None, buttons=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self._clicked = None

        outer = QFrame(self)
        outer.setObjectName("modernDialogFrame")
        outer.setStyleSheet(
            f"QFrame#modernDialogFrame {{ background: {CARD}; "
            f"border: 1px solid {BOR}; border-radius: 14px; }}"
            f"QLabel {{ background: transparent; border: none; color: {TEXT}; }}"
        )
        shadow = QGraphicsDropShadowEffect(outer)
        shadow.setBlurRadius(48); shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 200))
        outer.setGraphicsEffect(shadow)

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 30, 30, 30)
        root.addWidget(outer)

        v = QVBoxLayout(outer)
        v.setContentsMargins(26, 22, 26, 18); v.setSpacing(14)

        head = QHBoxLayout(); head.setSpacing(14)
        ic = icon_color or YEL
        icon_lbl = QLabel("!" if icon_char == "\u26A0" else icon_char)
        icon_lbl.setFixedSize(50, 50)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(
            f"background: {ic}22; color: {ic}; font-size: 24px; "
            f"font-weight: 700; border: 1px solid {ic}55; border-radius: 25px;"
        )
        head.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        tcol = QVBoxLayout(); tcol.setSpacing(4)
        t = QLabel(title)
        t.setStyleSheet(f"color: {TEXT}; font-size: 17px; font-weight: 600; "
                        f"background: transparent; border: none;")
        tcol.addWidget(t)
        if message:
            m = QLabel(message); m.setWordWrap(True)
            m.setStyleSheet(f"color: {SUB}; font-size: 13px; "
                            f"background: transparent; border: none;")
            tcol.addWidget(m)
        head.addLayout(tcol, 1)
        v.addLayout(head)

        bh = QHBoxLayout(); bh.setSpacing(8); bh.addStretch(1)
        for (label, role, is_primary) in (buttons or [("OK", "ok", True)]):
            b = QPushButton(label)
            if is_primary:
                b.setObjectName("primary")
            b.setFixedHeight(34); b.setMinimumWidth(110)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.clicked.connect(lambda _=False, r=role: self._on_pick(r))
            bh.addWidget(b)
        v.addLayout(bh)

        # fade-in
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(160); self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.setStartValue(0.0); self._fade.setEndValue(1.0)

    def _on_pick(self, role):
        self._clicked = role
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        # centre on parent
        p = self.parent()
        if p is not None:
            pw = p.window()
            if pw is not None:
                geo = pw.geometry()
                self.adjustSize()
                cx = geo.x() + (geo.width() - self.width()) // 2
                cy = geo.y() + (geo.height() - self.height()) // 2
                self.move(cx, cy)
        self._fade.start()

    @staticmethod
    def ask(parent, title, message, buttons,
            icon_char="\u26A0", icon_color=None):
        dlg = ModernDialog(parent, title, message, icon_char, icon_color, buttons)
        dlg.exec()
        return dlg._clicked


class HSVPlane(QWidget):
    """Saturation / Value square used inside the custom color picker."""
    colorChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(338, 338)
        self._hue = 0
        self._sat = 255
        self._val = 255
        self._dragging = False
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def color(self):
        return QColor.fromHsv(max(0, self._hue), self._sat, self._val)

    def setHue(self, h):
        self._hue = max(0, int(h)); self.update(); self.colorChanged.emit()

    def setColor(self, col: QColor):
        h, s, v, _ = col.getHsv()
        self._hue = max(0, h); self._sat = s; self._val = v
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width(); h = self.height()
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 8, 8)
        p.setClipPath(path)
        p.fillPath(path, QBrush(QColor.fromHsv(max(0, self._hue), 255, 255)))
        white = QLinearGradient(0, 0, w, 0)
        white.setColorAt(0, QColor(255, 255, 255, 255))
        white.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillPath(path, QBrush(white))
        black = QLinearGradient(0, 0, 0, h)
        black.setColorAt(0, QColor(0, 0, 0, 0))
        black.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillPath(path, QBrush(black))
        p.setClipping(False)
        p.setPen(QPen(QColor("#2f2f2f"), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        # marker
        mx = int(self._sat / 255 * (w - 1))
        my = int((1 - self._val / 255) * (h - 1))
        mx = max(8, min(w - 9, mx))
        my = max(8, min(h - 9, my))
        p.setPen(QPen(QColor("black"), 1))
        p.drawEllipse(mx - 7, my - 7, 14, 14)
        p.setPen(QPen(QColor("white"), 2))
        p.drawEllipse(mx - 6, my - 6, 12, 12)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_pos(e.pos())

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._update_pos(e.pos())

    def mouseReleaseEvent(self, _e):
        self._dragging = False

    def _update_pos(self, pt):
        x = max(0, min(self.width() - 1, pt.x()))
        y = max(0, min(self.height() - 1, pt.y()))
        self._sat = int(255 * x / max(1, self.width() - 1))
        self._val = int(255 * (1 - y / max(1, self.height() - 1)))
        self.update(); self.colorChanged.emit()


class HueBar(QWidget):
    """Vertical hue strip."""
    hueChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 338)
        self._hue = 0
        self._dragging = False
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def setHue(self, h):
        self._hue = max(0, int(h)); self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width(); h = self.height()
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 8, 8)
        grad = QLinearGradient(0, 0, 0, h)
        for stop, hue in (
            (0.0, 0), (1/6, 60), (2/6, 120),
            (3/6, 180), (4/6, 240), (5/6, 300), (1.0, 359)
        ):
            grad.setColorAt(stop, QColor.fromHsv(hue, 255, 255))
        p.fillPath(path, QBrush(grad))
        p.setPen(QPen(QColor("#2f2f2f"), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        py = int(self._hue / 359 * (h - 1)) if h > 1 else 0
        py = max(4, min(h - 5, py))
        p.setPen(QPen(QColor("black"), 1))
        p.drawRect(0, py - 3, w - 1, 6)
        p.setPen(QPen(QColor("white"), 2))
        p.drawRect(1, py - 2, w - 3, 4)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True; self._update_pos(e.pos().y())

    def mouseMoveEvent(self, e):
        if self._dragging: self._update_pos(e.pos().y())

    def mouseReleaseEvent(self, _e):
        self._dragging = False

    def _update_pos(self, y):
        y = max(0, min(self.height() - 1, y))
        self._hue = int(359 * y / max(1, self.height() - 1))
        self.update(); self.hueChanged.emit(self._hue)


class ModernColorPicker(QDialog):
    """Beautiful, modern in-app color picker.
    Layout: HSV square | Hue bar | Right panel (preview + inputs + presets).
    Dark glass-morphism style with subtle gradients and clean typography.
    """

    # Preset swatches shown at the bottom for quick access
    PRESETS = [
        "#FF6B6B", "#FF9F43", "#FBBF24", "#6CCB5F", "#4FD1C5",
        "#60CDFF", "#4CC2FF", "#0078D4", "#A855F7", "#EC4899",
        "#FFFFFF", "#B0B0B0", "#5C5C5C", "#1A1A1A", "#000000",
    ]

    def __init__(self, initial, parent=None, title="Pick a Color"):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self._result = None

        start_col = QColor(initial) if QColor(initial).isValid() else QColor("#ffffff")
        self._initial_hex = start_col.name().upper()

        # ── Outer frame ─────────────────────────────────────────────
        outer = QFrame(self)
        outer.setObjectName("mcpOuter")
        outer.setStyleSheet("""
            QFrame#mcpOuter {
                background: qlineargradient(x1:0,y1:0, x2:0,y2:1,
                    stop:0 #3a3a3a, stop:1 #2b2b2b);
                border: 1px solid #555555;
                border-radius: 14px;
            }
            QLabel {
                background: transparent; border: none;
                color: #e8e8e8; font-size: 12px;
            }
            QLineEdit {
                background: #252525;
                color: #f0f0f0;
                border: 1px solid #505050;
                border-radius: 6px;
                padding: 4px 8px;
                font-family: 'Cascadia Mono', Consolas, monospace;
                font-size: 12px;
            }
            QLineEdit:focus { border-color: #60cdff; }
            QPushButton {
                background: #444444;
                color: #f0f0f0;
                border: 1px solid #606060;
                border-radius: 8px;
                padding: 7px 20px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover { background: #555555; border-color: #80cbff; }
            QPushButton#mcpOK {
                background: #0078d4;
                border-color: #0078d4;
                color: white;
            }
            QPushButton#mcpOK:hover { background: #005a9e; }
        """)
        shadow = QGraphicsDropShadowEffect(outer)
        shadow.setBlurRadius(60); shadow.setOffset(0, 16)
        shadow.setColor(QColor(0, 0, 0, 180))
        outer.setGraphicsEffect(shadow)

        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(30, 30, 30, 30)
        root_lay.addWidget(outer)

        main = QVBoxLayout(outer)
        main.setContentsMargins(18, 16, 18, 16)
        main.setSpacing(14)

        # ── Title bar ───────────────────────────────────────────────
        title_row = QHBoxLayout(); title_row.setSpacing(8)
        ic = QLabel("🎨")
        ic.setStyleSheet("font-size: 16px; background: transparent; border: none;")
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "font-size: 15px; font-weight: 700; color: #ffffff; "
            "background: transparent; border: none;"
        )
        title_row.addWidget(ic)
        title_row.addWidget(title_lbl, 1)
        main.addLayout(title_row)

        # ── Thin separator ──────────────────────────────────────────
        sep = QFrame(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: #505050; border: none;")
        main.addWidget(sep)

        # ── Color picker body ────────────────────────────────────────
        body = QHBoxLayout(); body.setSpacing(14)

        # HSV plane + hue bar (left side)
        left_col = QVBoxLayout(); left_col.setSpacing(10)
        picker_row = QHBoxLayout(); picker_row.setSpacing(8)
        self.plane = HSVPlane()
        self.bar   = HueBar()
        picker_row.addWidget(self.plane)
        picker_row.addWidget(self.bar)
        left_col.addLayout(picker_row)
        body.addLayout(left_col)

        # Right panel
        right = QVBoxLayout(); right.setSpacing(10)
        right.setContentsMargins(0, 0, 0, 0)

        # ── Color preview (new / current) ───────────────────────────
        preview_frame = QFrame()
        preview_frame.setFixedHeight(62)
        preview_frame.setStyleSheet(
            "QFrame { border-radius: 10px; border: 1px solid #555; }"
        )
        pv_lay = QVBoxLayout(preview_frame)
        pv_lay.setContentsMargins(0, 0, 0, 0); pv_lay.setSpacing(0)
        self._new_sw  = QFrame(); self._new_sw.setFixedHeight(31)
        self._old_sw  = QFrame(); self._old_sw.setFixedHeight(31)
        self._new_sw.setStyleSheet(
            "border-radius: 9px 9px 0 0; border-bottom: 1px solid #555;"
        )
        self._old_sw.setStyleSheet("border-radius: 0 0 9px 9px;")
        pv_lay.addWidget(self._new_sw)
        pv_lay.addWidget(self._old_sw)

        # Labels row below preview
        lbl_row = QHBoxLayout()
        nl = QLabel("New"); nl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        nl.setStyleSheet("color: #909090; font-size: 10px; background: transparent; border:none;")
        cl = QLabel("Current"); cl.setAlignment(Qt.AlignmentFlag.AlignRight)
        cl.setStyleSheet("color: #909090; font-size: 10px; background: transparent; border:none;")
        lbl_row.addWidget(nl); lbl_row.addWidget(cl)

        right.addWidget(preview_frame)
        right.addLayout(lbl_row)

        # ── Hex input ────────────────────────────────────────────────
        hex_frame = QFrame()
        hex_frame.setStyleSheet(
            "QFrame { background: #252525; border: 1px solid #505050; border-radius: 8px; }"
            "QLabel { color: #909090; font-size: 12px; }"
        )
        hex_h = QHBoxLayout(hex_frame)
        hex_h.setContentsMargins(10, 6, 10, 6); hex_h.setSpacing(4)
        hash_lbl = QLabel("#")
        hash_lbl.setStyleSheet("color: #909090; font-size: 13px; font-weight: 600; background: transparent; border: none;")
        hash_lbl.setFixedWidth(14)
        self._hex_in = QLineEdit(start_col.name().upper().lstrip("#"))
        self._hex_in.setStyleSheet(
            "background: transparent; border: none; color: #f0f0f0; "
            "font-family: 'Cascadia Mono', Consolas, monospace; font-size: 14px; font-weight: 600;"
        )
        hex_h.addWidget(hash_lbl)
        hex_h.addWidget(self._hex_in, 1)
        right.addWidget(hex_frame)

        # ── HSV + RGB inputs in a 2-column grid ─────────────────────
        inputs_frame = QFrame()
        inputs_frame.setStyleSheet(
            "QFrame { background: #2a2a2a; border: 1px solid #484848; border-radius: 8px; }"
        )
        grid_lay = QHBoxLayout(inputs_frame)
        grid_lay.setContentsMargins(10, 8, 10, 8); grid_lay.setSpacing(14)

        def input_col(labels_vals):
            col = QVBoxLayout(); col.setSpacing(5)
            widgets = []
            for lbl_txt, ed in labels_vals:
                row = QHBoxLayout(); row.setSpacing(5)
                L = QLabel(lbl_txt)
                L.setFixedWidth(18)
                L.setStyleSheet(
                    "color: #888888; font-size: 11px; font-weight: 600; "
                    "background: transparent; border: none;"
                )
                ed.setFixedWidth(52)
                ed.setFixedHeight(26)
                row.addWidget(L); row.addWidget(ed)
                col.addLayout(row)
                widgets.append(ed)
            return col, widgets

        self._h_in = QLineEdit(); self._s_in = QLineEdit(); self._v_in = QLineEdit()
        self._r_in = QLineEdit(); self._g_in = QLineEdit(); self._b_in = QLineEdit()

        hsv_col, _ = input_col([("H", self._h_in), ("S", self._s_in), ("V", self._v_in)])
        rgb_col, _ = input_col([("R", self._r_in), ("G", self._g_in), ("B", self._b_in)])

        div = QFrame(); div.setFixedWidth(1)
        div.setStyleSheet("background: #484848; border: none;")

        grid_lay.addLayout(hsv_col)
        grid_lay.addWidget(div)
        grid_lay.addLayout(rgb_col)

        right.addWidget(inputs_frame)

        # ── Preset swatches ──────────────────────────────────────────
        sw_title = QLabel("Presets")
        sw_title.setStyleSheet(
            "color: #888888; font-size: 10px; font-weight: 600; letter-spacing: 0.8px; "
            "background: transparent; border: none;"
        )
        right.addWidget(sw_title)

        sw_grid = QWidget()
        sw_grid_lay = QHBoxLayout(sw_grid)
        sw_grid_lay.setContentsMargins(0, 0, 0, 0); sw_grid_lay.setSpacing(4)
        for hex_c in self.PRESETS[:8]:   # first row
            sw = QFrame(); sw.setFixedSize(22, 22)
            sw.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            sw.setStyleSheet(
                f"QFrame {{ background: {hex_c}; border-radius: 5px; border: 1px solid rgba(255,255,255,20); }}"
                f"QFrame:hover {{ border: 2px solid #ffffff; }}"
            )
            def make_click(hc):
                def on_click(e=None):
                    c = QColor(hc)
                    if c.isValid():
                        self.plane.setColor(c)
                        self.bar.setHue(c.hue() if c.hue() >= 0 else 0)
                        self._sync(c)
                sw.mousePressEvent = lambda e, h=hc: on_click()
                return on_click
            make_click(hex_c)
            sw_grid_lay.addWidget(sw)
        sw_grid_lay.addStretch(1)
        right.addWidget(sw_grid)

        sw_grid2 = QWidget()
        sw_grid2_lay = QHBoxLayout(sw_grid2)
        sw_grid2_lay.setContentsMargins(0, 0, 0, 0); sw_grid2_lay.setSpacing(4)
        for hex_c in self.PRESETS[8:]:   # second row
            sw = QFrame(); sw.setFixedSize(22, 22)
            sw.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            sw.setStyleSheet(
                f"QFrame {{ background: {hex_c}; border-radius: 5px; border: 1px solid rgba(255,255,255,20); }}"
                f"QFrame:hover {{ border: 2px solid #ffffff; }}"
            )
            def make_click2(hc):
                def on_click(e=None):
                    c = QColor(hc)
                    if c.isValid():
                        self.plane.setColor(c)
                        self.bar.setHue(c.hue() if c.hue() >= 0 else 0)
                        self._sync(c)
                sw.mousePressEvent = lambda e, h=hc: on_click()
                return on_click
            make_click2(hex_c)
            sw_grid2_lay.addWidget(sw)
        sw_grid2_lay.addStretch(1)
        right.addWidget(sw_grid2)

        right.addStretch(1)

        # ── OK / Cancel buttons ──────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.setFixedSize(100, 36)
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply"); ok.setObjectName("mcpOK"); ok.setFixedSize(100, 36)
        ok.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ok.clicked.connect(self._on_ok)
        btn_row.addWidget(cancel); btn_row.addWidget(ok)
        right.addLayout(btn_row)

        body.addLayout(right)
        main.addLayout(body)

        # ── Wire signals ─────────────────────────────────────────────
        self.plane.colorChanged.connect(lambda: self._sync(self.plane.color()))
        self.bar.hueChanged.connect(lambda h: (self.plane.setHue(h), self._sync(self.plane.color())))
        self._hex_in.editingFinished.connect(self._on_hex)
        for ed in (self._h_in, self._s_in, self._v_in):
            ed.editingFinished.connect(self._on_hsv)
        for ed in (self._r_in, self._g_in, self._b_in):
            ed.editingFinished.connect(self._on_rgb)

        self.plane.setColor(start_col)
        self.bar.setHue(start_col.hue() if start_col.hue() >= 0 else 0)
        self._sync(start_col)

        # Fade-in on open
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(180); self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.setStartValue(0.0); self._fade.setEndValue(1.0)

    def showEvent(self, e):
        super().showEvent(e)
        p = self.parent()
        if p is not None:
            pw = p.window()
            if pw is not None:
                geo = pw.geometry()
                self.adjustSize()
                cx = geo.x() + (geo.width() - self.width()) // 2
                cy = geo.y() + (geo.height() - self.height()) // 2
                self.move(cx, cy)
        self._fade.start()

    def _sync(self, col: QColor, skip_inputs=False):
        hex_ = col.name().upper()
        self._new_sw.setStyleSheet(
            f"background: {hex_}; border-radius: 9px 9px 0 0; "
            f"border: 1px solid rgba(255,255,255,15); border-bottom: 1px solid #444;"
        )
        self._old_sw.setStyleSheet(
            f"background: {self._initial_hex}; border-radius: 0 0 9px 9px; "
            f"border: 1px solid rgba(255,255,255,15); border-top: none;"
        )
        if not skip_inputs:
            h, s, v, _ = col.getHsv()
            for ed, val in ((self._hex_in, hex_),
                            (self._h_in, str(max(0, h))),
                            (self._s_in, str(round(s / 255 * 100))),
                            (self._v_in, str(round(v / 255 * 100))),
                            (self._r_in, str(col.red())),
                            (self._g_in, str(col.green())),
                            (self._b_in, str(col.blue()))):
                ed.blockSignals(True); ed.setText(val.lstrip("#")); ed.blockSignals(False)

    def _on_hex(self):
        raw = self._hex_in.text().strip()
        c = QColor(raw if raw.startswith("#") else f"#{raw}")
        if c.isValid():
            self.plane.setColor(c)
            self.bar.setHue(c.hue() if c.hue() >= 0 else 0)
            self._sync(c)

    def _on_hsv(self):
        try:
            h = max(0, min(359, int(self._h_in.text().strip())))
            s = max(0, min(100, int(self._s_in.text().strip())))
            v = max(0, min(100, int(self._v_in.text().strip())))
        except ValueError:
            return
        c = QColor.fromHsv(h, round(s / 100 * 255), round(v / 100 * 255))
        self.plane.setColor(c)
        self.bar.setHue(h)
        self._sync(c)

    def _on_rgb(self):
        try:
            r = max(0, min(255, int(self._r_in.text().strip())))
            g = max(0, min(255, int(self._g_in.text().strip())))
            b = max(0, min(255, int(self._b_in.text().strip())))
        except ValueError:
            return
        c = QColor(r, g, b)
        self.plane.setColor(c)
        self.bar.setHue(c.hue() if c.hue() >= 0 else 0)
        self._sync(c)

    def _on_ok(self):
        self._result = self.plane.color()
        self.accept()

    @staticmethod
    def pick(parent, initial_hex, title="Pick a Color"):
        dlg = ModernColorPicker(initial_hex, parent, title)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg._result is not None:
            return dlg._result
        return None


class FontPicker(QWidget):
    """Compact pill-shaped button that reveals an animated fade+slide popup
    listing fonts rendered in themselves.  Arrow flips on open/close."""
    fontChanged = pyqtSignal(str)

    def __init__(self, fonts, current, width=220, parent=None):
        super().__init__(parent)
        self._fonts = [f for f in fonts]
        if current and current not in self._fonts:
            self._fonts.insert(0, current)
        self._current = current or (self._fonts[0] if self._fonts else "Segoe UI")
        self._popup = None
        self._installed_fonts = set(QFontDatabase.families())
        self._open = False
        self.setFixedSize(width, 36)

        self._btn = QPushButton(self)
        self._btn.setGeometry(0, 0, width, 36)
        self._btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn.setFlat(True)
        self._btn.clicked.connect(self._toggle_popup)
        self._arrow = QLabel("\u25BE", self)
        self._arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._arrow.setFont(QFont(S.get("ui_font", "Segoe UI Variable"), 11))
        self._arrow.setGeometry(width - 26, 1, 22, 34)
        self._refresh_btn()

    def current(self):
        return self._current

    def setCurrent(self, fam):
        self._current = fam
        if fam not in self._fonts:
            self._fonts.insert(0, fam)
        self._refresh_btn()

    def _refresh_btn(self):
        self._btn.setText(f"  {self._current}")
        if self._current in self._installed_fonts:
            self._btn.setFont(QFont(self._current, 11))
        else:
            self._btn.setFont(QFont(S.get("ui_font", "Segoe UI Variable"), 11))
        self._btn.setStyleSheet(
            f"QPushButton {{ background: {CARD2}; color: {TEXT}; "
            f"border: none; border-radius: 10px; "
            f"padding: 6px 28px 6px 10px; text-align: left; }}"
            f"QPushButton:hover {{ border-color: {ACC}; background: {CARD3}; }}"
        )
        arrow_col = ACC if self._open else SUB
        self._arrow.setStyleSheet(
            f"background: transparent; border: none; color: {arrow_col}; font-weight: 700;"
        )
        self._arrow.raise_()

    def _toggle_popup(self):
        if self._popup is not None and self._popup.isVisible():
            self._close_popup()
            return
        self._show_popup()

    def _close_popup(self):
        if self._popup:
            old = self._popup
            self._popup = None
            self._open = False
            self._arrow.setText("\u25BE")
            self._refresh_btn()
            old.close()
            old.deleteLater()

    def _show_popup(self):
        win = self.window()
        pop = QDialog(win)
        pop.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        # Fix white background around popup
        pop.setStyleSheet(f"background: {CARD}; border: none;")
        self._popup = pop
        self._open = True
        self._arrow.setText("\u25B4")
        self._refresh_btn()
        pop.destroyed.connect(lambda: (setattr(self, "_popup", None),
                                       setattr(self, "_open", False)))

        outer = QFrame(pop); outer.setObjectName("fpFrame")
        outer.setStyleSheet(
            f"QFrame#fpFrame {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 10px; }}"
        )

        wrap = QVBoxLayout(pop); wrap.setContentsMargins(0, 0, 0, 0); wrap.addWidget(outer)

        v = QVBoxLayout(outer); v.setContentsMargins(6, 6, 6, 6); v.setSpacing(4)

        scroll = QScrollArea(); scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}")
        inner = QWidget(); inner.setStyleSheet("background: transparent;")
        iv = QVBoxLayout(inner); iv.setContentsMargins(2, 2, 2, 2); iv.setSpacing(2)

        for f in self._fonts:
            is_active = (f == self._current)
            btn = FontOptionRow(f, active=is_active, available=f in self._installed_fonts)

            def mk(ff):
                def _pick(_ff=None):
                    self._current = ff
                    self._open = False
                    self._arrow.setText("\u25BE")
                    self._refresh_btn()
                    if self._popup:
                        pop_to_close = self._popup
                        self._popup = None
                        pop_to_close.close()
                        pop_to_close.deleteLater()
                    QTimer.singleShot(0, lambda: self.fontChanged.emit(ff))
                return _pick
            btn.clicked.connect(mk(f))
            iv.addWidget(btn)

        iv.addStretch(1)
        scroll.setWidget(inner)
        v.addWidget(scroll)

        pop.resize(self.width() + 10, min(304, len(self._fonts) * 42 + 20))
        gp = self.mapToGlobal(QPoint(0, self.height() + 2))

        # Screen boundary clamping
        screen = QApplication.primaryScreen().availableGeometry()
        x = max(screen.x(), min(gp.x(), screen.x() + screen.width() - pop.width()))
        y = gp.y()
        if y + pop.height() > screen.y() + screen.height():
            y = self.mapToGlobal(QPoint(0, -pop.height() - 2)).y()
        y = max(screen.y(), y)

        # Slide-in animation from slightly below
        start_pos = QPoint(x, y + 10)
        end_pos   = QPoint(x, y)
        pop.move(start_pos)
        pop.setWindowOpacity(0.0)
        pop.show()

        fade = QPropertyAnimation(pop, b"windowOpacity", pop)
        fade.setDuration(200); fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(0.0); fade.setEndValue(1.0)

        slide = QPropertyAnimation(pop, b"pos", pop)
        slide.setDuration(200); slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide.setStartValue(start_pos); slide.setEndValue(end_pos)

        fade.start(); slide.start()
        pop._fp_fade = fade; pop._fp_slide = slide


class ModernCalendar(QWidget):
    """Themed, minimal monthly calendar used inside the clock popup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from datetime import date as _date
        today = _date.today()
        self._today = today
        self._selected = today
        self._view_month = today.replace(day=1)
        self._build()

    def _build(self):
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)

        head = QWidget(); hh = QHBoxLayout(head); hh.setContentsMargins(0, 0, 0, 0); hh.setSpacing(8)
        nav_style = (f"QPushButton {{ background: {CARD2}; color: {TEXT}; "
                     f"border: 1px solid {BOR_SFT}; border-radius: 16px; font-size: 15px; "
                     f"font-weight: 600; }}"
                     f"QPushButton:hover {{ background: {CARD3}; border-color: {ACC}; }}")
        self._prev_btn = QPushButton("\u2039"); self._prev_btn.setFixedSize(32, 32)
        self._prev_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._prev_btn.setStyleSheet(nav_style)
        self._prev_btn.clicked.connect(lambda: self._nav(-1))

        self._title_lbl = QLabel()
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setStyleSheet(
            f"color: {TEXT}; font-size: 14px; font-weight: 600; "
            f"background: transparent; border: none;"
        )

        self._next_btn = QPushButton("\u203A"); self._next_btn.setFixedSize(32, 32)
        self._next_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._next_btn.setStyleSheet(nav_style)
        self._next_btn.clicked.connect(lambda: self._nav(1))

        hh.addWidget(self._prev_btn)
        hh.addWidget(self._title_lbl, 1)
        hh.addWidget(self._next_btn)
        v.addWidget(head)

        wd = QWidget(); wh = QHBoxLayout(wd); wh.setContentsMargins(0, 0, 0, 0); wh.setSpacing(4)
        for name in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
            L = QLabel(name); L.setAlignment(Qt.AlignmentFlag.AlignCenter)
            L.setStyleSheet(
                f"color: {SUB}; font-size: 11px; font-weight: 600; "
                f"background: transparent; border: none;"
            )
            wh.addWidget(L, 1)
        v.addWidget(wd)

        self._grid_host = QWidget()
        self._grid_v = QVBoxLayout(self._grid_host)
        self._grid_v.setContentsMargins(0, 0, 0, 0); self._grid_v.setSpacing(4)
        v.addWidget(self._grid_host)

        self._render()

    def _nav(self, delta):
        from datetime import date as _date
        y, m = self._view_month.year, self._view_month.month
        m += delta
        if m < 1:
            m = 12; y -= 1
        elif m > 12:
            m = 1; y += 1
        self._view_month = _date(y, m, 1)
        self._render()

    def _render(self):
        from calendar import monthrange
        from datetime import date as _date

        while self._grid_v.count():
            item = self._grid_v.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        names = ["January", "February", "March", "April", "May", "June",
                 "July", "August", "September", "October", "November", "December"]
        y, m = self._view_month.year, self._view_month.month
        self._title_lbl.setText(f"{names[m-1]} {y}")

        first_wd, ndays = monthrange(y, m)
        day = 1
        for row in range(6):
            rw = QWidget(); rh = QHBoxLayout(rw)
            rh.setContentsMargins(0, 0, 0, 0); rh.setSpacing(4)
            for col in range(7):
                if row == 0 and col < first_wd:
                    spacer = QLabel(""); spacer.setFixedHeight(34)
                    spacer.setStyleSheet("background: transparent; border: none;")
                    rh.addWidget(spacer, 1)
                elif day > ndays:
                    spacer = QLabel(""); spacer.setFixedHeight(34)
                    spacer.setStyleSheet("background: transparent; border: none;")
                    rh.addWidget(spacer, 1)
                else:
                    d = _date(y, m, day)
                    is_today = (d == self._today)
                    is_sel = (d == self._selected)
                    b = QPushButton(str(day))
                    b.setFixedHeight(34)
                    b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                    if is_today:
                        base = (f"background: {ACC2}; color: #ffffff; border: none; "
                                f"border-radius: 17px; font-weight: 600;")
                        hover = f"background: {ACC_DK};"
                    elif is_sel:
                        base = (f"background: {CARD3}; color: {TEXT}; border: 2px solid {ACC}; "
                                f"border-radius: 17px; font-weight: 600;")
                        hover = f"background: {CARD3};"
                    else:
                        base = (f"background: transparent; color: {TEXT}; border: none; "
                                f"border-radius: 17px;")
                        hover = f"background: {CARD3};"
                    b.setStyleSheet(
                        f"QPushButton {{ {base} }}"
                        f"QPushButton:hover {{ {hover} }}"
                    )

                    def mk(dd):
                        def _pick():
                            self._selected = dd
                            self._render()
                        return _pick
                    b.clicked.connect(mk(d))
                    rh.addWidget(b, 1)
                    day += 1
            self._grid_v.addWidget(rw)


# ═══════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
#  MAIN WINDOW (EXTRACTED TO ui/main_window.py)
# ═══════════════════════════════════════════════════════════════════
from ui.main_window import ClipMasterApp

class SplashScreen(QWidget):
    """Fluid cinematic splash with proper layered effects architecture."""

    finished = pyqtSignal()

    def __init__(self, video_path, music_path=None, parent=None):
        super().__init__(parent)

        # Main window: transparent, no effects, no custom paint
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Shadow padding: blurRadius=40 on all sides + offset_y=12 extra at bottom
        _pad = 48
        self.setFixedSize(960 + _pad * 2, 540 + _pad + _pad + 12)
        self._center_on_screen()

        self._exiting = False

        # Container widget (outer - holds shadow)
        self._outer = QWidget(self)
        self._outer.setGeometry(_pad, _pad, 960, 540)

        # Shadow effect on outer container
        self._shadow = QGraphicsDropShadowEffect(self._outer)
        self._shadow.setBlurRadius(40)
        self._shadow.setColor(QColor(0, 0, 0, 100))
        self._shadow.setOffset(0, 12)
        self._outer.setGraphicsEffect(self._shadow)

        # FX stack (inner - holds opacity + scale animation target)
        # Start at 82% scale (animation start value) to avoid full-size flash
        _s0 = 0.82
        _w0, _h0 = int(960 * _s0), int(540 * _s0)
        _x0, _y0 = (960 - _w0) // 2, (540 - _h0) // 2
        self._fx_stack = QWidget(self._outer)
        self._fx_stack.setGeometry(_x0, _y0, _w0, _h0)

        # Opacity effect on fx stack
        self._opacity_effect = QGraphicsOpacityEffect(self._fx_stack)
        self._opacity_effect.setOpacity(0.0)
        self._fx_stack.setGraphicsEffect(self._opacity_effect)

        # Video widget inside fx stack
        self._video_widget = QVideoWidget(self._fx_stack)
        self._video_widget.setGeometry(0, 0, _w0, _h0)
        self._video_widget.setStyleSheet("background: black; border-radius: 12px;")

        # Media player
        self._media_player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.setVideoOutput(self._video_widget)
        self._audio_output.setVolume(0.0)
        self._media_player.setSource(QUrl.fromLocalFile(video_path))
        self._media_player.mediaStatusChanged.connect(self._on_media_status)
        self._media_player.positionChanged.connect(self._on_position_changed)
        self._video_duration = 0

        # Background music
        self._music_player = None
        self._music_audio = None
        if music_path and os.path.exists(music_path):
            self._music_player = QMediaPlayer(self)
            self._music_audio = QAudioOutput(self)
            self._music_player.setAudioOutput(self._music_audio)
            self._music_player.setSource(QUrl.fromLocalFile(music_path))
            self._music_audio.setVolume(0.0)

        self._setup_animations()

    def _center_on_screen(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.y() + (screen.height() - self.height()) // 2
        self.move(x, y)

    def _setup_animations(self):
        # --- ENTRANCE ---
        # Container scale via geometry animation for smoothness
        self._enter_geo = QVariantAnimation(self)
        self._enter_geo.setDuration(900)
        self._enter_geo.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._enter_geo.valueChanged.connect(self._on_enter_geo)

        # Opacity fade in
        self._enter_opacity = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._enter_opacity.setDuration(800)
        self._enter_opacity.setStartValue(0.0)
        self._enter_opacity.setEndValue(1.0)
        self._enter_opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

        # --- EXIT ---
        self._exit_geo = QVariantAnimation(self)
        self._exit_geo.setDuration(650)
        self._exit_geo.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._exit_geo.valueChanged.connect(self._on_exit_geo)

        self._exit_opacity = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._exit_opacity.setDuration(600)
        self._exit_opacity.setStartValue(1.0)
        self._exit_opacity.setEndValue(0.0)
        self._exit_opacity.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._exit_opacity.finished.connect(self.close)

    def _on_enter_geo(self, progress):
        # Interpolate from 82% scale to 100%
        scale = 0.82 + (0.18 * progress)
        w = int(960 * scale)
        h = int(540 * scale)
        x = (960 - w) // 2
        y = (540 - h) // 2
        self._fx_stack.setGeometry(x, y, w, h)
        self._video_widget.setGeometry(0, 0, w, h)

    def _on_exit_geo(self, progress):
        # Exit: shrink from 100% to 85% scale
        scale = 1.0 - (0.15 * progress)
        w = int(960 * scale)
        h = int(540 * scale)
        x = (960 - w) // 2
        y = (540 - h) // 2
        self._fx_stack.setGeometry(x, y, w, h)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self._start_entrance)

    def _start_entrance(self):
        self._enter_geo.setStartValue(0.0)
        self._enter_geo.setEndValue(1.0)
        self._enter_geo.start()
        self._enter_opacity.start()
        self._media_player.play()

        if self._music_player:
            self._music_player.play()
            self._fade_music_in()

    def _fade_music_in(self):
        fade = QPropertyAnimation(self._music_audio, b"volume", self)
        fade.setDuration(1000)
        fade.setStartValue(0.0)
        fade.setEndValue(0.5)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.start()

    def _on_media_status(self, status):
        from PyQt6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            # Get duration once loaded
            self._video_duration = self._media_player.duration()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            # Fallback if position tracking missed
            if not self._exiting:
                self._start_exit()

    def _on_position_changed(self, position):
        """Start exit animation 400ms before video ends."""
        if self._exiting or self._video_duration <= 0:
            return
        remaining = self._video_duration - position
        if remaining <= 400:  # 400ms before end
            self._start_exit()

    def _start_exit(self):
        if self._exiting:
            return
        self._exiting = True

        # Signal main window immediately and close
        self.finished.emit()
        self.close()

    def mousePressEvent(self, event):
        if not self._exiting:
            self._start_exit()


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════
def main():
    import ctypes
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CClip.App")
    except Exception:
        pass

    apply_theme()
    for folder_key in ("in_folder", "out_folder", "mus_folder"):
        os.makedirs(S[folder_key], exist_ok=True)

    app = QApplication(sys.argv)
    _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ico.ico")
    if os.path.exists(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, True)

    scale = float(S.get("ui_scale", 1.0))
    fam = S.get("ui_font", "Segoe UI Variable")
    app.setFont(QFont(fam, max(7, int(round(9 * scale)))))
    app.setStyleSheet(build_qss(scale))

    splash_video = os.path.abspath("start3.mp4")
    splash_music = os.path.abspath("MenuMusic.mp3")

    if os.path.exists(splash_video):
        splash = SplashScreen(splash_video, splash_music)
        splash.show()

        def show_main():
            global APP_INSTANCE
            has_splash_music = splash._music_player and splash._music_audio
            APP_INSTANCE = ClipMasterApp(skip_music_setup=has_splash_music)
            if has_splash_music:
                APP_INSTANCE._music_player = splash._music_player
                APP_INSTANCE._audio_output = splash._music_audio
                APP_INSTANCE._music_player.setParent(APP_INSTANCE)
                APP_INSTANCE._audio_output.setParent(APP_INSTANCE)
                APP_INSTANCE._music_player.mediaStatusChanged.connect(APP_INSTANCE._on_music_status_changed)
                APP_INSTANCE._music_muted = False

            # Fade in main window for seamless transition
            APP_INSTANCE.setWindowOpacity(0.0)
            APP_INSTANCE.show()
            APP_INSTANCE.raise_()
            APP_INSTANCE.activateWindow()

            fade_in = QPropertyAnimation(APP_INSTANCE, b"windowOpacity", APP_INSTANCE)
            fade_in.setDuration(150)
            fade_in.setStartValue(0.0)
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
            fade_in.start()

        splash.finished.connect(show_main)
        sys.exit(app.exec())
    else:
        win = ClipMasterApp()
        win.show()
        sys.exit(app.exec())

if __name__ == "__main__":
    main()