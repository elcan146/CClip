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
import webbrowser
from datetime import datetime, timedelta

# Suppress Qt multimedia FFmpeg warnings
os.environ["QT_LOGGING_RULES"] = "qt.multimedia.ffmpeg=false"

import psutil
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.ticker import FuncFormatter

from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QAbstractAnimation,
    QObject, pyqtSignal, QThread, QVariantAnimation, pyqtProperty, QSize, QRect,
)
from PyQt6.QtGui import (
    QColor, QCursor, QPainter, QBrush, QPen, QFont, QFontDatabase,
    QPainterPath, QLinearGradient, QIcon, QMouseEvent, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QPlainTextEdit, QSlider,
    QCheckBox, QComboBox, QStackedWidget, QFrame, QScrollArea, QFileDialog,
    QMessageBox, QGraphicsOpacityEffect, QProgressBar,
    QStyleFactory, QDialog, QGraphicsDropShadowEffect, QSizePolicy,
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

def _isolate_vocals(video_path, start, end, out_dir, clip_name, audio_track):
    """Extract clip audio and isolate vocals via Demucs --two-stems.
    Returns path to vocals.wav, or path to raw clip WAV if Demucs fails."""
    clip_audio = os.path.join(out_dir, f"clip_audio_{clip_name}.wav")
    cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", video_path,
        "-map", f"0:a:{audio_track}", "-vn", "-acodec", "pcm_s16le",
        "-ar", "44100", "-ac", "2", clip_audio
    ]
    subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if not os.path.exists(clip_audio):
        log("    Demucs skipped: could not extract clip audio.", "warn")
        return None
    try:
        demucs_out = os.path.join(out_dir, "demucs_out")
        cmd_d = ["demucs", "--two-stems=vocals", "-o", demucs_out, clip_audio]
        subprocess.run(cmd_d, capture_output=True, text=True, errors="replace",
                       creationflags=subprocess.CREATE_NO_WINDOW)
        basename   = os.path.splitext(os.path.basename(clip_audio))[0]
        vocals_wav = os.path.join(demucs_out, "htdemucs", basename, "vocals.wav")
        if os.path.exists(vocals_wav):
            log(f"    Vocals isolated: {os.path.basename(vocals_wav)}", "success")
            try:
                os.remove(clip_audio)
            except Exception:
                pass
            return vocals_wav
        log("    Demucs vocals.wav not found — falling back to raw clip audio.", "warn")
        return clip_audio
    except Exception as e:
        log(f"    Demucs error: {e} — using raw clip audio.", "warn")
        return clip_audio

def _transcribe(wav_path, progress_cb):
    try:
        import whisper
        import torch
        import threading
        
        audio_dur = _audio_dur(wav_path)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        
        progress_cb(5, 100, "Loading Whisper model...")
        model = whisper.load_model("small").to(device)
        
        # Use a threading approach to update progress while transcribing
        import time
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

def _render(video_path, sub_path, vo_path, vocals_path, music_path, start, end, out_path, audio_track, progress_cb, duration):
    """
    Render a single clip.  vocals_path = Demucs-isolated WAV (or raw clip WAV).
    If None, falls back to the original movie audio from the video file.

    Video enhancements (applied to the 1:1 fg layer):
      • Sine-wave zoom 1.0x→1.1x / 5-second period
      • 5 % horizontal stretch + center crop
      • Lens barrel correction (subtle distortion)
      • GTA-IV grade: contrast 1.15 / saturation 1.5 / gamma 0.9
      • Unsharp-mask sharpening
      • Vignette, fades, subtitles
      • Micro-jitter speed: sine-wave ±0.005 around base 1.1x

    Audio enhancements (Source A = isolated vocals):
      • +1 % pitch shift (asetrate→atempo), tempo-corrected
      • Transient compressor (attack 5ms / release 50ms / ratio 4) + limiter
      • Microscopic echo (10 ms @ 10 % decay) for dialogue thickness
      • Force centre-mono in stereo frame
      • BGM stereo-widening via extrastereo
      • Pink noise floor at 2 % volume
    """
    safe_sub  = sub_path.replace("\\", "/").replace(":", "\\:")
    fade_out  = max(duration - 0.18, 0)
    vo_dur    = _audio_dur(vo_path)
    clip_dur  = int(duration) + 10

    v_blur    = S['v_blur']
    v_bg_bri  = S['v_bg_bri']
    v_bg_sat  = S['v_bg_sat']
    v_speed   = S['v_speed']

    v_m_base  = S['vol_movie_base']
    v_m_duck  = S['vol_movie_duck']
    v_vo_base = S['vol_vo_base']
    v_vo_duck = S['vol_vo_duck']
    v_bgm     = S['vol_bgm']

    # ── VIDEO FILTERGRAPH ─────────────────────────────────────────
    # BG: blurred full-frame portrait backdrop
    # FG: sine-zoom → 5 % h-stretch → lens correction → colour grade
    # Speed: micro-jitter ±0.005 x around 1.1 x base via setpts expression
    # Logo floating animation: 4s ease-in-out cycle, 20px amplitude, centered vertically
    # t*60 converts time to virtual 60fps frames for smooth animation at any framerate
    logo_y_expr = "(H-h)/2-20*cos(2*PI*t*60/240)"  # smooth 60fps-equivalent float

    # Get absolute path to darklogo.png
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "darklogo.png")
    has_logo = os.path.exists(logo_path) and S.get("logo_enabled", True)

    # Build video filter with optional logo overlay
    if has_logo:
        # Logo chain: loop → scale → opacity → fps → overlay (optimized for fluid 60fps)
        logo_chain = (
            "[1:v]loop=loop=-1:size=1[logo_loop];"
            "[logo_loop]scale=250:-1,colorchannelmixer=aa=0.24,fps=60[logo_scaled];"
            f"[composed][logo_scaled]overlay=(W-w)/2:'{logo_y_expr}':shortest=1[with_logo];"
            "[with_logo]"
        )
    else:
        # No logo - just use composed directly
        logo_chain = "[composed]"

    video_filter = (
        "[0:v]hflip,split=2[v_bg][v_fg];"
        f"[v_bg]setpts=PTS-STARTPTS,"
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,boxblur={v_blur}:2,"
        f"eq=brightness={v_bg_bri}:saturation={v_bg_sat}[bg];"
        "[v_fg]setpts=PTS-STARTPTS,"
        "crop=w='min(iw\\,ih)':h='min(iw\\,ih)',"
        "scale=w='trunc(1080*(1.05+0.05*sin(2*PI*t/5))/2)*2':"
              "h='trunc(1080*(1.05+0.05*sin(2*PI*t/5))/2)*2':eval=frame,"
        "crop=1080:1080,"
        "scale=1134:1080,crop=1080:1080,"
        "setsar=1,"
        "lenscorrection=cx=0.5:cy=0.5:k1=-0.05:k2=0.05[fg];"
        "[bg][fg]overlay=0:420[composed];"
        f"{logo_chain}"
        "eq=contrast=1.15:saturation=1.5:gamma=0.9:brightness=0.01,"
        "unsharp=5:5:1.0:5:5:0.0,"
        "vignette=PI/7,"
        "fade=t=in:st=0:d=0.12,"
        f"fade=t=out:st={fade_out}:d=0.18,"
        f"ass='{safe_sub}',"
        "setpts=PTS/(1.1+0.005*sin(2*PI*T/7.0))[final_v]"
    )

    # ── AUDIO FILTERGRAPH ─────────────────────────────────────────
    if vocals_path:
        # Audio input indexes depend on whether logo is present
        if has_logo:
            # Inputs:  0=video  1=logo  2=voiceover  3=bgm  4=vocals  5=pink-noise
            audio_filter = (
                # Source A — isolated vocals: pitch +1 %, compressor, echo, center-mono
                "[4:a]asetrate=44100*1.01,atempo=0.9901,aresample=44100,"
                "acompressor=attack=5:release=50:ratio=4:makeup=2,"
                "alimiter=limit=0.95,"
                "aecho=0.8:0.8:10:0.1,"
                "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,"
                f"volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[vocals_a];"
                # Source B — AI voiceover: duck when VO has finished
                f"[2:a]volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"
                # Source C — background music: wide stereo
                f"[3:a]volume={v_bgm},"
                "extrastereo=m=1.5,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"
                # Source D — pink noise at 2 %
                "[5:a]volume=0.02,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[noise_a];"
                # Mix + master
                "[vocals_a][voice_a][bgm_a][noise_a]"
                "amix=inputs=4:duration=first:dropout_transition=0,"
                "acompressor=threshold=-15dB:ratio=3:makeup=3,"
                f"extrastereo=m=1.3,atempo={v_speed}[final_a]"
            )
        else:
            # Inputs:  0=video  1=voiceover  2=bgm  3=vocals  4=pink-noise
            audio_filter = (
                # Source A — isolated vocals: pitch +1 %, compressor, echo, center-mono
                "[3:a]asetrate=44100*1.01,atempo=0.9901,aresample=44100,"
                "acompressor=attack=5:release=50:ratio=4:makeup=2,"
                "alimiter=limit=0.95,"
                "aecho=0.8:0.8:10:0.1,"
                "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,"
                f"volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[vocals_a];"
                # Source B — AI voiceover: duck when VO has finished
                f"[1:a]volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"
                # Source C — background music: wide stereo
                f"[2:a]volume={v_bgm},"
                "extrastereo=m=1.5,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"
                # Source D — pink noise at 2 %
                "[4:a]volume=0.02,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[noise_a];"
                # Mix + master
                "[vocals_a][voice_a][bgm_a][noise_a]"
                "amix=inputs=4:duration=first:dropout_transition=0,"
                "acompressor=threshold=-15dB:ratio=3:makeup=3,"
                f"extrastereo=m=1.3,atempo={v_speed}[final_a]"
            )
        # Build command with optional logo input
        cmd_base = ["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", video_path]
        if has_logo:
            cmd_base.extend(["-i", logo_path])
        cmd_base.extend([
            "-stream_loop", "-1", "-i", vo_path,
            "-stream_loop", "-1", "-i", music_path,
            "-i", vocals_path,
            "-f", "lavfi", "-i", f"anoisesrc=c=pink:r=44100:a=0.02:d={clip_dur}",
            "-filter_complex_threads", "1",
            "-filter_complex", f"{video_filter};{audio_filter}",
            "-map", "[final_v]", "-map", "[final_a]",
            "-c:v", "h264_nvenc", "-preset", "p4",
            "-pix_fmt", "yuv420p", "-c:a", "aac", out_path
        ])
        command = cmd_base
    else:
        # Fallback: original movie audio taken directly from the video file
        # Audio inputs depend on whether logo is present
        if has_logo:
            # Inputs: 0=video 1=logo 2=voiceover 3=bgm
            audio_filter = (
                f"[0:a:{audio_track}]volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[movie_a];"
                f"[2:a]volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"
                f"[3:a]volume={v_bgm},"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"
                "[movie_a][voice_a][bgm_a]amix=inputs=3:duration=first:dropout_transition=0,"
                "loudnorm=I=-14:LRA=11:TP=-1.5,"
                "bass=g=4:f=100,treble=g=3:f=7500,"
                "acompressor=threshold=-15dB:ratio=3:makeup=3,"
                f"extrastereo=m=1.3,atempo={v_speed}[final_a]"
            )
        else:
            # Inputs: 0=video 1=voiceover 2=bgm
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
        # Build command with optional logo input
        cmd_base = ["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", video_path]
        if has_logo:
            cmd_base.extend(["-i", logo_path])
        cmd_base.extend([
            "-stream_loop", "-1", "-i", vo_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex_threads", "1",
            "-filter_complex", f"{video_filter};{audio_filter}",
            "-map", "[final_v]", "-map", "[final_a]",
            "-c:v", "h264_nvenc", "-preset", "p4",
            "-pix_fmt", "yuv420p", "-c:a", "aac", out_path
        ])
        command = cmd_base

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

                # Generate formatted filename base: SeriesName_T1_dd.mm.yy_hh.mm
                now = datetime.now()
                date_str = now.strftime("%d.%m.%y")  # dd.mm.yy
                time_str = now.strftime("%H.%M")      # hh.mm
                type_letter = prefix[0].upper() if prefix else "C"  # F/B/D
                clip_num = ci  # 1 or 2
                # Clean series name - remove spaces and special chars
                series_clean = "".join(c for c in movie_name_no_ext if c.isalnum())
                file_base = f"{series_clean}_{type_letter}{clip_num}_{date_str}_{time_str}"
                output_filename = f"{file_base}.mp4"
                output_mp4 = os.path.join(movie_out_dir, output_filename)

                meta_path = os.path.join(movie_out_dir, f"{file_base}_metadata.txt")
                with open(meta_path, "w", encoding="utf-8") as mf:
                    yt_title = data.get('yt_title', 'Clip #shorts')
                    yt_desc = data.get('yt_desc', '#clips')
                    mf.write(f"TITLE:\n{yt_title}\n\nDESCRIPTION:\n{yt_desc}")

                vo_path = os.path.join(movie_out_dir, f"{file_base}_vo.mp3")
                script_text = data.get("script", "Wow.")
                _gen_voiceover(script_text, vo_path)

                clip_title = data.get("title", "EPIC")
                sub_path = _build_subs(full_transcript, start, end, file_base, clip_title, movie_out_dir)

                log(f"    Isolating vocals with Demucs...", "info")
                vocals_path = _isolate_vocals(movie_path, start, end, movie_out_dir, clip_name, audio_track)

                render_start_time = time.time()
                _render(movie_path, sub_path, vo_path, vocals_path, music_path, start, end, output_mp4, audio_track, progress_cb, end - start)
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

    if os.path.exists("user_data/token.json"):
        creds = Credentials.from_authorized_user_file("user_data/token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GRequest())
        else:
            if not os.path.exists("user_data/client_secrets.json"):
                yt_log("client_secrets.json not found!")
                finish_cb(False)
                return

            yt_log("Opening browser for YouTube OAuth...")
            flow = InstalledAppFlow.from_client_secrets_file("user_data/client_secrets.json", SCOPES)
            creds = flow.run_local_server(port=0)

        with open("user_data/token.json", "w") as tf:
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

        self._title_lbl = QLabel("")
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

            SCOPES = [
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/yt-analytics.readonly"
            ]
            creds = None
            token_path = "user_data/token.json"
            secrets_path = "user_data/client_secrets.json"

            if os.path.exists(token_path):
                creds = Credentials.from_authorized_user_file(token_path, SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(GRequest())
                else:
                    if not os.path.exists(secrets_path):
                        self.error.emit("client_secrets.json not found in user_data/!")
                        return
                    flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
                    creds = flow.run_local_server(port=0)
                with open(token_path, "w") as tf:
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


class AnimatedComboPicker(QWidget):
    """Premium animated dropdown picker (like FontPicker but for text options).
    Shows a pill-shaped button with animated fade+slide popup menu."""
    optionChanged = pyqtSignal(str)

    def __init__(self, options, current=None, width=160, parent=None):
        super().__init__(parent)
        self._options = list(options)
        self._current = current or (self._options[0] if self._options else "")
        self._popup = None
        self._open = False
        self._width = width
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

    def setCurrent(self, option):
        if option in self._options:
            self._current = option
            self._refresh_btn()
            self.optionChanged.emit(option)

    def currentText(self):
        return self._current

    def _refresh_btn(self):
        self._btn.setText(f"  {self._current}")
        self._btn.setFont(QFont(S.get("ui_font", "Segoe UI Variable"), 12))
        self._btn.setStyleSheet(
            f"QPushButton {{ background: {CARD2}; color: {TEXT}; "
            f"border: 1px solid {BOR}; border-radius: 10px; "
            f"padding: 6px 28px 6px 12px; text-align: left; }}"
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
        # No white background - use app's color scheme
        pop.setStyleSheet(f"background: {CARD}; border: none;")
        self._popup = pop
        self._open = True
        self._arrow.setText("\u25B4")
        self._refresh_btn()
        pop.destroyed.connect(lambda: (setattr(self, "_popup", None),
                                       setattr(self, "_open", False)))

        # Styled frame inside
        outer = QFrame(pop)
        outer.setStyleSheet(
            f"QFrame {{ background: {CARD2}; border: 1px solid {BOR}; border-radius: 10px; }}"
        )
        wrap = QVBoxLayout(pop)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(outer)

        v = QVBoxLayout(outer)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(2)

        # Scrollable options
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(2, 2, 2, 2)
        iv.setSpacing(2)

        for opt in self._options:
            is_active = (opt == self._current)
            btn = QPushButton(opt)
            btn.setFlat(True)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setFixedHeight(34)
            if is_active:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {ACC}; color: #ffffff; "
                    f"border-radius: 8px; text-align: left; padding-left: 12px; "
                    f"font-weight: 600; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {TEXT}; "
                    f"border-radius: 8px; text-align: left; padding-left: 12px; }}"
                    f"QPushButton:hover {{ background: {CARD3}; }}"
                )

            def mk(choice):
                def _pick():
                    self._current = choice
                    self._open = False
                    self._arrow.setText("\u25BE")
                    self._refresh_btn()
                    if self._popup:
                        pop_to_close = self._popup
                        self._popup = None
                        pop_to_close.close()
                        pop_to_close.deleteLater()
                    QTimer.singleShot(0, lambda: self.optionChanged.emit(choice))
                return _pick

            btn.clicked.connect(mk(opt))
            iv.addWidget(btn)

        iv.addStretch(1)
        scroll.setWidget(inner)
        v.addWidget(scroll)

        # Size and position
        pop.resize(self.width() + 10, min(200, len(self._options) * 38 + 20))
        gp = self.mapToGlobal(QPoint(0, self.height() + 4))

        screen = QApplication.primaryScreen().availableGeometry()
        x = max(screen.x(), min(gp.x(), screen.x() + screen.width() - pop.width()))
        y = gp.y()
        if y + pop.height() > screen.y() + screen.height():
            y = self.mapToGlobal(QPoint(0, -pop.height() - 4)).y()
        y = max(screen.y(), y)

        # Slide-in + fade animation
        start_pos = QPoint(x, y + 10)
        end_pos = QPoint(x, y)
        pop.move(start_pos)
        pop.setWindowOpacity(0.0)
        pop.show()

        fade = QPropertyAnimation(pop, b"windowOpacity", pop)
        fade.setDuration(180)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)

        slide = QPropertyAnimation(pop, b"pos", pop)
        slide.setDuration(180)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide.setStartValue(start_pos)
        slide.setEndValue(end_pos)

        fade.start()
        slide.start()


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
class ClipMasterApp(QMainWindow):
    NAV_ITEMS = [
        ("config",    "\u2699",  "Configuration"),   # gear
        ("prompt",    "\u2726",  "Prompt Editor"),        # 4-pointed star
        ("audio",     "\u266A",  "Audio Mixer"),     # eighth note
        ("video",     "\u25A6",  "Video & Subs"),    # square w/ horizontal lines
        ("engine",    "\u25B6",  "Create"),     # play triangle
        ("youtube",   "\u25B7",  "YouTube Panel"),   # outlined play
        ("analytics", "\u25C9",  "Analytics"),       # fisheye
        ("uiconfig",  "\u2630",  "UI Settings"),     # trigram lines
    ]

    def __init__(self, skip_music_setup=False):
        super().__init__()
        global APP_INSTANCE
        APP_INSTANCE = self

        self.setWindowTitle("CClip v1.0")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setMouseTracking(True)
        self.resize(1480, 900)
        self.setMinimumSize(1240, 760)

        self._running = False
        self._batch_thread = None
        self._upload_thread = None
        self._scan_thread = None
        self._auth_thread = None

        self.hist_cpu = [0]*60
        self.hist_gpu = [0]*60
        self.hist_ram = [0]*60
        self.hist_dsk = [0]*60

        self._current_page = None
        self._page_snapshot = {}
        self._dirty_keys = set()
        self._suppress_dirty = False

        # Setup logging
        self._setup_logging()

        self._builders = {
            "config":    self._build_config_page,
            "prompt":    self._build_prompt_page,
            "audio":     self._build_audio_page,
            "video":     self._build_video_subs_page,
            "engine":    self._build_engine_page,
            "analytics": self._build_analytics_page,
            "youtube":   self._build_youtube_page,
            "uiconfig":  self._build_uiconfig_page,
        }

        self._setup_ui(initial_page="config")

        self._stats_thread = StatsThread(); self._stats_thread.start()

        self._log_timer = QTimer(self); self._log_timer.timeout.connect(self._poll_log); self._log_timer.start(60)
        self._yt_timer  = QTimer(self); self._yt_timer.timeout.connect(self._poll_yt_log); self._yt_timer.start(100)
        self._stats_timer = QTimer(self); self._stats_timer.timeout.connect(self._poll_stats); self._stats_timer.start(120)
        self._clock_timer = QTimer(self); self._clock_timer.timeout.connect(self._tick_clock); self._clock_timer.start(1000)
        self._tick_clock()

        if not skip_music_setup:
            self._setup_music()

    def _setup_music(self):
        """Initialize menu music player."""
        self._music_muted = False
        self._music_player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._music_player.setAudioOutput(self._audio_output)
        self._audio_output.setVolume(0.5)
        music_path = os.path.abspath("MenuMusic.mp3")
        if os.path.exists(music_path):
            import contextlib
            with contextlib.redirect_stderr(open(os.devnull, 'w')):
                self._music_player.setSource(QUrl.fromLocalFile(music_path))
            self._music_player.mediaStatusChanged.connect(self._on_music_status_changed)
            self._music_player.play()

    def _setup_logging(self):
        """Setup session logging to Logs folder."""
        self._logs_folder = os.path.abspath("Logs")
        os.makedirs(self._logs_folder, exist_ok=True)
        
        # Create log file with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_file = os.path.join(self._logs_folder, f"cclip_{timestamp}.log")
        
        # Write session header
        with open(self._log_file, "w", encoding="utf-8") as f:
            f.write(f"CClip Session Log\n")
            f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*60}\n\n")

    def _write_log(self, msg):
        """Write a message to the log file."""
        if hasattr(self, '_log_file') and self._log_file:
            try:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    ts = datetime.now().strftime("%H:%M:%S")
                    f.write(f"[{ts}] {msg}\n")
            except Exception:
                pass  # Silently fail if logging fails

    def _setup_ui(self, initial_page="config"):
        """Build the whole chrome (title bar, sidebar, center stack, right sidebar, toast). Re-callable for live theme/scale changes."""
        outer = QWidget(); outer.setObjectName("root")
        self.setCentralWidget(outer)
        outer_v = QVBoxLayout(outer)
        outer_v.setContentsMargins(0, 0, 0, 0)
        outer_v.setSpacing(0)

        self.title_bar = CustomTitleBar(self, outer)
        outer_v.addWidget(self.title_bar)

        self.root = QWidget(); self.root.setObjectName("root")
        outer_v.addWidget(self.root, 1)

        root_layout = QHBoxLayout(self.root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._build_sidebar()
        root_layout.addWidget(self.sidebar)
        sep = QFrame(); sep.setObjectName("vhairline"); root_layout.addWidget(sep)

        self.center = QWidget(); self.center.setObjectName("pageBg")
        center_v = QVBoxLayout(self.center)
        center_v.setContentsMargins(0, 0, 0, 0); center_v.setSpacing(0)
        self.stack = QStackedWidget()
        center_v.addWidget(self.stack, 1)
        root_layout.addWidget(self.center, 1)

        sep2 = QFrame(); sep2.setObjectName("vhairline"); root_layout.addWidget(sep2)
        self._build_right_sidebar()
        root_layout.addWidget(self.right_bar)

        self._pages = {}
        for key, fn in self._builders.items():
            fn()

        self._toast = ToastPopup(self.root, self._on_toast_save, self._on_toast_discard)

        self._indicator = QFrame(self.nav_container)
        self._indicator.setStyleSheet(f"background: {ACC}; border-radius: 2px;")
        self._indicator.setFixedSize(3, 22)
        self._indicator.show()
        self._indicator.raise_()
        self._ind_anim = QPropertyAnimation(self._indicator, b"pos", self)
        self._ind_anim.setDuration(260)
        self._ind_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        QTimer.singleShot(0, lambda k=initial_page: self.show_page(k, animate=False))

    # ---------------- EDGE RESIZE (frameless window) ----------------
    _EDGE = 6

    def _edge_at(self, pos):
        r = self.rect()
        x, y, w, h, m = pos.x(), pos.y(), r.width(), r.height(), self._EDGE
        left   = x < m
        right  = x > w - m
        top    = y < m
        bottom = y > h - m
        if top and left:      return Qt.CursorShape.SizeFDiagCursor,  "tl"
        if top and right:     return Qt.CursorShape.SizeBDiagCursor,  "tr"
        if bottom and left:   return Qt.CursorShape.SizeBDiagCursor,  "bl"
        if bottom and right:  return Qt.CursorShape.SizeFDiagCursor,  "br"
        if top:               return Qt.CursorShape.SizeVerCursor,    "t"
        if bottom:            return Qt.CursorShape.SizeVerCursor,    "b"
        if left:              return Qt.CursorShape.SizeHorCursor,    "l"
        if right:             return Qt.CursorShape.SizeHorCursor,    "r"
        return None, None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            cursor, edge = self._edge_at(event.position().toPoint())
            if edge:
                self._resize_edge = edge
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geo = self.geometry()
                event.accept()
                return
        self._resize_edge = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if hasattr(self, '_resize_edge') and self._resize_edge and (event.buttons() & Qt.MouseButton.LeftButton):
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            geo = QRect(self._resize_start_geo)
            edge = self._resize_edge
            dx, dy = delta.x(), delta.y()
            if 'r' in edge: geo.setRight(geo.right() + dx)
            if 'b' in edge: geo.setBottom(geo.bottom() + dy)
            if 'l' in edge: geo.setLeft(geo.left() + dx)
            if 't' in edge: geo.setTop(geo.top() + dy)
            min_w, min_h = self.minimumWidth(), self.minimumHeight()
            if geo.width() >= min_w and geo.height() >= min_h:
                self.setGeometry(geo)
            event.accept()
            return
        cursor, _ = self._edge_at(event.position().toPoint())
        self.setCursor(cursor or Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_edge = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    # ---------------- LIVE THEME / SCALE ----------------
    def apply_live_theme(self):
        """Rebuild the entire UI live with the new theme/scale/font."""
        apply_theme()
        scale = float(S.get("ui_scale", 1.0))
        fam = S.get("ui_font", "Segoe UI Variable")
        app = QApplication.instance()
        if app:
            app.setFont(QFont(fam, max(7, int(round(9 * scale)))))
            app.setStyleSheet(build_qss(scale))
        cur = self._current_page or "config"
        self._current_page = None
        self._suppress_dirty = True
        try:
            self._setup_ui(initial_page=cur)
        finally:
            self._suppress_dirty = False
        if hasattr(self, "title_bar"):
            self.title_bar.refresh_style()

    # ---------------- DIRTY TRACKING ----------------
    def _mark_dirty(self, key):
        if getattr(self, "_suppress_dirty", False):
            return
        self._dirty_keys.add(key)

    def _rebuild_page(self, key):
        """Destroy and rebuild a single page (used by discard)."""
        w = self._pages.get(key)
        if not w:
            return
        idx = self.stack.indexOf(w)
        self.stack.removeWidget(w)
        w.deleteLater()
        del self._pages[key]
        builder = self._builders.get(key)
        if builder:
            self._suppress_dirty = True
            try:
                builder()
            finally:
                self._suppress_dirty = False
        new_w = self._pages.get(key)
        if new_w:
            self.stack.setCurrentWidget(new_w)

    # ---------------- SIDEBAR ----------------
    def _build_sidebar(self):
        self.sidebar = QWidget(); self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(248)
        v = QVBoxLayout(self.sidebar)
        v.setContentsMargins(0, 18, 0, 14); v.setSpacing(0)

        _base = os.path.dirname(os.path.abspath(__file__))
        _logo_file = "darklogo.png" if S.get("ui_theme", "Dark") == "Light" else "lightlogo.png"
        _lp = os.path.join(_base, _logo_file)
        _lx = QPixmap(_lp) if os.path.exists(_lp) else None
        brand = QLabel()
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setContentsMargins(0, 0, 0, 8)
        if _lx and not _lx.isNull():
            brand.setPixmap(_lx.scaledToWidth(200, Qt.TransformationMode.SmoothTransformation))
        else:
            brand.setText("CClip"); brand.setObjectName("brandTitle")
        v.addWidget(brand)

        v.addSpacing(12)

        self.nav_container = QWidget()
        self.nav_layout = QVBoxLayout(self.nav_container)
        self.nav_layout.setContentsMargins(10, 0, 10, 0); self.nav_layout.setSpacing(3)
        v.addWidget(self.nav_container)

        self._nav_btns = {}
        for key, icon, label in self.NAV_ITEMS:
            b = NavButton(key, icon, label)
            b.clicked.connect(self.show_page)
            self.nav_layout.addWidget(b)
            self._nav_btns[key] = b

        v.addSpacing(14)

        pills = QWidget()
        pv = QVBoxLayout(pills); pv.setContentsMargins(18, 0, 18, 0); pv.setSpacing(6)
        self._sb_movies_lbl = self._make_pill(pv, "Movies", str(S['total_movies']), ACC)
        self._sb_shorts_lbl = self._make_pill(pv, "Shorts", str(S['total_shorts']), GREEN)
        v.addWidget(pills)

        v.addStretch(1)

        reset_btn = QPushButton("Reset Defaults")
        reset_btn.setObjectName("softAction")
        reset_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        reset_btn.setFixedHeight(34)
        reset_btn.clicked.connect(self._reset_all_defaults)

        # Music toggle button
        self._music_btn = QPushButton("\u266B")  # Musical note symbol
        self._music_btn.setObjectName("softAction")
        self._music_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._music_btn.setFixedHeight(34)
        self._music_btn.setFixedWidth(40)
        self._music_btn.setToolTip("Toggle menu music")
        self._music_btn.clicked.connect(self._toggle_music)

        reset_wrap = QWidget()
        rw = QHBoxLayout(reset_wrap); rw.setContentsMargins(18, 0, 18, 10)
        rw.addWidget(reset_btn)
        rw.addWidget(self._music_btn)
        v.addWidget(reset_wrap)

        cr = QLabel("\u00A9 2026  CClip v1.1")
        cr.setObjectName("tiny")
        cr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(cr)

    def _make_pill(self, parent_layout, label, value, color):
        f = QFrame(); f.setObjectName("cardSlim")
        h = QHBoxLayout(f); h.setContentsMargins(12, 8, 12, 8)
        l = QLabel(label); l.setObjectName("subSm")
        vv = QLabel(value); vv.setStyleSheet(f"color: {color}; font-weight: 600;")
        vv.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(l); h.addStretch(1); h.addWidget(vv)
        parent_layout.addWidget(f)
        return vv

    def _reset_all_defaults(self):
        resp = ModernDialog.ask(
            self,
            "Reset defaults",
            "Restore every setting to the defaults stored in this code file?",
            buttons=[
                ("Cancel", "cancel", False),
                ("Reset",  "reset",  True),
            ],
            icon_char="\u26A0",
            icon_color=YEL,
        )
        if resp != "reset":
            return
        S.clear()
        S.update(copy.deepcopy(DEFAULT_SETTINGS))
        for folder_key in ("in_folder", "out_folder", "mus_folder"):
            os.makedirs(S[folder_key], exist_ok=True)
        save_settings()
        self._dirty_keys.clear()
        self._page_snapshot = copy.deepcopy(S)
        if hasattr(self, "_toast") and self._toast._shown:
            self._toast.slide_out()
        self.apply_live_theme()

    # ---------------- RIGHT TELEMETRY ----------------
    def _build_right_sidebar(self):
        self.right_bar = QWidget(); self.right_bar.setObjectName("rightbar")
        self.right_bar.setFixedWidth(266)
        v = QVBoxLayout(self.right_bar)
        v.setContentsMargins(18, 18, 12, 12); v.setSpacing(6)

        h1 = QLabel("System "); h1.setObjectName("h2"); v.addWidget(h1)
        sub = QLabel("Live \u00B7 refreshes every 1.5s"); sub.setObjectName("subSm"); v.addWidget(sub)
        v.addSpacing(6)
        line = QFrame(); line.setObjectName("hairline"); v.addWidget(line)

        bg_rgb = SIDEBAR
        self.sys_fig = Figure(figsize=(2.5, 8.5), dpi=92, facecolor=bg_rgb)
        self.sys_fig.subplots_adjust(left=0.12, right=0.99, top=0.96, bottom=0.04, hspace=0.55)

        def setup_ax(ax, title, color):
            ax.set_facecolor(bg_rgb)
            ax.set_ylim(0, 100); ax.set_xlim(0, 59)
            ax.set_xticks([]); ax.set_yticks([0, 50, 100])
            ax.set_yticklabels(['0', '50', '100'], color=SUB_DK, fontsize=7)
            ax.set_title(title, color=TEXT, fontsize=9, pad=6, loc="left")
            ax.grid(True, color=SUB_DK, linestyle=":", linewidth=0.6, alpha=0.85)
            for sp in ax.spines.values():
                sp.set_edgecolor(BOR); sp.set_linewidth(0.6)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ln, = ax.plot([], [], color=color, lw=1.6)
            val = ax.text(0.98, 0.84, "0%", transform=ax.transAxes, ha="right", va="center",
                          color=color, fontsize=9, fontweight="bold")
            return ln, ax, val

        self.line_cpu, self.ax_cpu, self.val_cpu = setup_ax(self.sys_fig.add_subplot(411), "CPU", ACC)
        self.line_gpu, self.ax_gpu, self.val_gpu = setup_ax(self.sys_fig.add_subplot(412), "GPU", GREEN)
        self.line_ram, self.ax_ram, self.val_ram = setup_ax(self.sys_fig.add_subplot(413), "RAM", YEL)
        self.line_dsk, self.ax_dsk, self.val_dsk = setup_ax(self.sys_fig.add_subplot(414), "DISK", RED)

        self.sys_canvas = FigureCanvas(self.sys_fig)
        self.sys_canvas.setStyleSheet("background: transparent;")
        v.addWidget(self.sys_canvas, 1)

        self._clock_btn = QPushButton("")
        self._clock_btn.setFlat(True)
        self._clock_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._clock_btn.setStyleSheet(
            f"QPushButton {{ background: {CARD2}; color: {TEXT}; border: 1px solid {BOR_SFT}; "
            f"border-radius: 8px; padding: 8px 10px; font-weight: 600; }}"
            f"QPushButton:hover {{ border-color: {ACC}; background: {CARD3}; }}"
        )
        self._clock_btn.clicked.connect(self._open_calendar_popup)
        v.addWidget(self._clock_btn)

    def _tick_clock(self):
        now = datetime.now()
        line1 = now.strftime("%d/%m/%Y")
        line2 = now.strftime("%A  %H:%M:%S")
        if hasattr(self, "_clock_btn") and self._clock_btn is not None:
            self._clock_btn.setText(f"{line1}\n{line2}")

    def _open_calendar_popup(self):
        dlg = QDialog(self)
        dlg.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QFrame(dlg); outer.setObjectName("calOuter")
        outer.setStyleSheet(
            f"QFrame#calOuter {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 14px; }}"
        )

        root = QVBoxLayout(dlg); root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(outer)

        v = QVBoxLayout(outer); v.setContentsMargins(18, 16, 18, 14); v.setSpacing(10)

        today_lbl = QLabel(datetime.now().strftime("%A, %d %B %Y"))
        today_lbl.setStyleSheet(
            f"color: {TEXT}; font-size: 14px; font-weight: 600; "
            f"background: transparent; border: none;"
        )
        v.addWidget(today_lbl)

        time_lbl = QLabel(datetime.now().strftime("%H:%M:%S"))
        time_lbl.setStyleSheet(
            f"color: {SUB}; font-size: 12px; background: transparent; border: none; "
            f"font-weight: 500;"
        )
        v.addWidget(time_lbl)

        sep = QFrame(); sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {BOR_SFT}; border: none;")
        v.addWidget(sep)

        cal = ModernCalendar()
        v.addWidget(cal)

        foot = QHBoxLayout(); foot.setContentsMargins(0, 4, 0, 0); foot.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(30); close_btn.setFixedWidth(90)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(dlg.close)
        foot.addWidget(close_btn)
        v.addLayout(foot)

        dlg.setWindowOpacity(0.0)
        fade = QPropertyAnimation(dlg, b"windowOpacity", dlg)
        fade.setDuration(160); fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(0.0); fade.setEndValue(1.0)

        # Position above the clock button
        dlg.adjustSize()
        if hasattr(self, "_clock_btn") and self._clock_btn is not None:
            gp = self._clock_btn.mapToGlobal(QPoint(0, 0))
            x = gp.x() + self._clock_btn.width() // 2 - dlg.sizeHint().width() // 2
            y = gp.y() - dlg.sizeHint().height() - 8
            # Clamp to available screen geometry so calendar is always visible
            screen = QApplication.primaryScreen().availableGeometry()
            x = max(screen.x() + 8, min(x, screen.x() + screen.width()  - dlg.sizeHint().width()  - 8))
            y = max(screen.y() + 8, min(y, screen.y() + screen.height() - dlg.sizeHint().height() - 8))
            # If above-button position causes clipping, try below instead
            if y < screen.y() + 8:
                y_below = gp.y() + self._clock_btn.height() + 8
                if y_below + dlg.sizeHint().height() <= screen.y() + screen.height() - 8:
                    y = y_below
                else:
                    y = screen.y() + 8
            dlg.move(x, y)
        dlg.show()
        fade.start()
        dlg.exec()

    # ---------------- PAGE TRANSITIONS ----------------
    def _add_page(self, key, widget):
        self._pages[key] = widget
        self.stack.addWidget(widget)

    def show_page(self, key, animate=True):
        if key not in self._pages:
            return
        widget = self._pages[key]
        if self.stack.currentWidget() is widget:
            self._update_nav_visuals(key)
            self._move_indicator(key)
            return

        # Prompt if leaving a dirty tab
        if self._current_page and self._current_page != key and self._dirty_keys:
            tab_name = dict((k, lbl) for k, _icn, lbl in self.NAV_ITEMS).get(
                self._current_page, self._current_page
            )
            resp = ModernDialog.ask(
                self,
                "Unsaved changes",
                f"You have unsaved changes on the \u201C{tab_name}\u201D tab.\n"
                f"Do you want to save them, discard them, or stay on this tab?",
                buttons=[
                    ("Cancel",  "cancel",  False),
                    ("Discard", "discard", False),
                    ("Save",    "save",    True),
                ],
                icon_char="\u26A0",
                icon_color=YEL,
            )
            if resp in (None, "cancel"):
                return
            if resp == "save":
                save_settings()
                self._dirty_keys.clear()
                if self._toast._shown:
                    self._toast.slide_out()
            elif resp == "discard":
                self._revert_dirty()
                prev = self._current_page
                if self._toast._shown:
                    self._toast.slide_out()
                self._rebuild_page(prev)
                widget = self._pages[key]

        # snapshot the incoming page so discard can revert
        self._page_snapshot = copy.deepcopy(S)
        self._current_page = key

        self.stack.setCurrentWidget(widget)
        self._update_nav_visuals(key)
        self._move_indicator(key)

        if animate:
            eff = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(eff)
            eff.setOpacity(0.0)
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(240)
            anim.setStartValue(0.0); anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda: widget.setGraphicsEffect(None))
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

        if key == "analytics":
            QTimer.singleShot(80, self._update_charts)

    def _update_nav_visuals(self, key):
        for k, b in self._nav_btns.items():
            b.setChecked(k == key)

    def _move_indicator(self, key):
        btn = self._nav_btns.get(key)
        if not btn:
            return
        self.nav_container.updateGeometry()
        y = btn.y() + (btn.height() - self._indicator.height()) // 2
        target = QPoint(4, y)
        if not self._indicator.isVisible() or self._indicator.pos() == QPoint(0, 0):
            self._indicator.move(target); self._indicator.show(); self._indicator.raise_()
            return
        if self._ind_anim.state() == QAbstractAnimation.State.Running:
            self._ind_anim.stop()
        self._ind_anim.setStartValue(self._indicator.pos())
        self._ind_anim.setEndValue(target)
        self._ind_anim.start()

    # ---------------- PAGE HELPERS ----------------
    def _scroll_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("pageBg")
        scroll.setStyleSheet(f"QScrollArea {{ background: {BG}; border: none; }}")
        scroll.viewport().setStyleSheet(f"background: {BG};")
        inner = QWidget(); inner.setObjectName("pageBg")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(24, 18, 24, 18); lay.setSpacing(10)
        scroll.setWidget(inner)
        return scroll, lay

    def _plain_page(self):
        page = QWidget(); page.setObjectName("pageBg")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 18, 24, 18); lay.setSpacing(10)
        return page, lay

    def _page_header(self, lay, title, subtitle=""):
        h = QLabel(title); h.setObjectName("h1"); lay.addWidget(h)
        if subtitle:
            s = QLabel(subtitle); s.setObjectName("sub"); lay.addWidget(s)
        accent = QFrame()
        accent.setStyleSheet(f"background: {ACC}; border: none; border-radius: 1px;")
        accent.setFixedHeight(2); accent.setFixedWidth(40)
        lay.addWidget(accent)
        anim = QPropertyAnimation(accent, b"minimumWidth", self)
        anim.setDuration(500); anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(40); anim.setEndValue(160)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        anim2 = QPropertyAnimation(accent, b"maximumWidth", self)
        anim2.setDuration(500); anim2.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim2.setStartValue(40); anim2.setEndValue(160)
        anim2.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        lay.addSpacing(4)

    # ---------------- ROW BUILDERS ----------------
    def _slider_row(self, parent_layout, label, key, min_val, max_val, default, is_int=False, callback=None):
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setMinimumWidth(210); lbl.setMaximumWidth(250)
        h.addWidget(lbl)

        slider = QSlider(Qt.Orientation.Horizontal)
        if is_int:
            slider.setMinimum(int(min_val)); slider.setMaximum(int(max_val))
            slider.setValue(int(S.get(key, default)))
        else:
            scale = 100
            slider.setMinimum(int(min_val * scale)); slider.setMaximum(int(max_val * scale))
            slider.setValue(int(float(S.get(key, default)) * scale))

        val_lbl = QLabel(""); val_lbl.setObjectName("pctLabel")
        val_lbl.setMinimumWidth(60)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        def fmt(v):
            return str(int(v)) if is_int else f"{v:.2f}"

        def on_change(raw):
            v = int(raw) if is_int else round(raw / 100.0, 2)
            val_lbl.setText(fmt(v))
            update_s(key, v)
            if callback:
                callback(v)

        slider.valueChanged.connect(on_change)
        cur = S.get(key, default)
        val_lbl.setText(fmt(int(cur) if is_int else float(cur)))

        h.addWidget(slider, 1); h.addWidget(val_lbl)
        parent_layout.addWidget(row)

    def _text_row(self, parent_layout, label, key, default):
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setMinimumWidth(210); lbl.setMaximumWidth(250); h.addWidget(lbl)
        edit = QLineEdit(str(S.get(key, default)))
        edit.textChanged.connect(lambda t: update_s(key, t.strip()))
        edit.setMaximumWidth(280); h.addWidget(edit); h.addStretch(1)
        parent_layout.addWidget(row)

    def _option_row(self, parent_layout, label, key, values, callback=None):
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setMinimumWidth(210); lbl.setMaximumWidth(250); h.addWidget(lbl)
        combo = ModernComboBox(); combo.addItems(values)
        combo.setMinimumWidth(100)
        cur = S.get(key, values[0])
        if cur in values:
            combo.setCurrentText(cur)
        def on_change(v):
            update_s(key, v)
            if callback:
                callback(v)
        combo.currentTextChanged.connect(on_change)
        h.addWidget(combo); h.addStretch(1)
        parent_layout.addWidget(row)

    def _color_row(self, parent_layout, label, key, default_hex, on_apply=None):
        """Plain (unframed) row: label, small swatch, hex, Pick button \u2014 opens ModernColorPicker."""
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setMinimumWidth(210); lbl.setMaximumWidth(250)
        h.addWidget(lbl)

        cur_hex = str(S.get(key, default_hex))
        if cur_hex.startswith("&H"):
            cur_hex = default_hex
            update_s(key, cur_hex, trigger_save_popup=False)

        sw = QFrame(); sw.setFixedSize(28, 28)
        sw.setStyleSheet(f"background: {cur_hex}; border: 1px solid {BOR}; border-radius: 6px;")
        h.addWidget(sw)

        hex_lbl = QLabel(cur_hex.upper())
        hex_lbl.setMinimumWidth(86)
        hex_lbl.setStyleSheet(
            f"color: {TEXT}; font-family: 'Cascadia Mono', Consolas, monospace; font-weight: 500; "
            f"background: transparent; border: none;"
        )
        h.addWidget(hex_lbl)

        btn = QPushButton("Pick"); btn.setObjectName("softAction"); btn.setFixedWidth(100); btn.setFixedHeight(30)
        def pick():
            start = str(S.get(key, default_hex))
            if start.startswith("&H"):
                start = default_hex
            col = ModernColorPicker.pick(self, start, f"Choose {label}")
            if col is not None:
                hexv = col.name().upper()
                sw.setStyleSheet(f"background: {hexv}; border: 1px solid {BOR}; border-radius: 6px;")
                hex_lbl.setText(hexv)
                update_s(key, hexv)
                if on_apply:
                    on_apply(hexv)
        btn.clicked.connect(pick)
        h.addWidget(btn); h.addStretch(1)
        parent_layout.addWidget(row)

    def _font_combo_row(self, parent_layout, label, key, default, options, callback=None):
        """Uses the custom animated FontPicker widget (compact, no wasted right space)."""
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setMinimumWidth(210); lbl.setMaximumWidth(250)
        h.addWidget(lbl)
        cur = S.get(key, default)
        picker = FontPicker(options, current=cur, width=220)
        def on_change(v):
            update_s(key, v)
            if callback:
                callback(v)
        picker.fontChanged.connect(on_change)
        h.addWidget(picker); h.addStretch(1)
        parent_layout.addWidget(row)

    def _toggle_row(self, parent_layout, label, desc, key, on_color):
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 6, 0, 6); h.setSpacing(12)

        text_col = QVBoxLayout(); text_col.setSpacing(2)
        l = QLabel(label); l.setObjectName("h3"); text_col.addWidget(l)
        d = QLabel(desc); d.setObjectName("subSm"); d.setWordWrap(True); text_col.addWidget(d)
        wrap = QWidget(); wrap.setLayout(text_col)
        h.addWidget(wrap, 1)

        sw = ToggleSwitch(on_color=on_color)
        sw.setChecked(bool(S.get(key, False)))
        sw.toggled.connect(lambda v, k=key: update_s(k, bool(v)))
        h.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        parent_layout.addWidget(row)

    def _create_styled_combo(self, options, default, width=160, callback=None):
        """Create an animated premium dropdown like FontPicker (custom popup with slide/fade animation)."""
        picker = AnimatedComboPicker(options, current=default, width=width)
        if callback:
            picker.optionChanged.connect(callback)
        return picker

    def _folder_row(self, parent_layout, label, key):
        row = QWidget(); h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4); h.setSpacing(12)
        lbl = QLabel(label); lbl.setObjectName("h3")
        lbl.setMinimumWidth(140); lbl.setMaximumWidth(160); h.addWidget(lbl)

        path_lbl = QLabel(S[key]); path_lbl.setObjectName("subSm"); path_lbl.setWordWrap(True)
        h.addWidget(path_lbl, 1)

        btn = QPushButton("Browse\u2026"); btn.setFixedWidth(120)
        def browse():
            d = QFileDialog.getExistingDirectory(self, f"Select {label}", S[key])
            if d:
                d = os.path.abspath(d)
                path_lbl.setText(d)
                update_s(key, d)
                os.makedirs(S[key], exist_ok=True)
        btn.clicked.connect(browse)
        h.addWidget(btn)
        parent_layout.addWidget(row)

    # ---------------- PAGE 1 - CONFIGURATION ----------------
    def _build_config_page(self):
        page, lay = self._scroll_page()
        self._page_header(lay, "Configuration", "Folders, API keys, and processing settings.")

        c1 = Card("Folder Selectors", "Choose where inputs, outputs, and music")
        self._folder_row(c1.body(), "Input Folder",  "in_folder")
        self._folder_row(c1.body(), "Output Folder", "out_folder")
        self._folder_row(c1.body(), "Music Folder",  "mus_folder")
        lay.addWidget(c1)

        c2 = Card("API Keys", "Secure credentials for Gemini AI.")
        kr = QWidget(); h = QHBoxLayout(kr); h.setContentsMargins(0, 6, 0, 4); h.setSpacing(8)
        l = QLabel("Gemini API Key"); l.setObjectName("h3"); l.setMinimumWidth(140); h.addWidget(l)
        self._gem_entry = QLineEdit(S["gemini_key"])
        self._gem_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self._gem_entry.setPlaceholderText("AIzaSy\u2026")
        self._gem_entry.textChanged.connect(lambda t: update_s("gemini_key", t.strip(), trigger_save_popup=True))
        h.addWidget(self._gem_entry, 1)
        eye = QPushButton("\U0001F441"); eye.setFixedWidth(44); eye.setToolTip("Show / hide API key")
        eye.setStyleSheet("font-size: 14px;")
        def toggle():
            is_hidden = self._gem_entry.echoMode() == QLineEdit.EchoMode.Password
            self._gem_entry.setEchoMode(
                QLineEdit.EchoMode.Normal if is_hidden else QLineEdit.EchoMode.Password
            )
            # open eye when showing, closed eye (no-see) when hidden
            eye.setText("\U0001F441" if is_hidden else "\U0001F576")
        eye.clicked.connect(toggle); h.addWidget(eye)
        save_btn = QPushButton("Save Key"); save_btn.setObjectName("softAction"); save_btn.setFixedWidth(110)
        save_btn.clicked.connect(self._save_api_key_explicit)
        h.addWidget(save_btn)
        c2.body().addWidget(kr)
        lay.addWidget(c2)

        c3 = Card("Processing Mode", "Test-run option for safe testing.")
        self._toggle_row(c3.body(), "Test Mode",
                         "Skips AI calls and uses mock data - no API credits consumed.",
                         "test_mode", ACC2)
        lay.addWidget(c3)

        lay.addStretch(1)
        self._add_page("config", page)

    def _save_api_key_explicit(self):
        new_key = self._gem_entry.text().strip()
        update_s("gemini_key", new_key, trigger_save_popup=False)
        save_settings()
        if self._toast._shown:
            self._toast.slide_out()
        log("API Key successfully saved.", "success")
        QMessageBox.information(self, "Saved", "Your Gemini API Key has been saved successfully.")

    # ---------------- PAGE 2 - AI BRAIN ----------------
    def _build_prompt_page(self):
        page, lay = self._plain_page()
        self._page_header(lay, "Prompt Editor", "Modify the core instructions sent to AI.")

        card = QFrame(); card.setObjectName("card")
        cv = QVBoxLayout(card); cv.setContentsMargins(18, 14, 18, 14); cv.setSpacing(8)

        head = QWidget(); hh = QHBoxLayout(head); hh.setContentsMargins(0, 0, 0, 0)
        t = QLabel("Gemini Base Prompt"); t.setObjectName("h3"); hh.addWidget(t); hh.addStretch(1)
        reset = QPushButton("Reset to Default"); reset.setFixedHeight(28)
        reset.clicked.connect(self._reset_prompt)
        hh.addWidget(reset)
        cv.addWidget(head)
        line = QFrame(); line.setObjectName("hairline"); cv.addWidget(line)

        self._prompt_box = QPlainTextEdit()
        self._prompt_box.setObjectName("terminal")
        self._prompt_box.setPlainText(S["ai_prompt"])
        self._prompt_box.textChanged.connect(
            lambda: update_s("ai_prompt", self._prompt_box.toPlainText().strip())
        )
        cv.addWidget(self._prompt_box, 1)

        lay.addWidget(card, 1)
        self._add_page("prompt", page)

    def _reset_prompt(self):
        self._prompt_box.setPlainText(DEFAULT_PROMPT)
        update_s("ai_prompt", DEFAULT_PROMPT)

    # ---------------- PAGE 3 - AUDIO ----------------
    def _build_audio_page(self):
        page, lay = self._scroll_page()
        self._page_header(lay, "Audio Mixer", "Fine-tune audio levels and ducking mechanics.")

        c = Card("Volume Controls", "Balance between movie, voiceover, and music.")
        self._slider_row(c.body(), "Movie Volume (Base Level)", "vol_movie_base", 0.0, 2.0, 1.0)
        self._slider_row(c.body(), "Movie Volume (Intro)", "vol_movie_duck", 0.0, 2.0, 0.6)
        self._slider_row(c.body(), "AI Voiceover (Intro Hook)", "vol_vo_base", 0.0, 2.0, 1.0)
        self._slider_row(c.body(), "AI Voiceover (Background)", "vol_vo_duck", 0.0, 1.0, 0.06)
        self._slider_row(c.body(), "Background Music (Loop)", "vol_bgm", 0.0, 1.0, 0.05)
        lay.addWidget(c)
        lay.addStretch(1)
        self._add_page("audio", page)

    # ---------------- PAGE 4 - VIDEO/SUBS ----------------
    def _build_video_subs_page(self):
        page, lay = self._scroll_page()
        self._page_header(lay, "Video & Subtitle FX", "Visual rendering pipeline and subtitle styling.")

        c1 = Card("Video Pipeline", "Speed, blur, and color grading.")
        self._slider_row(c1.body(), "Clip Speed", "v_speed", 1.0, 2.0, 1.1)
        self._slider_row(c1.body(), "Background Blur Amount", "v_blur", 5, 50, 30, is_int=True)
        self._slider_row(c1.body(), "Background Brightness", "v_bg_bri", -0.5, 0.5, -0.03)
        self._slider_row(c1.body(), "Background Saturation", "v_bg_sat", 0.0, 2.0, 0.92)
        self._slider_row(c1.body(), "Foreground Brightness", "v_fg_bri", -0.5, 0.5, 0.01)
        self._slider_row(c1.body(), "Foreground Saturation", "v_fg_sat", 0.0, 2.0, 1.12)
        self._slider_row(c1.body(), "Foreground Contrast", "v_fg_con", 0.5, 1.5, 1.08)
        lay.addWidget(c1)

        c2 = Card("Subtitle Styling", "ASS format - font, size, colors.")
        self._font_combo_row(c2.body(), "Font Family", "sub_font", "Readfine", SUB_FONTS)
        self._slider_row(c2.body(), "Font Size", "sub_size", 40, 150, 92, is_int=True)
        self._slider_row(c2.body(), "Height ", "sub_margin_v", 50, 1000, 430, is_int=True)
        self._color_row(c2.body(), "Primary Color", "sub_pri", "#FFFFFF")
        self._color_row(c2.body(), "Secondary Color", "sub_sec", "#FFFF00")
        self._color_row(c2.body(), "Outline Color", "sub_out", "#000000")
        self._color_row(c2.body(), "Shadow Color", "sub_bak", "#000000")
        self._slider_row(c2.body(), "Outline Thickness", "sub_out_w", 0, 10, 3, is_int=True)
        self._slider_row(c2.body(), "Shadow Thickness", "sub_sha_w", 0, 10, 3, is_int=True)
        lay.addWidget(c2)

        c3 = Card("Logo Overlay", "Floating logo with transparency.")
        self._toggle_row(c3.body(), "Enable Logo Overlay",
                         "Show darklogo.png floating overlay on rendered clips (requires darklogo.png in app folder)",
                         "logo_enabled", GREEN)
        lay.addWidget(c3)

        lay.addStretch(1)
        self._add_page("video", page)

    # ---------------- PAGE 5 - ENGINE ----------------
    def _build_engine_page(self):
        page, lay = self._plain_page()
        self._page_header(lay, "Create", "Launch the batch processor - watch the magic.")

        hero = QFrame(); hero.setObjectName("card")
        hh = QHBoxLayout(hero); hh.setContentsMargins(22, 18, 22, 18); hh.setSpacing(12)
        lcol = QVBoxLayout(); lcol.setSpacing(2)
        title = QLabel("Ready to process"); title.setObjectName("h2"); lcol.addWidget(title)
        self._hero_sub = QLabel("Configure folders and API key, then launch the batch.")
        self._hero_sub.setObjectName("sub"); lcol.addWidget(self._hero_sub)
        lwrap = QWidget(); lwrap.setLayout(lcol); hh.addWidget(lwrap, 1)

        # Button container for Start Batch + Show in folder
        btn_container = QWidget()
        btn_h = QHBoxLayout(btn_container); btn_h.setContentsMargins(0, 0, 0, 0); btn_h.setSpacing(10)

        # Show in folder button (square folder icon)
        self._show_folder_btn = QPushButton()
        self._show_folder_btn.setObjectName("softAction")
        self._show_folder_btn.setFixedSize(52, 52)
        # Load folder icon from image
        folder_icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "folderlogo.png")
        if os.path.exists(folder_icon_path):
            icon = QIcon(folder_icon_path)
            self._show_folder_btn.setIcon(icon)
            self._show_folder_btn.setIconSize(QSize(28, 28))
        self._show_folder_btn.setStyleSheet(
            f"QPushButton {{ border-radius: 10px; "
            f"background: {CARD2}; border: 1px solid {BOR}; }}"
            f"QPushButton:hover {{ background: {CARD3}; border-color: {ACC}; }}"
            f"QPushButton:pressed {{ background: {ACC2}; }}"
        )
        # Add opacity effect to darken icon on hover
        self._folder_icon_opacity = QGraphicsOpacityEffect(self._show_folder_btn)
        self._folder_icon_opacity.setOpacity(1.0)
        self._show_folder_btn.setGraphicsEffect(self._folder_icon_opacity)
        self._show_folder_btn.enterEvent = lambda e: self._folder_icon_opacity.setOpacity(0.6)
        self._show_folder_btn.leaveEvent = lambda e: self._folder_icon_opacity.setOpacity(1.0)
        self._show_folder_btn.setToolTip("Open clips output folder in File Explorer")
        self._show_folder_btn.clicked.connect(self._on_show_in_folder)
        btn_h.addWidget(self._show_folder_btn)

        self._start_btn = QPushButton("Start Batch")
        self._start_btn.setObjectName("primary")
        self._start_btn.setFixedSize(220, 52)
        self._start_btn.setStyleSheet(
            f"QPushButton#primary {{ font-size: 20px; font-weight: 600; border-radius: 10px; "
            f"background: {ACC2}; color: #ffffff; border: 1px solid {ACC2}; letter-spacing: 0.3px; }}"
            f"QPushButton#primary:hover {{ background: {ACC_DK}; border-color: {ACC}; }}"
            f"QPushButton#primary:disabled {{ background: {CARD3}; color: {SUB_DK}; border-color: {BOR}; }}"
        )
        self._start_btn.clicked.connect(self._on_start_cancel)
        btn_h.addWidget(self._start_btn)
        hh.addWidget(btn_container)
        lay.addWidget(hero)

        pc = Card("Progress", "Live batch telemetry.")

        # Elapsed and Estimated time row - at the TOP of progress card
        time_row = QWidget(); tr_h = QHBoxLayout(time_row); tr_h.setContentsMargins(0, 0, 0, 8)
        self._elapsed_lbl = QLabel("Elapsed: 00:00:00")
        self._elapsed_lbl.setObjectName("subSm")
        self._elapsed_lbl.setStyleSheet(f"color: {ACC}; font-family: 'Cascadia Mono', Consolas, monospace; font-weight: 600;")
        tr_h.addWidget(self._elapsed_lbl, 1)
        self._eta_lbl = QLabel("ETA: --:--:--")
        self._eta_lbl.setObjectName("subSm")
        self._eta_lbl.setStyleSheet(f"color: {GREEN}; font-family: 'Cascadia Mono', Consolas, monospace; font-weight: 600;")
        self._eta_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        tr_h.addWidget(self._eta_lbl)
        pc.body().addWidget(time_row)

        mr = QWidget(); mh = QHBoxLayout(mr); mh.setContentsMargins(0, 0, 0, 0)
        self._micro_lbl = QLabel("Idle"); self._micro_lbl.setObjectName("sub"); mh.addWidget(self._micro_lbl, 1)
        self._micro_pct = QLabel("0%"); self._micro_pct.setObjectName("pctLabel"); mh.addWidget(self._micro_pct)
        pc.body().addWidget(mr)
        self._micro_bar = AnimatedProgressBar(color=ACC, height=8)
        pc.body().addWidget(self._micro_bar)
        pc.body().addSpacing(8)

        mr2 = QWidget(); mh2 = QHBoxLayout(mr2); mh2.setContentsMargins(0, 0, 0, 0)
        self._macro_lbl = QLabel("Overall \u00B7 Movie 0 / 0"); self._macro_lbl.setObjectName("h3"); mh2.addWidget(self._macro_lbl, 1)
        self._macro_pct = QLabel("0%"); self._macro_pct.setStyleSheet(f"color: {ACC2}; font-family: 'Cascadia Mono', Consolas, monospace;")
        mh2.addWidget(self._macro_pct)
        pc.body().addWidget(mr2)
        self._macro_bar = AnimatedProgressBar(color=ACC2, height=14)
        pc.body().addWidget(self._macro_bar)
        lay.addWidget(pc)

        tc = QFrame(); tc.setObjectName("card")
        tv = QVBoxLayout(tc); tv.setContentsMargins(18, 14, 18, 14); tv.setSpacing(8)
        head = QWidget(); hh = QHBoxLayout(head); hh.setContentsMargins(0, 0, 0, 0)
        t = QLabel("Live Terminal"); t.setObjectName("h3"); hh.addWidget(t); hh.addStretch(1)
        clr = QPushButton("Clear")
        clr.setFixedHeight(32); clr.setMinimumWidth(80)
        clr.clicked.connect(self._clear_terminal)
        hh.addWidget(clr)
        tv.addWidget(head)
        line = QFrame(); line.setObjectName("hairline"); tv.addWidget(line)

        self._term = QPlainTextEdit()
        self._term.setObjectName("terminal")
        self._term.setReadOnly(True)
        self._term.setStyleSheet(self._term.styleSheet() + f"QPlainTextEdit#terminal {{ color: {GREEN}; }}")
        tv.addWidget(self._term, 1)
        lay.addWidget(tc, 1)

        self._tlog("-" * 62, "dim")
        self._tlog("  CClip -  Ready", "success")
        self._tlog("  Configure folders + API key, then press Start Batch.", "info")
        self._tlog("-" * 62, "dim")

        self._add_page("engine", page)

    def _clear_terminal(self):
        self._term.clear()

    def _tlog(self, msg, color="normal"):
        color_map = {
            "error": RED, "warn": YEL, "success": GREEN,
            "info": ACC, "dim": SUB_DK, "mono": SUB_DK, "normal": GREEN,
        }
        c = color_map.get(color, GREEN)
        ts = datetime.now().strftime("%H:%M:%S")
        # Escape HTML-sensitive chars
        safe = (msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        self._term.appendHtml(f'<span style="color:{SUB_DK};">[{ts}]</span>  <span style="color:{c};">{safe}</span>')
        # Also write to log file (strip HTML for log)
        self._write_log(msg)

    # ---------------- PAGE 6 - ANALYTICS ----------------
    def _build_analytics_page(self):
        page, lay = self._plain_page()
        # Set page background to match theme
        page.setStyleSheet(f"QWidget#pageBg {{ background: {BG}; }}")
        self._page_header(lay, "Analytics Dashboard", "YouTube channel insights - performance, metrics, and content analysis.")

        # ========== HEADER: CHANNEL STATS ==========
        header_card = QFrame(); header_card.setObjectName("card")
        hh = QHBoxLayout(header_card); hh.setContentsMargins(20, 16, 20, 16); hh.setSpacing(16)

        # Channel info (left side)
        ch_info = QVBoxLayout(); ch_info.setSpacing(4)
        self._ch_name_lbl = QLabel("Not Connected")
        self._ch_name_lbl.setObjectName("h2")
        self._ch_name_lbl.setStyleSheet(f"color: {TEXT}; font-weight: 700;")
        ch_info.addWidget(self._ch_name_lbl)

        self._ch_subtitle = QLabel("Authenticate via YouTube Panel to see live data")
        self._ch_subtitle.setObjectName("sub")
        ch_info.addWidget(self._ch_subtitle)
        hh.addLayout(ch_info, 1)

        # Stats counters
        stats_layout = QHBoxLayout(); stats_layout.setSpacing(12)
        self._ch_subs = self._stat_badge(stats_layout, "0", "Subscribers", GREEN)
        self._ch_views = self._stat_badge(stats_layout, "0", "Total Views", ACC)
        self._ch_videos = self._stat_badge(stats_layout, "0", "Videos", YEL)
        self._ch_watch_hours = self._stat_badge(stats_layout, "0", "Watch Hours", CYAN)
        self._quota_badge = self._stat_badge(stats_layout, "0/10K", "Quota Used", SUB)
        hh.addLayout(stats_layout)

        lay.addWidget(header_card)

        # ========== CONTROLS ROW ==========
        controls = QWidget(); cl = QHBoxLayout(controls); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(12)

        # Refresh button with spinner
        self._refresh_btn = QPushButton("\u21bb  Refresh Analytics")
        self._refresh_btn.setObjectName("primary")
        self._refresh_btn.setFixedHeight(42)
        self._refresh_btn.setFixedWidth(200)
        self._refresh_btn.clicked.connect(self._on_refresh_analytics)
        cl.addWidget(self._refresh_btn)

        # Loading spinner label (hidden by default)
        self._loading_lbl = QLabel("")
        self._loading_lbl.setStyleSheet(f"color: {ACC}; font-size: 13px;")
        cl.addWidget(self._loading_lbl)
        cl.addStretch(1)

        # Date range selector (styled like FontPicker)
        self._date_range_combo = self._create_styled_combo(
            ["Last 7 Days", "Last 28 Days", "Last 90 Days", "Lifetime"],
            "Last 28 Days",
            width=160,
            callback=lambda text: self._update_analytics_ui()
        )
        cl.addWidget(self._date_range_combo)

        # Graphs Toggle Button
        self._graphs_toggle = QPushButton("Show Insights")
        self._graphs_toggle.setObjectName("softAction")
        self._graphs_toggle.setFixedHeight(42)
        self._graphs_toggle.setFixedWidth(160)
        self._graphs_toggle.setCheckable(True)
        self._graphs_toggle.clicked.connect(self._on_toggle_graphs)
        cl.addWidget(self._graphs_toggle)

        lay.addWidget(controls)

        # ========== COLLAPSIBLE GRAPHS SECTION ==========
        self._graphs_container = QWidget()
        self._graphs_container.setVisible(False)
        graphs_scroll = QScrollArea()
        graphs_scroll.setWidgetResizable(True)
        graphs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        graphs_scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}")

        graphs_widget = QWidget()
        graphs_widget.setStyleSheet(f"background: {BG};")
        gl = QVBoxLayout(graphs_widget); gl.setContentsMargins(0, 0, 0, 0); gl.setSpacing(16)

        # SECTION 1: The Big Picture (Cumulative Growth)
        s1_card = Card("Growth Overview", "Channel trajectory and cumulative momentum")
        s1_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        s1_grid = QWidget(); s1l = QVBoxLayout(s1_grid); s1l.setContentsMargins(4, 4, 4, 4); s1l.setSpacing(14)

        # Total Views Graph
        views_card = QFrame()
        views_card.setObjectName("card")
        views_card.setStyleSheet(f"QFrame {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 8px; }}")
        views_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vvl = QVBoxLayout(views_card); vvl.setContentsMargins(12, 10, 12, 10)
        vtl = QLabel("Views Growth Trend"); vtl.setObjectName("h3"); vvl.addWidget(vtl)
        self._cumulative_views_fig = Figure(figsize=(10, 3.2), dpi=100, facecolor=CARD, edgecolor=CARD)
        self._cumulative_views_ax = self._cumulative_views_fig.add_subplot(111)
        self._cumulative_views_ax.set_facecolor(CARD)
        self._cumulative_views_canvas = FigureCanvas(self._cumulative_views_fig)
        self._cumulative_views_canvas.setStyleSheet(f"background: {CARD};")
        self._cumulative_views_canvas.setMinimumHeight(245)
        self._cumulative_views_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vvl.addWidget(self._cumulative_views_canvas, 1)
        s1l.addWidget(views_card, 1)

        # Total Subscribers Graph
        subs_card = QFrame()
        subs_card.setObjectName("card")
        subs_card.setStyleSheet(f"QFrame {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 8px; }}")
        subs_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ssl = QVBoxLayout(subs_card); ssl.setContentsMargins(12, 10, 12, 10)
        stl = QLabel("Subscriber Growth Trend"); stl.setObjectName("h3"); ssl.addWidget(stl)
        self._cumulative_subs_fig = Figure(figsize=(10, 3.2), dpi=100, facecolor=CARD, edgecolor=CARD)
        self._cumulative_subs_ax = self._cumulative_subs_fig.add_subplot(111)
        self._cumulative_subs_ax.set_facecolor(CARD)
        self._cumulative_subs_canvas = FigureCanvas(self._cumulative_subs_fig)
        self._cumulative_subs_canvas.setStyleSheet(f"background: {CARD};")
        self._cumulative_subs_canvas.setMinimumHeight(245)
        self._cumulative_subs_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ssl.addWidget(self._cumulative_subs_canvas, 1)
        s1l.addWidget(subs_card, 1)

        s1_card.body().addWidget(s1_grid)
        gl.addWidget(s1_card)

        # SECTION 2: The Daily Pulse (Daily Activity)
        s2_card = Card("Daily Pulse", "Recent activity across views, subscribers, and watch time")
        s2_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        s2_grid = QWidget(); s2l = QHBoxLayout(s2_grid); s2l.setContentsMargins(4, 4, 4, 4); s2l.setSpacing(10)

        # Views Per Day
        vpd_card = QFrame()
        vpd_card.setObjectName("card")
        vpd_card.setStyleSheet(f"QFrame {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 8px; }}")
        vpdl = QVBoxLayout(vpd_card); vpdl.setContentsMargins(12, 10, 12, 10)
        vpdtl = QLabel("Views Per Day"); vpdtl.setObjectName("h3"); vpdl.addWidget(vpdtl)
        self._daily_views_fig = Figure(figsize=(4.5, 3.0), dpi=100, facecolor=CARD, edgecolor=CARD)
        self._daily_views_ax = self._daily_views_fig.add_subplot(111)
        self._daily_views_ax.set_facecolor(CARD)
        self._daily_views_canvas = FigureCanvas(self._daily_views_fig)
        self._daily_views_canvas.setStyleSheet(f"background: {CARD};")
        self._daily_views_canvas.setAutoFillBackground(False)
        self._daily_views_canvas.setMinimumHeight(180)
        vpdl.addWidget(self._daily_views_canvas, 1)
        s2l.addWidget(vpd_card, 1)

        # Subs Per Day
        spd_card = QFrame()
        spd_card.setObjectName("card")
        spd_card.setStyleSheet(f"QFrame {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 8px; }}")
        spdl = QVBoxLayout(spd_card); spdl.setContentsMargins(12, 10, 12, 10)
        spdtl = QLabel("Subscribers Per Day"); spdtl.setObjectName("h3"); spdl.addWidget(spdtl)
        self._daily_subs_fig = Figure(figsize=(4.5, 3.0), dpi=100, facecolor=CARD, edgecolor=CARD)
        self._daily_subs_ax = self._daily_subs_fig.add_subplot(111)
        self._daily_subs_ax.set_facecolor(CARD)
        self._daily_subs_canvas = FigureCanvas(self._daily_subs_fig)
        self._daily_subs_canvas.setStyleSheet(f"background: {CARD};")
        self._daily_subs_canvas.setAutoFillBackground(False)
        self._daily_subs_canvas.setMinimumHeight(180)
        spdl.addWidget(self._daily_subs_canvas, 1)
        s2l.addWidget(spd_card, 1)

        # Watch Hours Per Day
        whpd_card = QFrame()
        whpd_card.setObjectName("card")
        whpd_card.setStyleSheet(f"QFrame {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 8px; }}")
        whpdl = QVBoxLayout(whpd_card); whpdl.setContentsMargins(12, 10, 12, 10)
        whpdtl = QLabel("Watch Hours Per Day"); whpdtl.setObjectName("h3"); whpdl.addWidget(whpdtl)
        self._daily_watch_fig = Figure(figsize=(4.5, 3.0), dpi=100, facecolor=CARD, edgecolor=CARD)
        self._daily_watch_ax = self._daily_watch_fig.add_subplot(111)
        self._daily_watch_ax.set_facecolor(CARD)
        self._daily_watch_canvas = FigureCanvas(self._daily_watch_fig)
        self._daily_watch_canvas.setStyleSheet(f"background: {CARD};")
        self._daily_watch_canvas.setAutoFillBackground(False)
        self._daily_watch_canvas.setMinimumHeight(180)
        whpdl.addWidget(self._daily_watch_canvas, 1)
        s2l.addWidget(whpd_card, 1)

        s2_card.body().addWidget(s2_grid)
        gl.addWidget(s2_card)

        # SECTION 3: Content Performance (Video Metrics + Demographics)
        s3_card = Card("Content Performance & Audience", "Shorts performance, distribution, and viewer profile")
        s3_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        s3_grid = QWidget(); s3l = QHBoxLayout(s3_grid); s3l.setContentsMargins(4, 4, 4, 4); s3l.setSpacing(10)

        # Left: Top Shorts + Traffic Sources
        left_col = QVBoxLayout(); left_col.setSpacing(12)

        top_shorts_card = QFrame()
        top_shorts_card.setObjectName("card")
        top_shorts_card.setStyleSheet(f"QFrame {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 8px; }}")
        tsc = QVBoxLayout(top_shorts_card); tsc.setContentsMargins(12, 10, 12, 10)
        tsctl = QLabel("Top Shorts This Week"); tsctl.setObjectName("h3"); tsc.addWidget(tsctl)
        self._top_shorts_list = QVBoxLayout()
        self._top_shorts_list.setSpacing(6)
        tsc.addLayout(self._top_shorts_list, 1)
        left_col.addWidget(top_shorts_card)

        traffic_card = QFrame()
        traffic_card.setObjectName("card")
        traffic_card.setStyleSheet(f"QFrame {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 8px; }}")
        tc = QVBoxLayout(traffic_card); tc.setContentsMargins(12, 10, 12, 10)
        tcl = QLabel("Traffic Sources"); tcl.setObjectName("h3"); tc.addWidget(tcl)
        self._traffic_sources_list = QVBoxLayout()
        self._traffic_sources_list.setSpacing(4)
        tc.addLayout(self._traffic_sources_list, 1)
        left_col.addWidget(traffic_card)
        s3l.addLayout(left_col, 1)

        # Middle: Retention
        mid_col = QVBoxLayout(); mid_col.setSpacing(12)

        retention_card = QFrame()
        retention_card.setObjectName("card")
        retention_card.setStyleSheet(f"QFrame {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 8px; }}")
        rc = QVBoxLayout(retention_card); rc.setContentsMargins(12, 10, 12, 10)
        rcl = QLabel("Shorts Retention"); rcl.setObjectName("h3"); rc.addWidget(rcl)
        self._retention_fig = Figure(figsize=(3.5, 2.8), dpi=100, facecolor=CARD, edgecolor=CARD)
        self._retention_ax = self._retention_fig.add_subplot(111)
        self._retention_ax.set_facecolor(CARD)
        self._retention_canvas = FigureCanvas(self._retention_fig)
        self._retention_canvas.setStyleSheet(f"background: {CARD};")
        self._retention_canvas.setAutoFillBackground(False)
        self._retention_canvas.setMinimumHeight(180)
        rc.addWidget(self._retention_canvas, 1)
        mid_col.addWidget(retention_card)
        s3l.addLayout(mid_col, 1)

        # Right: Demographics
        right_col = QVBoxLayout(); right_col.setSpacing(12)

        demo_card = QFrame()
        demo_card.setObjectName("card")
        demo_card.setStyleSheet(f"QFrame {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 8px; }}")
        dc = QVBoxLayout(demo_card); dc.setContentsMargins(12, 10, 12, 10)
        dcl = QLabel("Age & Gender"); dcl.setObjectName("h3"); dc.addWidget(dcl)
        self._demo_fig = Figure(figsize=(3.5, 2.8), dpi=100, facecolor=CARD, edgecolor=CARD)
        self._demo_ax = self._demo_fig.add_subplot(111)
        self._demo_ax.set_facecolor(CARD)
        self._demo_canvas = FigureCanvas(self._demo_fig)
        self._demo_canvas.setStyleSheet(f"background: {CARD};")
        self._demo_canvas.setAutoFillBackground(False)
        self._demo_canvas.setMinimumHeight(140)
        dc.addWidget(self._demo_canvas, 1)
        right_col.addWidget(demo_card)

        device_card = QFrame()
        device_card.setObjectName("card")
        device_card.setStyleSheet(f"QFrame {{ background: {CARD}; border: 1px solid {BOR}; border-radius: 8px; }}")
        devc = QVBoxLayout(device_card); devc.setContentsMargins(12, 10, 12, 10)
        devcl = QLabel("Device Type"); devcl.setObjectName("h3"); devc.addWidget(devcl)
        self._device_fig = Figure(figsize=(3.5, 2.8), dpi=100, facecolor=CARD, edgecolor=CARD)
        self._device_ax = self._device_fig.add_subplot(111)
        self._device_ax.set_facecolor(CARD)
        self._device_canvas = FigureCanvas(self._device_fig)
        self._device_canvas.setStyleSheet(f"background: {CARD};")
        self._device_canvas.setAutoFillBackground(False)
        self._device_canvas.setMinimumHeight(140)
        devc.addWidget(self._device_canvas, 1)
        right_col.addWidget(device_card)
        s3l.addLayout(right_col, 1)

        s3_card.body().addWidget(s3_grid)
        gl.addWidget(s3_card)

        graphs_widget.setLayout(gl)
        graphs_scroll.setWidget(graphs_widget)

        graphs_main_layout = QVBoxLayout(self._graphs_container)
        graphs_main_layout.setContentsMargins(0, 0, 0, 0)
        graphs_main_layout.addWidget(graphs_scroll)

        lay.addWidget(self._graphs_container)

        # ========== VIDEO LIST (Always Visible) ==========
        self._videos_card = Card("Video Performance", "All uploaded videos with metrics and copyright status")
        self._videos_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._videos_scroll = QScrollArea()
        self._videos_scroll.setWidgetResizable(True)
        self._videos_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._videos_scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}")

        self._videos_container = QWidget()
        self._videos_container.setObjectName("analyticsVideosContainer")
        self._videos_container.setStyleSheet(f"QWidget#analyticsVideosContainer {{ background: {BG}; }}")
        self._videos_layout = QVBoxLayout(self._videos_container)
        self._videos_layout.setContentsMargins(12, 12, 12, 12)
        self._videos_layout.setSpacing(8)
        self._videos_layout.addStretch(1)

        self._videos_scroll.setWidget(self._videos_container)
        self._videos_card.body().addWidget(self._videos_scroll, 1)
        lay.addWidget(self._videos_card, 1)

        # Store analytics data and state
        self._analytics_data = None
        self._analytics_thread = None
        self._is_graphs_expanded = False

        # Initialize empty state
        self._update_analytics_ui()
        self._add_page("analytics", page)

    def _counter_card(self, parent_layout, value, label, color):
        f = QFrame(); f.setObjectName("card")
        v = QVBoxLayout(f); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(2)
        val = CountUpLabel(value, color=color)
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(val)
        l = QLabel(label); l.setObjectName("subSm")
        l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(l)
        parent_layout.addWidget(f, 1)
        return val

    def _update_counters(self):
        try:
            self._c_movies.set_value_animated(S.get("total_movies", 0))
            self._c_shorts.set_value_animated(S.get("total_shorts", 0))
            self._c_funny.set_value_animated(S["clip_types"].get("Funny", 0))
            self._c_drama.set_value_animated(S["clip_types"].get("Dramatic", 0))
            self._c_bad.set_value_animated(S["clip_types"].get("Badass", 0))
        except Exception:
            pass

    def _update_charts(self):
        self._update_counters()
        chart_bg = CARD
        text_col = TEXT
        grid_col = BOR

        # Update old pie chart if it exists (for Engine page compatibility)
        if hasattr(self, '_pie_ax') and self._pie_ax:
            self._pie_ax.clear()
            ct = S["clip_types"]
            total_c = sum(ct.values())
            if total_c > 0:
                lbls = [k for k, v in ct.items() if v > 0]
                sizes = [v for k, v in ct.items() if v > 0]
                color_map = {"Funny": YEL, "Dramatic": CYAN, "Badass": RED}
                colors = [color_map.get(k, ACC) for k in lbls]
                wedges, texts, autos = self._pie_ax.pie(
                    sizes, labels=lbls, colors=colors, autopct="%1.0f%%",
                    startangle=140, textprops={"color": text_col, "fontsize": 9},
                    wedgeprops={"linewidth": 2, "edgecolor": chart_bg}
                )
                for at in autos:
                    at.set_color(text_col); at.set_fontsize(9)
            else:
                self._pie_ax.text(0, 0, "No clips generated yet", ha="center", va="center", color=SUB_DK, fontsize=10)
                self._pie_ax.axis("off")
            self._pie_ax.set_facecolor(chart_bg)
            self._pie_fig.patch.set_facecolor(chart_bg)
            self._pie_canvas.draw_idle()

        # Update old line chart if it exists (for Engine page compatibility)
        if hasattr(self, '_line_ax') and self._line_ax:
            self._line_ax.clear()
            rt = S["render_times"]
            if rt:
                xs = list(range(1, len(rt) + 1))
                self._line_ax.plot(xs, rt, color=ACC2, linewidth=2.2, marker="o",
                                   markersize=5, markerfacecolor=ACC, markeredgecolor=ACC)
                self._line_ax.fill_between(xs, rt, alpha=0.18, color=ACC)
                avg = sum(rt) / len(rt)
                self._line_ax.axhline(avg, color=YEL, linewidth=1.2, linestyle="--", alpha=0.7)
                self._line_ax.text(xs[-1], avg + 1, f" avg {avg:.0f}s", color=YEL, fontsize=8)
                self._line_ax.set_xlabel("Clip #", color=SUB_DK, fontsize=8)
                self._line_ax.set_ylabel("Seconds", color=SUB_DK, fontsize=8)
                self._line_ax.tick_params(colors=SUB_DK, labelsize=7)
            else:
                self._line_ax.text(0.5, 0.5, "No render data yet", ha="center", va="center",
                                   transform=self._line_ax.transAxes, color=SUB_DK, fontsize=10)
            self._line_ax.set_facecolor(chart_bg)
            self._line_fig.patch.set_facecolor(chart_bg)
            self._line_ax.grid(True, color=SUB_DK, linestyle=":", linewidth=0.7, alpha=0.85)
            self._line_ax.set_axisbelow(True)
            for sp in self._line_ax.spines.values():
                sp.set_edgecolor(grid_col); sp.set_linewidth(0.6)
            self._line_ax.spines["top"].set_visible(False)
            self._line_ax.spines["right"].set_visible(False)
            self._line_canvas.draw_idle()

    def _do_copyright_scan(self):
        self._copy_box.setPlainText("  Scanning...")
        self._scan_thread = ScanThread()
        self._scan_thread.done.connect(self._on_scan_done)
        self._scan_thread.start()

    def _on_scan_done(self, results):
        self._copy_box.clear()
        if not results:
            self._copy_box.setPlainText("  No .mp4 files found in the output folder.\n  Generate clips first.")
            return
        lines = []
        for r in results:
            lines.append(f"  [{r['status']:>8}]  {r['file']}   ({r['size']})")
            for flag in r["flags"]:
                lines.append(f"              \u2514\u2500 {flag}")
        clean = sum(1 for r in results if "Clean" in r["status"])
        flagged = len(results) - clean
        lines.append("")
        lines.append("  " + ("\u2500" * 40))
        lines.append(f"  Total: {len(results)}   Clean: {clean}   Flagged: {flagged}")
        self._copy_box.setPlainText("\n".join(lines))

    def _stat_badge(self, parent, value, label, color):
        """Create a stat badge for the header."""
        badge = QFrame()
        badge.setObjectName("analyticsStatBadge")
        badge.setStyleSheet(
            f"QFrame#analyticsStatBadge {{ background: {CARD2}; border: 1px solid {BOR_SFT}; border-radius: 10px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        v = QVBoxLayout(badge); v.setContentsMargins(12, 8, 12, 8); v.setSpacing(2)
        val_lbl = QLabel(value)
        val_lbl.setObjectName("h2")
        val_lbl.setStyleSheet(f"color: {color}; font-weight: 700; font-size: 20px;")
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(val_lbl)
        lbl = QLabel(label)
        lbl.setObjectName("subSm")
        lbl.setStyleSheet(f"color: {SUB}; font-size: 10px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(lbl)
        parent.addWidget(badge)
        return val_lbl

    def _to_float(self, value, default=0.0):
        try:
            if value is None or value == "":
                return default
            return float(value)
        except Exception:
            return default

    def _to_int(self, value, default=0):
        return int(round(self._to_float(value, default)))

    def _format_compact_number(self, value, _pos=None):
        value = self._to_float(value, 0)
        sign = "-" if value < 0 else ""
        value = abs(value)
        if value >= 1_000_000_000:
            return f"{sign}{value / 1_000_000_000:.1f}B"
        if value >= 1_000_000:
            return f"{sign}{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{sign}{value / 1_000:.1f}K"
        if value == int(value):
            return f"{sign}{int(value)}"
        return f"{sign}{value:.1f}"

    def _current_analytics_limit(self):
        if not hasattr(self, "_date_range_combo"):
            return 28
        return {
            "Last 7 Days": 7,
            "Last 28 Days": 28,
            "Last 90 Days": 90,
            "Lifetime": None,
        }.get(self._date_range_combo.currentText(), 28)

    def _limit_analytics_series(self, rows):
        rows = list(rows or [])
        limit = self._current_analytics_limit()
        if limit is None:
            return rows
        return rows[-limit:]

    def _day_label(self, value, compact=False):
        if not value:
            return ""
        try:
            parsed = datetime.strptime(str(value)[:10], "%Y-%m-%d")
            return parsed.strftime("%d %b" if compact else "%a")
        except Exception:
            text = str(value)
            return text[-5:] if compact and len(text) >= 5 else text

    def _setup_analytics_axes(self, fig, ax, ylabel=None, grid_axis="y"):
        fig.patch.set_facecolor(CARD)
        ax.set_facecolor(CARD)
        ax.tick_params(colors=SUB_DK, labelsize=8, length=0)
        ax.grid(True, axis=grid_axis, color=BOR_SFT, linestyle="-", linewidth=0.7, alpha=0.55)
        ax.set_axisbelow(True)
        ax.yaxis.set_major_formatter(FuncFormatter(self._format_compact_number))
        if ylabel:
            ax.set_ylabel(ylabel, color=SUB_DK, fontsize=8)
        for sp in ax.spines.values():
            sp.set_edgecolor(BOR_SFT)
            sp.set_linewidth(0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    def _finish_analytics_chart(self, fig, canvas):
        fig.patch.set_facecolor(CARD)
        fig.patch.set_alpha(1.0)
        canvas.setStyleSheet(f"background: {CARD};")
        canvas.setAutoFillBackground(False)
        try:
            fig.tight_layout(pad=1.0)
        except Exception:
            pass
        canvas.draw_idle()

    def _draw_empty_chart(self, fig, ax, canvas, message):
        ax.clear()
        fig.patch.set_facecolor(CARD)
        ax.set_facecolor(CARD)
        ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes, color=SUB_DK, fontsize=10)
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
        self._finish_analytics_chart(fig, canvas)

    def _clear_layout(self, layout):
        while layout.count() > 0:
            item = layout.takeAt(0)
            child = item.layout()
            if child:
                self._clear_layout(child)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _empty_list_row(self, text):
        row = QFrame()
        row.setObjectName("analyticsEmptyRow")
        row.setStyleSheet(
            f"QFrame#analyticsEmptyRow {{ background: {CARD2}; border: 1px solid {BOR_SFT}; border-radius: 10px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        h = QHBoxLayout(row); h.setContentsMargins(14, 12, 14, 12); h.setSpacing(10)
        dot = QLabel("—")
        dot.setStyleSheet(f"color: {SUB_DK}; font-size: 18px; font-weight: 700;")
        dot.setFixedWidth(24)
        dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(dot)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {SUB}; font-size: 12px;")
        h.addWidget(lbl, 1)
        return row

    def _update_cumulative_charts(self, cumulative_views, cumulative_subs):
        """Update the cumulative growth charts."""
        def draw_growth(fig, ax, canvas, rows, color, label, empty_text):
            rows = self._limit_analytics_series(rows)
            ax.clear()
            if not rows:
                self._draw_empty_chart(fig, ax, canvas, empty_text)
                return
            dates = [self._day_label(d.get("date", ""), compact=True) for d in rows]
            values = [self._to_float(d.get("value", 0)) for d in rows]
            xs = list(range(len(values)))
            self._setup_analytics_axes(fig, ax, label)
            ax.plot(xs, values, color=color, linewidth=2.6, solid_capstyle="round")
            ax.fill_between(xs, values, min(values) if values else 0, alpha=0.18, color=color)
            ax.scatter(xs[-1], values[-1], s=34, color=color, edgecolor=CARD, linewidth=1.2, zorder=5)
            ax.annotate(self._format_compact_number(values[-1]), xy=(xs[-1], values[-1]), xytext=(-8, 8),
                        textcoords="offset points", ha="right", color=color, fontsize=8, fontweight="bold")
            step = max(1, len(xs) // 5)
            ticks = xs[::step]
            if xs[-1] not in ticks:
                ticks.append(xs[-1])
            ax.set_xticks(ticks)
            ax.set_xticklabels([dates[i] for i in ticks], color=SUB_DK, fontsize=8)
            if len(xs) == 1:
                ax.set_xlim(-0.5, 0.5)
            else:
                ax.set_xlim(xs[0], xs[-1])
            self._finish_analytics_chart(fig, canvas)

        draw_growth(self._cumulative_views_fig, self._cumulative_views_ax, self._cumulative_views_canvas,
                    cumulative_views, ACC, "Views", "No growth data yet")
        draw_growth(self._cumulative_subs_fig, self._cumulative_subs_ax, self._cumulative_subs_canvas,
                    cumulative_subs, GREEN, "Subscribers", "No subscriber data yet")

    def _update_daily_charts(self, daily_views, daily_subs, daily_watch):
        """Update the daily activity charts."""
        self._daily_views_ax.clear()
        rows = self._limit_analytics_series(daily_views)
        if rows:
            labels = [self._day_label(d.get("day", ""), compact=self._current_analytics_limit() != 7) for d in rows]
            values = [self._to_float(d.get("views", 0)) for d in rows]
            xs = list(range(len(values)))
            self._setup_analytics_axes(self._daily_views_fig, self._daily_views_ax, "Views")
            self._daily_views_ax.bar(xs, values, color=ACC, alpha=0.9, width=0.68)
            step = max(1, len(xs) // 7)
            self._daily_views_ax.set_xticks(xs[::step])
            self._daily_views_ax.set_xticklabels(labels[::step], color=SUB_DK, fontsize=8, rotation=0)
            self._finish_analytics_chart(self._daily_views_fig, self._daily_views_canvas)
        else:
            self._draw_empty_chart(self._daily_views_fig, self._daily_views_ax, self._daily_views_canvas, "No views data")

        self._daily_subs_ax.clear()
        rows = self._limit_analytics_series(daily_subs)
        if rows:
            labels = [self._day_label(d.get("day", ""), compact=self._current_analytics_limit() != 7) for d in rows]
            values = [self._to_float(d.get("subscribersGained", 0)) - self._to_float(d.get("subscribersLost", 0)) for d in rows]
            xs = list(range(len(values)))
            colors = [GREEN if v >= 0 else RED for v in values]
            self._setup_analytics_axes(self._daily_subs_fig, self._daily_subs_ax, "Net Subs")
            self._daily_subs_ax.axhline(0, color=BOR, linewidth=1)
            self._daily_subs_ax.bar(xs, values, color=colors, alpha=0.9, width=0.68)
            step = max(1, len(xs) // 7)
            self._daily_subs_ax.set_xticks(xs[::step])
            self._daily_subs_ax.set_xticklabels(labels[::step], color=SUB_DK, fontsize=8, rotation=0)
            self._finish_analytics_chart(self._daily_subs_fig, self._daily_subs_canvas)
        else:
            self._draw_empty_chart(self._daily_subs_fig, self._daily_subs_ax, self._daily_subs_canvas, "No subscriber data")

        self._daily_watch_ax.clear()
        rows = self._limit_analytics_series(daily_watch)
        if rows:
            labels = [self._day_label(d.get("day", ""), compact=self._current_analytics_limit() != 7) for d in rows]
            values = [self._to_float(d.get("estimatedMinutesWatched", 0)) / 60 for d in rows]
            xs = list(range(len(values)))
            self._setup_analytics_axes(self._daily_watch_fig, self._daily_watch_ax, "Hours")
            self._daily_watch_ax.plot(xs, values, color=CYAN, linewidth=2.4, marker="o", markersize=3.5)
            self._daily_watch_ax.fill_between(xs, values, 0, alpha=0.16, color=CYAN)
            step = max(1, len(xs) // 7)
            self._daily_watch_ax.set_xticks(xs[::step])
            self._daily_watch_ax.set_xticklabels(labels[::step], color=SUB_DK, fontsize=8, rotation=0)
            self._finish_analytics_chart(self._daily_watch_fig, self._daily_watch_canvas)
        else:
            self._draw_empty_chart(self._daily_watch_fig, self._daily_watch_ax, self._daily_watch_canvas, "No watch-time data")

    def _update_retention_chart(self, retention_data):
        """Update the retention pie chart."""
        self._retention_ax.clear()
        if not retention_data:
            self._draw_empty_chart(self._retention_fig, self._retention_ax, self._retention_canvas, "No retention data")
            return
        stayed = max(0, min(100, self._to_float(retention_data.get("stayed", 0))))
        swiped = max(0, min(100, self._to_float(retention_data.get("swiped", 100 - stayed))))
        total = stayed + swiped
        if total <= 0:
            self._draw_empty_chart(self._retention_fig, self._retention_ax, self._retention_canvas, "No retention data")
            return
        stayed = stayed / total * 100
        swiped = 100 - stayed
        self._retention_fig.patch.set_facecolor(CARD)
        self._retention_ax.set_facecolor(CARD)
        wedges, _, autos = self._retention_ax.pie(
            [stayed, swiped],
            labels=None,
            colors=[GREEN, RED],
            autopct=lambda pct: f"{pct:.0f}%" if pct >= 5 else "",
            startangle=90,
            counterclock=False,
            pctdistance=0.67,
            textprops={"color": TEXT, "fontsize": 9, "fontweight": "bold"},
            wedgeprops={"linewidth": 2, "edgecolor": CARD}
        )
        self._retention_ax.legend(
            wedges,
            [f"Stayed {stayed:.0f}%", f"Swiped {swiped:.0f}%"],
            loc="lower center",
            bbox_to_anchor=(0.5, -0.08),
            ncol=2,
            frameon=False,
            fontsize=8,
            labelcolor=SUB
        )
        self._retention_ax.axis("equal")
        for at in autos:
            at.set_color(TEXT)
        for sp in self._retention_ax.spines.values():
            sp.set_visible(False)
        self._finish_analytics_chart(self._retention_fig, self._retention_canvas)

    def _update_device_chart(self, device_data):
        """Update the device type doughnut chart."""
        self._device_ax.clear()
        items = [(str(k).replace("_", " ").title(), self._to_float(v)) for k, v in (device_data or {}).items() if self._to_float(v) > 0]
        items = sorted(items, key=lambda x: x[1], reverse=True)[:5]
        if not items:
            self._draw_empty_chart(self._device_fig, self._device_ax, self._device_canvas, "No device data")
            return
        labels = [x[0] for x in items]
        values = [x[1] for x in items]
        colors = [ACC, GREEN, YEL, CYAN, RED][:len(values)]
        self._device_fig.patch.set_facecolor(CARD)
        self._device_ax.set_facecolor(CARD)
        wedges, _, autos = self._device_ax.pie(
            values, labels=None, colors=colors, autopct=lambda pct: f"{pct:.0f}%" if pct >= 7 else "",
            startangle=90, pctdistance=0.78, textprops={"color": TEXT, "fontsize": 8, "fontweight": "bold"},
            wedgeprops={"linewidth": 2, "edgecolor": CARD, "width": 0.42}
        )
        self._device_ax.text(0, 0, self._format_compact_number(sum(values)), ha="center", va="center",
                             color=TEXT, fontsize=11, fontweight="bold")
        self._device_ax.legend(wedges, labels, loc="lower center", bbox_to_anchor=(0.5, -0.12),
                               ncol=2, frameon=False, fontsize=7, labelcolor=SUB)
        self._device_ax.axis("equal")
        for at in autos:
            at.set_color(TEXT)
        self._finish_analytics_chart(self._device_fig, self._device_canvas)

    def _update_demographics_chart(self, demo_data):
        """Update the age and gender demographics chart."""
        self._demo_ax.clear()
        if not demo_data or not (demo_data.get("age") or demo_data.get("gender")):
            self._draw_empty_chart(self._demo_fig, self._demo_ax, self._demo_canvas, "No demographic data")
            return
        age_data = demo_data.get("age", {})
        gender_data = demo_data.get("gender", {})
        age_map = [
            ("13-17", "AGE_13_17"), ("18-24", "AGE_18_24"), ("25-34", "AGE_25_34"),
            ("35-44", "AGE_35_44"), ("45-54", "AGE_45_54"), ("55-64", "AGE_55_64"), ("65+", "AGE_65_")
        ]
        labels = [label for label, key in age_map if self._to_float(age_data.get(key, 0)) > 0]
        values = [self._to_float(age_data.get(key, 0)) for label, key in age_map if self._to_float(age_data.get(key, 0)) > 0]
        if not values and gender_data:
            labels = [str(k).replace("_", " ").title() for k in gender_data.keys()]
            values = [self._to_float(v) for v in gender_data.values()]
        if not values:
            self._draw_empty_chart(self._demo_fig, self._demo_ax, self._demo_canvas, "No demographic data")
            return
        xs = list(range(len(values)))
        self._setup_analytics_axes(self._demo_fig, self._demo_ax, "Viewer %")
        bars = self._demo_ax.bar(xs, values, color=[ACC, BLUE, CYAN, GREEN, YEL, RED, ACC2][:len(values)], alpha=0.9, width=0.62)
        self._demo_ax.set_xticks(xs)
        self._demo_ax.set_xticklabels(labels, color=SUB_DK, fontsize=7, rotation=0)
        self._demo_ax.set_ylim(0, max(values) * 1.28 if max(values) > 0 else 10)
        for bar, value in zip(bars, values):
            self._demo_ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{value:.0f}%",
                               ha="center", va="bottom", color=TEXT, fontsize=7)
        if gender_data:
            gender_text = " · ".join(f"{str(k).replace('_', ' ').title()} {self._to_float(v):.0f}%" for k, v in gender_data.items())
            self._demo_ax.text(0.98, 0.95, gender_text, transform=self._demo_ax.transAxes, ha="right", va="top",
                               color=SUB, fontsize=7)
        self._finish_analytics_chart(self._demo_fig, self._demo_canvas)

    def _update_top_shorts_list(self, top_shorts):
        """Update the top shorts list."""
        self._clear_layout(self._top_shorts_list)
        if not top_shorts:
            self._top_shorts_list.addWidget(self._empty_list_row("No Shorts data available yet. Refresh analytics after uploading Shorts."))
            return
        max_views = max(self._to_float(s.get("views", 0)) for s in top_shorts[:5]) or 1
        for i, short in enumerate(top_shorts[:5]):
            row = QFrame()
            row.setObjectName("analyticsMiniRow")
            row.setStyleSheet(
                f"QFrame#analyticsMiniRow {{ background: {CARD2}; border: 1px solid {BOR_SFT}; border-radius: 10px; }}"
                f"QFrame#analyticsMiniRow:hover {{ background: {CARD3}; border-color: {BOR}; }}"
                f"QLabel {{ background: transparent; border: none; }}"
            )
            h = QHBoxLayout(row); h.setContentsMargins(10, 8, 10, 8); h.setSpacing(10)
            rank = QLabel(f"#{i+1}")
            rank.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rank.setFixedSize(34, 34)
            rank.setStyleSheet(f"color: {ACC}; background: {CARD}; border: 1px solid {BOR}; border-radius: 17px; font-weight: 800;")
            h.addWidget(rank)
            title_col = QVBoxLayout(); title_col.setSpacing(4)
            title_text = short.get("title", "Untitled")
            title = QLabel(title_text)
            title.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: 600;")
            title.setToolTip(title_text)
            title.setWordWrap(True)
            title_col.addWidget(title)
            bar_wrap = QFrame()
            bar_wrap.setFixedHeight(5)
            bar_wrap.setStyleSheet(f"background: {CARD}; border-radius: 2px;")
            bar = QFrame(bar_wrap)
            bar.setGeometry(0, 0, max(4, int(120 * self._to_float(short.get("views", 0)) / max_views)), 5)
            bar.setStyleSheet(f"background: {ACC}; border-radius: 2px;")
            title_col.addWidget(bar_wrap)
            h.addLayout(title_col, 1)
            views = QLabel(f"{self._to_int(short.get('views', 0)):,}")
            views.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            views.setStyleSheet(f"color: {ACC}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; font-weight: 700;")
            h.addWidget(views)
            self._top_shorts_list.addWidget(row)

    def _update_traffic_sources_list(self, traffic_data):
        """Update the traffic sources list."""
        self._clear_layout(self._traffic_sources_list)
        if not traffic_data:
            self._traffic_sources_list.addWidget(self._empty_list_row("No traffic-source data available for the selected period."))
            return
        source_names = {
            "ANNOTATION": "Annotations",
            "CAMPAIGN_CARD": "Cards",
            "END_SCREEN": "End Screens",
            "HASHTAG": "Hashtags",
            "NOTIFICATION": "Notifications",
            "PLAYLIST": "Playlists",
            "RELATED_VIDEO": "Related Videos",
            "SEARCH": "YouTube Search",
            "SUBSCRIBER": "Subscriptions",
            "VIDEO_REMIXES": "Remixes",
            "YOUTUBE_CHANNEL": "Channel Pages",
            "YOUTUBE_HOME": "Home Page",
            "YOUTUBE_SHORTS": "Shorts Feed",
            "EXT_URL": "External",
            "NO_LINK_EMBEDDED": "Embedded Player",
            "NO_LINK_OTHER": "Direct/Other"
        }
        clean_items = [(source, self._to_float(views)) for source, views in traffic_data.items() if self._to_float(views) > 0]
        total = sum(v for _, v in clean_items)
        sorted_sources = sorted(clean_items, key=lambda x: x[1], reverse=True)[:6]
        if total <= 0 or not sorted_sources:
            self._traffic_sources_list.addWidget(self._empty_list_row("No traffic-source data available for the selected period."))
            return
        for source, views in sorted_sources:
            row = QFrame()
            row.setObjectName("trafficRow")
            row.setStyleSheet(
                f"QFrame#trafficRow {{ background: {CARD2}; border: 1px solid {BOR_SFT}; border-radius: 10px; }}"
                f"QLabel {{ background: transparent; border: none; }}"
            )
            v = QVBoxLayout(row); v.setContentsMargins(10, 8, 10, 8); v.setSpacing(6)
            h = QHBoxLayout(); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
            name = QLabel(source_names.get(source, source))
            name.setStyleSheet(f"color: {TEXT}; font-size: 11px; font-weight: 600;")
            h.addWidget(name, 1)
            pct = views / total * 100
            value = QLabel(f"{pct:.0f}% · {self._format_compact_number(views)}")
            value.setStyleSheet(f"color: {ACC}; font-size: 10px; font-weight: 700;")
            h.addWidget(value)
            v.addLayout(h)
            bar_wrap = QFrame()
            bar_wrap.setFixedHeight(5)
            bar_wrap.setStyleSheet(f"background: {CARD}; border-radius: 2px;")
            bar = QFrame(bar_wrap)
            bar.setGeometry(0, 0, max(4, int(180 * pct / 100)), 5)
            bar.setStyleSheet(f"background: {ACC}; border-radius: 2px;")
            v.addWidget(bar_wrap)
            self._traffic_sources_list.addWidget(row)

    def _on_toggle_graphs(self):
        """Toggle the graphs section to cover full tab."""
        checked = self._graphs_toggle.isChecked()
        self._is_graphs_expanded = checked
        if checked:
            self._graphs_toggle.setText("Hide Insights")
            self._graphs_container.setVisible(True)
            if hasattr(self, '_videos_card'):
                self._videos_card.setVisible(False)
            self._graphs_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        else:
            self._graphs_toggle.setText("Show Insights")
            self._graphs_container.setVisible(False)
            if hasattr(self, '_videos_card'):
                self._videos_card.setVisible(True)
        if self._analytics_data and checked:
            self._update_analytics_ui()

    def _on_refresh_analytics(self):
        """Start the analytics refresh process."""
        if self._analytics_thread and self._analytics_thread.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("Loading...")
        self._loading_lbl.setText("\u23F3 Fetching data from YouTube API...")
        self._analytics_thread = AnalyticsFetchThread(self)
        self._analytics_thread.progress.connect(self._on_analytics_progress)
        self._analytics_thread.finished_ok.connect(self._on_analytics_complete)
        self._analytics_thread.error.connect(self._on_analytics_error)
        self._analytics_thread.start()

    def _on_analytics_progress(self, msg):
        """Update loading status."""
        self._loading_lbl.setText(f"\u23F3 {msg}")

    def _on_analytics_complete(self, data, used_quota, total_quota):
        """Handle successful analytics fetch."""
        self._analytics_data = data
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("\u21bb  Refresh Analytics")
        self._loading_lbl.setText(f"\u2713 Last updated: {datetime.now().strftime('%H:%M:%S')} | Quota: {used_quota:,}/{total_quota:,}")
        self._update_analytics_ui()

    def _on_analytics_error(self, error_msg):
        """Handle analytics fetch error."""
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("\u21bb  Refresh Analytics")
        self._loading_lbl.setText(f"\u2717 Error: {error_msg[:60]}")
        ModernDialog.ask(
            self, "Analytics Error",
            f"Failed to fetch analytics data:\n{error_msg}\n\nPlease ensure you're authenticated via the YouTube Panel.",
            [("OK", "ok", True)],
            icon_char="\u26A0",
            icon_color=RED
        )

    def _update_analytics_ui(self):
        """Update the UI with current analytics data."""
        if not self._analytics_data:
            self._show_empty_analytics_state()
            return
        
        data = self._analytics_data
        channel = data.get("channel", {})
        
        # Update header stats
        self._ch_name_lbl.setText(channel.get("title", "Unknown Channel"))
        self._ch_subtitle.setText(f"Channel ID: {channel.get('channel_id', 'N/A')[:20]}...")
        self._ch_subs.setText(f"{self._to_int(channel.get('subscribers', 0)):,}")
        self._ch_views.setText(f"{self._to_int(channel.get('total_views', 0)):,}")
        self._ch_videos.setText(f"{self._to_int(channel.get('total_videos', 0)):,}")
        
        # Calculate watch hours from videos data
        total_watch_hours = sum(self._to_float(v.get("watch_time_minutes", 0)) for v in data.get("videos", [])) / 60
        self._ch_watch_hours.setText(f"{int(total_watch_hours):,}")
        
        used, total = self._get_quota_status()
        self._quota_badge.setText(f"{used:,}/{total:,}")
        
        # Update all charts
        self._update_cumulative_charts(
            data.get("cumulative_views", []),
            data.get("cumulative_subs", [])
        )
        
        self._update_daily_charts(
            data.get("daily_views", []),
            data.get("daily_subs", []),
            data.get("daily_watch", [])
        )
        
        self._update_retention_chart(data.get("retention", {}))
        self._update_top_shorts_list(data.get("top_shorts", []))
        self._update_traffic_sources_list(data.get("traffic_sources", {}))
        self._update_demographics_chart(data.get("demographics", {}))
        self._update_device_chart(data.get("device_data", {}))
        self._update_video_list(data.get("videos", []))

    def _show_empty_analytics_state(self):
        """Show empty state when no data available."""
        self._ch_name_lbl.setText("Not Connected")
        self._ch_subtitle.setText("Click 'Refresh Analytics' to fetch YouTube data")
        self._ch_subs.setText("0")
        self._ch_views.setText("0")
        self._ch_videos.setText("0")
        self._ch_watch_hours.setText("0")
        used, total = self._get_quota_status()
        self._quota_badge.setText(f"{used:,}/{total:,}")
        
        # Clear top shorts list
        if hasattr(self, '_top_shorts_list'):
            self._clear_layout(self._top_shorts_list)
            self._top_shorts_list.addWidget(self._empty_list_row("No data yet. Click Refresh Analytics to load your YouTube channel data."))
        if hasattr(self, '_traffic_sources_list'):
            self._clear_layout(self._traffic_sources_list)
            self._traffic_sources_list.addWidget(self._empty_list_row("Traffic sources will appear after analytics refresh."))
        chart_targets = [
            ("_cumulative_views_fig", "_cumulative_views_ax", "_cumulative_views_canvas", "No growth data"),
            ("_cumulative_subs_fig", "_cumulative_subs_ax", "_cumulative_subs_canvas", "No subscriber data"),
            ("_daily_views_fig", "_daily_views_ax", "_daily_views_canvas", "No views data"),
            ("_daily_subs_fig", "_daily_subs_ax", "_daily_subs_canvas", "No subscriber data"),
            ("_daily_watch_fig", "_daily_watch_ax", "_daily_watch_canvas", "No watch-time data"),
            ("_retention_fig", "_retention_ax", "_retention_canvas", "No retention data"),
            ("_demo_fig", "_demo_ax", "_demo_canvas", "No demographic data"),
            ("_device_fig", "_device_ax", "_device_canvas", "No device data"),
        ]
        for fig_name, ax_name, canvas_name, message in chart_targets:
            if hasattr(self, fig_name) and hasattr(self, ax_name) and hasattr(self, canvas_name):
                self._draw_empty_chart(getattr(self, fig_name), getattr(self, ax_name), getattr(self, canvas_name), message)
        if hasattr(self, '_videos_layout'):
            self._update_video_list([])

    def _update_youtube_charts(self):
        """Update the pie charts with YouTube analytics data."""
        if not self._analytics_data:
            return
        self._update_analytics_ui()

    def _update_video_list(self, videos):
        """Update the video list display."""
        self._clear_layout(self._videos_layout)
        if not videos:
            self._videos_layout.addWidget(self._empty_list_row("No videos found on your channel. Refresh analytics after uploading videos."))
            self._videos_layout.addStretch(1)
            return
        videos_sorted = sorted(videos, key=lambda x: self._to_float(x.get("views", 0)), reverse=True)
        header = self._create_video_row(None, is_header=True)
        self._videos_layout.addWidget(header)
        for video in videos_sorted[:50]:
            row = self._create_video_row(video)
            self._videos_layout.addWidget(row)
        self._videos_layout.addStretch(1)

    def _create_video_row(self, video, is_header=False):
        """Create a video row widget."""
        row = QFrame()
        row.setObjectName("analyticsVideoHeader" if is_header else "analyticsVideoRow")
        if is_header:
            row.setStyleSheet(
                f"QFrame#analyticsVideoHeader {{ background: {CARD2}; border: 1px solid {BOR}; border-radius: 10px; }}"
                f"QLabel {{ background: transparent; border: none; }}"
            )
        else:
            row.setStyleSheet(
                f"QFrame#analyticsVideoRow {{ background: {CARD}; border: 1px solid {BOR_SFT}; border-radius: 12px; }}"
                f"QFrame#analyticsVideoRow:hover {{ background: {CARD2}; border-color: {BOR}; }}"
                f"QLabel {{ background: transparent; border: none; }}"
            )
        h = QHBoxLayout(row); h.setContentsMargins(12, 8, 12, 8); h.setSpacing(10)
        if is_header:
            headers = [("Video", 3), ("Views", 1), ("Likes", 1), ("Comments", 1), ("Subs", 1), ("Status", 1)]
            for text, stretch in headers:
                lbl = QLabel(text)
                lbl.setStyleSheet(f"color: {SUB}; font-weight: 700; font-size: 11px; letter-spacing: 0.4px;")
                if text != "Video":
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                h.addWidget(lbl, stretch)
            return row
        vid_info = QHBoxLayout(); vid_info.setSpacing(10)

        # Play button that opens video link
        video_id = video.get("id", "")
        thumb = QPushButton("▶")
        thumb.setFixedSize(48, 36)
        thumb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        thumb.setStyleSheet(
            f"QPushButton {{ color: {ACC}; background: {CARD2}; border: 1px solid {BOR_SFT}; "
            f"border-radius: 8px; font-size: 16px; font-weight: 800; }}"
            f"QPushButton:hover {{ background: {ACC}; color: #ffffff; border-color: {ACC}; }}"
        )
        if video_id:
            thumb.setToolTip(f"Open youtube.com/shorts/{video_id}")
            thumb.clicked.connect(lambda checked, vid=video_id: webbrowser.open(f"https://youtube.com/shorts/{vid}"))
        vid_info.addWidget(thumb)
        tcol = QVBoxLayout(); tcol.setSpacing(3)
        full_title = video.get("title", "Untitled")
        title = QLabel(full_title)
        title.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: 650;")
        title.setToolTip(full_title)
        title.setWordWrap(True)
        tcol.addWidget(title)
        cat = video.get("category", "Uncategorized")
        cat_color = {"Funny": YEL, "Badass": RED, "Dramatic": CYAN}.get(cat, SUB_DK)
        date_text = video.get("published_at", "")[:10]
        cat_lbl = QLabel(f"● {cat}" + (f"  ·  {date_text}" if date_text else ""))
        cat_lbl.setStyleSheet(f"color: {cat_color}; font-size: 10px; font-weight: 600;")
        tcol.addWidget(cat_lbl)
        vid_info.addLayout(tcol)
        h.addLayout(vid_info, 3)
        views = QLabel(f"{self._to_int(video.get('views', 0)):,}")
        views.setAlignment(Qt.AlignmentFlag.AlignCenter)
        views.setStyleSheet(f"color: {TEXT}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; font-weight: 700;")
        h.addWidget(views, 1)
        likes = self._to_int(video.get('likes', 0))
        dislikes = self._to_int(video.get('dislikes', 0))
        like_ratio = f"{likes:,}" if dislikes <= 0 else f"{likes:,}/{dislikes:,}"
        likes_lbl = QLabel(like_ratio)
        likes_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        likes_lbl.setStyleSheet(f"color: {GREEN if likes > max(dislikes * 10, 0) else TEXT}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px;")
        likes_lbl.setToolTip(f"Likes: {likes:,} | Dislikes: {dislikes:,}")
        h.addWidget(likes_lbl, 1)
        comments = QLabel(f"{self._to_int(video.get('comments', 0)):,}")
        comments.setAlignment(Qt.AlignmentFlag.AlignCenter)
        comments.setStyleSheet(f"color: {TEXT}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px;")
        h.addWidget(comments, 1)
        subs = self._to_int(video.get('subscribers_gained', 0))
        subs_lbl = QLabel(f"+{subs:,}" if subs > 0 else "0")
        subs_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subs_lbl.setStyleSheet(f"color: {GREEN if subs > 0 else SUB_DK}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; font-weight: 700;")
        h.addWidget(subs_lbl, 1)
        status = video.get('copyright_status', 'Clean')
        status_icon = "\u2713" if status == "Clean" else "\u26A0" if status == "Age Restricted" else "\u2717"
        status_color = GREEN if status == "Clean" else YEL if status == "Age Restricted" else RED
        status_lbl = QLabel(f"{status_icon} {status}")
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_lbl.setStyleSheet(
            f"color: {status_color}; background: {CARD2}; border: 1px solid {BOR_SFT}; "
            f"border-radius: 8px; padding: 4px 8px; font-size: 10px; font-weight: 700;"
        )
        status_lbl.setToolTip(f"Privacy: {video.get('privacy_status', 'unknown')}")
        h.addWidget(status_lbl, 1)
        return row




    # ═══════════════════════════════════════════════════════════════════
    #  YOUTUBE ANALYTICS BACKEND  —  API Wrappers & Quota Ledger
    # ═══════════════════════════════════════════════════════════════════
    QUOTA_LEDGER_FILE = "quota_ledger.json"
    QUOTA_UNITS = {
        "videos.list": 1,
        "channels.list": 1,
        "youtubeAnalytics.reports.query": 1,
    }

    def _get_youtube_credentials(self):
        """Get authenticated YouTube credentials from token.json."""
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request as GRequest
            SCOPES = [
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/yt-analytics.readonly"
            ]
            creds = None
            token_path = "user_data/token.json"
            if os.path.exists(token_path):
                creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(GRequest())
                    with open(token_path, "w") as tf:
                        tf.write(creds.to_json())
                else:
                    return None
            return creds
        except Exception as e:
            print(f"Error getting credentials: {e}")
            return None

    def _load_quota_ledger(self):
        """Load the quota ledger from file."""
        if os.path.exists(self.QUOTA_LEDGER_FILE):
            try:
                with open(self.QUOTA_LEDGER_FILE, "r") as f:
                    return json.load(f)
            except:
                pass
        return {"daily_quota": 10000, "used_today": 0, "last_reset": datetime.now().isoformat(), "operations": []}

    def _save_quota_ledger(self, ledger):
        """Save the quota ledger to file."""
        try:
            with open(self.QUOTA_LEDGER_FILE, "w") as f:
                json.dump(ledger, f, indent=4)
        except Exception as e:
            print(f"Failed to save quota ledger: {e}")

    def _deduct_quota(self, operation, count=1):
        """Deduct quota units for an API operation."""
        ledger = self._load_quota_ledger()
        units = self.QUOTA_UNITS.get(operation, 1) * count
        ledger["used_today"] += units
        ledger["operations"].append({
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "units": units,
            "count": count
        })
        ledger["operations"] = ledger["operations"][-100:]
        self._save_quota_ledger(ledger)
        return ledger["used_today"], ledger["daily_quota"]

    def _get_quota_status(self):
        """Get current quota status."""
        ledger = self._load_quota_ledger()
        return ledger["used_today"], ledger["daily_quota"]

    def _categorize_video(self, video_data):
        """Categorize video into Funny, Badass, or Dramatic based on metadata."""
        title = video_data.get("title", "").lower()
        description = video_data.get("description", "").lower()
        tags = video_data.get("tags", [])
        tags_str = " ".join(tags).lower()
        combined = f"{title} {description} {tags_str}"
        clip_type = video_data.get("clip_type", "")
        if clip_type:
            if clip_type.lower() in ["funny", "comedy", "humor"]:
                return "Funny"
            elif clip_type.lower() in ["badass", "epic", "hype", "cool"]:
                return "Badass"
            elif clip_type.lower() in ["dramatic", "drama", "emotional", "sad"]:
                return "Dramatic"
        if any(kw in combined for kw in ["funny", "lol", "haha", "comedy", "joke", "laugh"]):
            return "Funny"
        elif any(kw in combined for kw in ["epic", "badass", "hype", "savage", "cool", "intense"]):
            return "Badass"
        elif any(kw in combined for kw in ["dramatic", "emotional", "sad", "tears", "deep", "powerful"]):
            return "Dramatic"
        return "Uncategorized"

    def _fetch_channel_stats(self, youtube, analytics):
        """Fetch channel statistics using YouTube Data API."""
        try:
            channels_response = youtube.channels().list(
                part="snippet,statistics,contentDetails",
                mine=True
            ).execute()
            if not channels_response.get("items"):
                return None
            channel = channels_response["items"][0]
            stats = channel["statistics"]
            return {
                "channel_id": channel["id"],
                "title": channel["snippet"].get("title", "Unknown"),
                "subscribers": int(stats.get("subscriberCount", 0)),
                "total_views": int(stats.get("viewCount", 0)),
                "total_videos": int(stats.get("videoCount", 0)),
                "thumbnail": channel["snippet"].get("thumbnails", {}).get("default", {}).get("url", "")
            }
        except Exception as e:
            print(f"Error fetching channel stats: {e}")
            return None

    def _fetch_video_list(self, youtube, channel_id, max_results=50):
        """Fetch list of videos for the channel."""
        try:
            channels_response = youtube.channels().list(
                part="contentDetails",
                id=channel_id
            ).execute()
            if not channels_response.get("items"):
                return []
            uploads_playlist_id = channels_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            videos = []
            next_page_token = None
            while len(videos) < max_results:
                playlist_params = {
                    "part": "snippet,contentDetails",
                    "playlistId": uploads_playlist_id,
                    "maxResults": min(50, max_results - len(videos))
                }
                if next_page_token:
                    playlist_params["pageToken"] = next_page_token
                playlist_response = youtube.playlistItems().list(**playlist_params).execute()
                video_ids = [item["contentDetails"]["videoId"] for item in playlist_response.get("items", [])]
                if video_ids:
                    video_response = youtube.videos().list(
                        part="snippet,statistics,contentDetails,status",
                        id=",".join(video_ids)
                    ).execute()
                    for video in video_response.get("items", []):
                        snippet = video["snippet"]
                        statistics = video["statistics"]
                        status = video["status"]
                        content_details = video["contentDetails"]
                        video_data = {
                            "id": video["id"],
                            "title": snippet.get("title", "Untitled"),
                            "description": snippet.get("description", ""),
                            "published_at": snippet.get("publishedAt", ""),
                            "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                            "tags": snippet.get("tags", []),
                            "views": int(statistics.get("viewCount", 0)),
                            "likes": int(statistics.get("likeCount", 0)),
                            "comments": int(statistics.get("commentCount", 0)),
                            "duration": content_details.get("duration", ""),
                            "privacy_status": status.get("privacyStatus", "unknown"),
                            "upload_status": status.get("uploadStatus", "unknown"),
                            "license": content_details.get("licensedContent", False),
                            "category": snippet.get("categoryId", "")
                        }
                        if "regionRestriction" in content_details:
                            video_data["copyright_status"] = "Region Restricted"
                        elif content_details.get("contentRating", {}).get("ytRating") == "ytAgeRestricted":
                            video_data["copyright_status"] = "Age Restricted"
                        else:
                            video_data["copyright_status"] = "Clean"
                        video_data["clip_type"] = self._get_clip_type_from_local(video["id"])
                        video_data["category"] = self._categorize_video(video_data)
                        videos.append(video_data)
                next_page_token = playlist_response.get("nextPageToken")
                if not next_page_token:
                    break
            return videos
        except Exception as e:
            print(f"Error fetching video list: {e}")
            return []

    def _get_clip_type_from_local(self, video_id):
        """Try to find clip type from local metadata files."""
        try:
            out_folder = S.get("out_folder", "clips_output")
            if not os.path.exists(out_folder):
                return ""
            for movie_dir in os.listdir(out_folder):
                full_dir = os.path.join(out_folder, movie_dir)
                if not os.path.isdir(full_dir):
                    continue
                for fname in os.listdir(full_dir):
                    if fname.endswith("_metadata.txt"):
                        meta_path = os.path.join(full_dir, fname)
                        try:
                            with open(meta_path, "r", encoding="utf-8") as f:
                                content = f.read()
                            if video_id in content:
                                clip_name = fname.replace("_metadata.txt", "")
                                # Check new format: SeriesName_X1_dd.mm.yy_hh.mm where X is F/B/D
                                if "_F" in clip_name:
                                    return "Funny"
                                elif "_B" in clip_name:
                                    return "Badass"
                                elif "_D" in clip_name:
                                    return "Dramatic"
                                # Fallback to old format detection
                                elif "funny" in clip_name.lower():
                                    return "Funny"
                                elif "badass" in clip_name.lower():
                                    return "Badass"
                                elif "dramatic" in clip_name.lower():
                                    return "Dramatic"
                        except:
                            pass
        except:
            pass
        return ""

    def _fetch_analytics_data(self, analytics, video_ids):
        """Fetch advanced analytics (dislikes, subscribers gained) for videos."""
        advanced_data = {}
        if not video_ids:
            return advanced_data
        try:
            batch_size = 200
            for i in range(0, len(video_ids), batch_size):
                batch = video_ids[i:i + batch_size]
                end_date = datetime.now().strftime("%Y-%m-%d")
                start_date = (datetime.now().replace(year=datetime.now().year - 1)).strftime("%Y-%m-%d")
                reports_response = analytics.reports().query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics="dislikes,subscribersGained,estimatedMinutesWatched",
                    dimensions="video",
                    filters=f"video=={','.join(batch)}"
                ).execute()
                if "rows" in reports_response:
                    headers = [h["name"] for h in reports_response.get("columnHeaders", [])]
                    for row in reports_response["rows"]:
                        video_id = row[0]
                        data = dict(zip(headers, row))
                        advanced_data[video_id] = {
                            "dislikes": int(data.get("dislikes", 0)),
                            "subscribers_gained": int(data.get("subscribersGained", 0)),
                            "watch_time_minutes": float(data.get("estimatedMinutesWatched", 0))
                        }
        except Exception as e:
            print(f"Error fetching analytics data: {e}")
        return advanced_data

    def _merge_video_data(self, videos, analytics_data):
        """Merge basic video data with advanced analytics."""
        for video in videos:
            video_id = video["id"]
            if video_id in analytics_data:
                video.update(analytics_data[video_id])
            else:
                video["dislikes"] = 0
                video["subscribers_gained"] = 0
                video["watch_time_minutes"] = 0
        return videos
    # ---------------- PAGE 7 - YOUTUBE ----------------
    def _build_youtube_page(self):
        page, lay = self._scroll_page()
        self._page_header(lay, "YouTube Control Panel", "Authenticate, set privacy, and upload.")

        ac = Card("Authentication Status", "OAuth credentials live in user_data/token.json.")
        ar = QWidget(); ah = QHBoxLayout(ar); ah.setContentsMargins(0, 6, 0, 6); ah.setSpacing(10)
        self._auth_dot = QLabel("\u25CF"); self._auth_dot.setStyleSheet(f"color: {RED}; font-size: 22px;")
        ah.addWidget(self._auth_dot)
        self._auth_lbl = QLabel("  Not authenticated  -  token.json not found in user_data/")
        self._auth_lbl.setObjectName("h3")
        self._auth_lbl.setStyleSheet(f"color: {RED}; font-weight: 600;")
        ah.addWidget(self._auth_lbl, 1)
        self._login_btn = QPushButton("Login to YouTube"); self._login_btn.setObjectName("softAction")
        self._login_btn.setFixedSize(200, 36)
        self._login_btn.clicked.connect(self._yt_login)
        ah.addWidget(self._login_btn)
        ac.body().addWidget(ar)

        if os.path.exists("user_data/token.json"):
            self._set_auth_ui(True)

        inst = QFrame()
        inst.setStyleSheet(
            f"QFrame {{ background: {CARD2}; border: 1px solid {BOR_SFT}; border-radius: 8px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        ih = QHBoxLayout(inst); ih.setContentsMargins(14, 12, 14, 12); ih.setSpacing(12)
        info = QLabel("\u24D8")
        info.setStyleSheet(f"color: {ACC}; font-size: 22px; background: transparent; border: none;")
        info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        info.setFixedWidth(24)
        ih.addWidget(info)
        iv = QVBoxLayout(); iv.setContentsMargins(0, 0, 0, 0); iv.setSpacing(4)
        it = QLabel("How to authenticate")
        it.setStyleSheet(f"color: {TEXT}; font-weight: 600; background: transparent; border: none;")
        iv.addWidget(it)
        il = QLabel("Download client_secrets.json from Google Cloud Console \u2192 APIs & Services \u2192 Credentials \u2192 OAuth 2.0 Client IDs \u2192 Desktop App. Place the file in the user_data/ folder.")
        il.setWordWrap(True)
        il.setStyleSheet(f"color: {SUB}; font-size: 12px; background: transparent; border: none;")
        iv.addWidget(il)
        ih.addLayout(iv, 1)
        ac.body().addWidget(inst)
        lay.addWidget(ac)

        pm = Card("Upload Settings", "Private drafts are safer during testing.")
        self._toggle_row(pm.body(), "Publish Mode - Public Upload",
                         "OFF = Private (safe)    ON = Public (visible worldwide - irreversible)",
                         "pub_mode", RED)
        self._toggle_row(pm.body(), "Auto-Upload After Render",
                         "Automatically upload all rendered clips when batch completes (requires authentication)",
                         "auto_upload", GREEN)
        lay.addWidget(pm)

        uc = Card("Batch Uploader", "Upload every pending clip in the output folder.")
        ur = QWidget(); uh = QHBoxLayout(ur); uh.setContentsMargins(0, 6, 0, 6)
        self._upload_btn = QPushButton("Upload All Pending"); self._upload_btn.setObjectName("softAction")
        self._upload_btn.setFixedSize(260, 46)
        self._upload_btn.clicked.connect(self._on_upload_all)
        uh.addWidget(self._upload_btn); uh.addStretch(1)
        uc.body().addWidget(ur)
        lay.addWidget(uc)

        lc = Card("Upload Log", "Real-time log of uploads.")
        self._yt_term = QPlainTextEdit(); self._yt_term.setObjectName("ytTerminal")
        self._yt_term.setReadOnly(True); self._yt_term.setFixedHeight(260)
        self._yt_term.setStyleSheet(self._yt_term.styleSheet() + f"QPlainTextEdit#ytTerminal {{ color: {ACC}; }}")
        self._yt_term.setPlainText("  YouTube upload log will appear here.\n  Authenticate first, then click 'Upload All Pending'.")
        lc.body().addWidget(self._yt_term)
        lay.addWidget(lc)

        lay.addStretch(1)
        self._add_page("youtube", page)

    def _set_auth_ui(self, ok):
        update_s("yt_authed", ok, trigger_save_popup=False)
        color = GREEN if ok else RED
        text = "  Authenticated  -  user_data/token.json found" if ok else "  Not authenticated  -  user_data/token.json not found"
        self._auth_dot.setStyleSheet(f"color: {color}; font-size: 22px;")
        self._auth_lbl.setStyleSheet(f"color: {color}; font-weight: 600;")
        self._auth_lbl.setText(text)
        self._login_btn.setText("Re-Authenticate" if ok else "Login to YouTube")

    def _yt_login(self):
        self._yt_write("  Opening browser for OAuth...\n  Follow the login steps.")
        self._auth_thread = AuthThread()
        self._auth_thread.success.connect(lambda: (self._set_auth_ui(True), self._yt_write("  Authentication successful.  user_data/token.json saved.")))
        self._auth_thread.error.connect(lambda msg: QMessageBox.critical(self, "Auth Error", msg))
        self._auth_thread.start()

    def _on_upload_all(self):
        self._yt_write("  Starting upload session...", clear=False)
        self._upload_btn.setText("Uploading\u2026"); self._upload_btn.setEnabled(False)
        self._upload_btn.setStyleSheet(f"QPushButton {{ background: {YEL}; color: #1c1c1c; border: 1px solid {YEL}; border-radius: 6px; }}")
        self._upload_thread = UploadThread()
        self._upload_thread.finished_ok.connect(self._on_upload_done)
        self._upload_thread.start()

    def _on_upload_done(self, ok):
        self._upload_btn.setText("Upload All Pending"); self._upload_btn.setEnabled(True)
        self._upload_btn.setStyleSheet("")  # fall back to QSS #softAction
        self._upload_btn.setObjectName("softAction")
        self._upload_btn.style().unpolish(self._upload_btn); self._upload_btn.style().polish(self._upload_btn)

    def _yt_write(self, msg, clear=True):
        if clear:
            self._yt_term.setPlainText(msg)
        else:
            self._yt_term.appendPlainText(msg)
        # Also write to log file with [YouTube] prefix
        self._write_log(f"[YouTube] {msg}")

    # ---------------- PAGE 8 - UI CONFIG ----------------
    def _build_uiconfig_page(self):
        page, lay = self._scroll_page()
        self._page_header(lay, "UI Configuration", "Theme, font, scale, and a custom palette builder.")

        tc = Card("Appearance", "Light, Dark, or build your own custom palette \u2014 changes apply instantly.")
        self._option_row(tc.body(), "Theme Mode", "ui_theme", ["Dark", "Light", "Custom"],
                         callback=lambda v: self.apply_live_theme())
        self._font_combo_row(tc.body(), "Font Family", "ui_font", "Segoe UI Variable", UI_FONTS,
                             callback=lambda v: self.apply_live_theme())
        lay.addWidget(tc)

        sc = Card("Scaling & Size", "Zoom the whole interface in or out.")
        self._slider_row(sc.body(), "UI Scale", "ui_scale", 0.8, 1.5, 1.0, is_int=False,
                         callback=lambda v: self._apply_live_theme_debounced())
        lay.addWidget(sc)

        cc = Card("Custom Theme Builder", "Active when Theme Mode = Custom. Pick any color you want \u2014 changes apply instantly.")
        on_col = lambda _=None: self.apply_live_theme()
        self._custom_color_row(cc.body(), "App Background",   "BG",       "#1c1c1c", on_col)
        self._custom_color_row(cc.body(), "Card Surface",     "CARD",     "#272727", on_col)
        self._custom_color_row(cc.body(), "Card Hover",       "CARD3",    "#323232", on_col)
        self._custom_color_row(cc.body(), "Sidebar",          "SIDEBAR",  "#1f1f1f", on_col)
        self._custom_color_row(cc.body(), "Accent (Primary)", "ACC2",     "#0078d4", on_col)
        self._custom_color_row(cc.body(), "Accent (Light)",   "ACC",      "#60cdff", on_col)
        self._custom_color_row(cc.body(), "Text",             "TEXT",     "#ffffff", on_col)
        self._custom_color_row(cc.body(), "Sub Text",         "SUB",      "#b0b0b0", on_col)
        self._custom_color_row(cc.body(), "Border",           "BOR",      "#3a3a3a", on_col)
        lay.addWidget(cc)

        lay.addStretch(1)
        self._add_page("uiconfig", page)

    def _custom_color_row(self, parent_layout, label, palette_key, default_hex, on_apply=None):
        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background: {CARD2}; border: 1px solid {BOR_SFT}; border-radius: 8px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        h = QHBoxLayout(row); h.setContentsMargins(12, 8, 12, 8); h.setSpacing(12)
        lbl = QLabel(label)
        lbl.setMinimumWidth(200); lbl.setMaximumWidth(260)
        lbl.setStyleSheet(f"color: {TEXT}; font-weight: 500; background: transparent; border: none;")
        h.addWidget(lbl)

        cur_hex = S.get("custom_theme", {}).get(palette_key, default_hex)

        sw = QFrame(); sw.setFixedSize(32, 32)
        sw.setStyleSheet(f"background: {cur_hex}; border: 2px solid {BOR}; border-radius: 8px;")
        h.addWidget(sw)

        hex_lbl = QLabel(cur_hex.upper())
        hex_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hex_lbl.setMinimumWidth(92)
        hex_lbl.setStyleSheet(
            f"color: {TEXT}; background: {CARD3}; border: 1px solid {BOR}; border-radius: 6px; "
            f"padding: 5px 10px; font-family: 'Cascadia Mono', Consolas, monospace; font-weight: 600;"
        )
        h.addWidget(hex_lbl)

        btn = QPushButton("Pick"); btn.setObjectName("softAction"); btn.setFixedWidth(110); btn.setFixedHeight(32)
        def pick():
            start = S.get("custom_theme", {}).get(palette_key, default_hex)
            col = ModernColorPicker.pick(self, start, f"Choose {label}")
            if col is not None:
                hexv = col.name().upper()
                sw.setStyleSheet(f"background: {hexv}; border: 2px solid {BOR}; border-radius: 8px;")
                hex_lbl.setText(hexv)
                ct = dict(S.get("custom_theme", {}))
                ct[palette_key] = hexv
                update_s("custom_theme", ct)
                if on_apply:
                    on_apply(hexv)
        btn.clicked.connect(pick)
        h.addWidget(btn); h.addStretch(1)
        parent_layout.addWidget(row)

    def _apply_live_theme_debounced(self):
        """Debounce rapid slider changes before re-theming."""
        if not hasattr(self, "_theme_timer") or self._theme_timer is None:
            self._theme_timer = QTimer(self)
            self._theme_timer.setSingleShot(True)
            self._theme_timer.setInterval(400)
            self._theme_timer.timeout.connect(self.apply_live_theme)
        self._theme_timer.stop()
        self._theme_timer.start()

    # ---------------- BATCH EVENTS ----------------
    def _on_show_in_folder(self):
        """Open the clips output folder in File Explorer."""
        folder_path = os.path.abspath(S["out_folder"])
        os.makedirs(folder_path, exist_ok=True)
        try:
            import subprocess
            subprocess.Popen(["explorer", folder_path], creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            log(f"Failed to open folder: {e}", "error")
            QMessageBox.warning(self, "Error", f"Could not open folder:\n{folder_path}")

    def _on_start_cancel(self):
        if self._running:
            CANCEL.set()
            self._start_btn.setText("Cancelling\u2026")
            self._start_btn.setEnabled(False)
            return

        if not S["gemini_key"] and not S["test_mode"]:
            QMessageBox.warning(self, "API Key Missing", "No Gemini API key entered!")
            self.show_page("config")
            return

        self._running = True
        CANCEL.clear()
        self._batch_start_time = time.time()

        self._start_btn.setText("Cancel Batch")
        self._start_btn.setObjectName("danger")
        self._start_btn.style().unpolish(self._start_btn); self._start_btn.style().polish(self._start_btn)
        self._start_btn.setEnabled(True)
        self._hero_sub.setText("Batch in progress - live updates below.")
        self.show_page("engine")

        self._tlog("", "dim")
        self._tlog("  > Batch started.", "success")

        # Start elapsed time timer
        if not hasattr(self, '_elapsed_timer') or self._elapsed_timer is None:
            self._elapsed_timer = QTimer(self)
            self._elapsed_timer.timeout.connect(self._update_elapsed_time)
        self._elapsed_timer.start(1000)  # Update every second

        self._batch_thread = BatchThread()
        self._batch_thread.progress.connect(self._on_progress)
        self._batch_thread.macro.connect(self._on_macro)
        self._batch_thread.finished_ok.connect(self._on_batch_done)
        self._batch_thread.error_signal.connect(lambda msg: self._tlog(msg, "error"))
        self._batch_thread.start()

    def _update_elapsed_time(self):
        """Update the elapsed time label during batch processing."""
        if not self._running or not hasattr(self, '_batch_start_time'):
            return
        elapsed = time.time() - self._batch_start_time
        elapsed_str = self._format_duration(elapsed)
        self._elapsed_lbl.setText(f"Elapsed: {elapsed_str}")

    def _format_duration(self, seconds):
        """Format seconds as HH:MM:SS."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _on_progress(self, cur, total, label):
        pct = cur / max(total, 1)
        self._micro_lbl.setText(label)
        self._micro_pct.setText(f"{int(pct*100)}%")
        self._micro_bar.animate_to(pct)
        self._update_eta(cur, total)

    def _on_macro(self, cur, total, movie_name):
        pct = (cur - 1) / max(total, 1)
        self._macro_lbl.setText(f"Overall \u00B7 Movie {cur} / {total}  -  {movie_name}")
        self._macro_pct.setText(f"{int(pct*100)}%")
        self._macro_bar.animate_to(pct)
        self._update_eta(cur, total, is_macro=True)

    def _update_eta(self, cur, total, is_macro=False):
        """Calculate and display ETA based on progress and elapsed time."""
        if not hasattr(self, '_batch_start_time') or cur <= 0 or total <= 0:
            return
        elapsed = time.time() - self._batch_start_time
        # Estimate total time: elapsed / (cur/total) = elapsed * total / cur
        estimated_total = elapsed * total / cur
        remaining = estimated_total - elapsed
        if remaining < 0:
            remaining = 0
        eta_str = self._format_duration(remaining)
        self._eta_lbl.setText(f"ETA: {eta_str}")

    def _on_batch_done(self, ok):
        self._running = False
        # Stop the elapsed timer
        if hasattr(self, '_elapsed_timer') and self._elapsed_timer:
            self._elapsed_timer.stop()
        # Reset time displays
        self._elapsed_lbl.setText("Elapsed: 00:00:00")
        self._eta_lbl.setText("ETA: --:--:--")

        self._start_btn.setText("Start Batch")
        self._start_btn.setObjectName("primary")
        self._start_btn.style().unpolish(self._start_btn); self._start_btn.style().polish(self._start_btn)
        self._start_btn.setEnabled(True)
        self._hero_sub.setText("Batch complete." if ok else "Batch stopped.")
        self._macro_bar.animate_to(1.0 if ok else 0.0)
        self._macro_pct.setText("100%" if ok else "0%")
        self._micro_bar.animate_to(1.0 if ok else 0.0)
        self._micro_pct.setText("100%" if ok else "0%")
        self._update_charts()
        self._sb_movies_lbl.setText(str(S['total_movies']))
        self._sb_shorts_lbl.setText(str(S['total_shorts']))

        # Auto-upload if enabled and batch completed successfully
        if ok and S.get("auto_upload", False):
            if os.path.exists("user_data/token.json"):
                log("Auto-upload enabled - starting upload session...", "info")
                self._yt_write("  Auto-upload starting after batch completion...", clear=False)
                self._on_upload_all()
            else:
                log("Auto-upload enabled but not authenticated (user_data/token.json missing) - skipping upload", "warn")

    # ---------------- TOAST ----------------
    def show_save_popup_safe(self):
        # Called from any thread via update_s; must marshal to GUI thread
        QTimer.singleShot(0, self._toast.slide_in)

    def _on_toast_save(self):
        save_settings()
        self._dirty_keys.clear()
        self._toast.slide_out()

    def _on_toast_discard(self):
        """Revert only the keys the user has touched since entering the current tab, then rebuild that tab."""
        self._revert_dirty()
        self._toast.slide_out()
        if self._current_page:
            self._rebuild_page(self._current_page)

    def _revert_dirty(self):
        """Restore dirty keys to the per-tab snapshot taken when the tab was entered."""
        for k in list(self._dirty_keys):
            if k in self._page_snapshot:
                S[k] = copy.deepcopy(self._page_snapshot[k])
        self._dirty_keys.clear()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "_toast") and self._toast._shown:
            self._toast.slide_in()

    # ---------------- POLLING ----------------
    def _poll_log(self):
        try:
            while True:
                msg, color = LOG_Q.get_nowait()
                self._tlog(msg, color)
        except queue.Empty:
            pass

    def _poll_yt_log(self):
        try:
            while True:
                msg = YT_LOG_Q.get_nowait()
                self._yt_write(msg, clear=False)
        except queue.Empty:
            pass

    def _poll_stats(self):
        latest = None
        try:
            while True:
                latest = STATS_Q.get_nowait()
        except queue.Empty:
            pass
        if not latest:
            return
        cpu, ram, gp, gt, gm, dsk = latest
        self.hist_cpu.pop(0); self.hist_cpu.append(cpu)
        self.hist_gpu.pop(0); self.hist_gpu.append(gp)
        self.hist_ram.pop(0); self.hist_ram.append(ram)
        self.hist_dsk.pop(0); self.hist_dsk.append(dsk)
        xs = list(range(60))
        def upd(ln, data, val):
            ln.set_data(xs, data)
            val.set_text(f"{data[-1]:.0f}%")
        upd(self.line_cpu, self.hist_cpu, self.val_cpu)
        upd(self.line_gpu, self.hist_gpu, self.val_gpu)
        upd(self.line_ram, self.hist_ram, self.val_ram)
        upd(self.line_dsk, self.hist_dsk, self.val_dsk)
        try:
            self.sys_canvas.draw_idle()
        except Exception:
            pass

    # ---------------- MUSIC CONTROLS ----------------
    def _toggle_music(self):
        if not hasattr(self, '_music_player') or not self._music_player:
            return
        self._music_muted = not self._music_muted
        if self._music_muted:
            self._fade_music_out()
            self._music_btn.setStyleSheet(f"color: {SUB_DK};")
            self._music_btn.setText("\u266A")  # Muted note
        else:
            self._fade_music_in()
            self._music_btn.setStyleSheet(f"color: {ACC};")
            self._music_btn.setText("\u266B")  # Full note

    def _fade_music_out(self):
        self._fade_anim = QPropertyAnimation(self._audio_output, b"volume", self)
        self._fade_anim.setDuration(800)
        self._fade_anim.setStartValue(self._audio_output.volume())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._music_player.pause)
        self._fade_anim.start()

    def _fade_music_in(self):
        self._music_player.play()
        self._fade_anim = QPropertyAnimation(self._audio_output, b"volume", self)
        self._fade_anim.setDuration(800)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(0.5)
        self._fade_anim.start()

    def _on_music_status_changed(self, status):
        from PyQt6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            # Soft fade out, then restart with fade in
            self._fade_anim = QPropertyAnimation(self._audio_output, b"volume", self)
            self._fade_anim.setDuration(500)
            self._fade_anim.setStartValue(self._audio_output.volume())
            self._fade_anim.setEndValue(0.0)
            def restart_and_fade():
                self._music_player.setPosition(0)
                self._music_player.play()
                self._fade_anim = QPropertyAnimation(self._audio_output, b"volume", self)
                self._fade_anim.setDuration(1000)
                self._fade_anim.setStartValue(0.0)
                self._fade_anim.setEndValue(0.5)
                self._fade_anim.start()
            self._fade_anim.finished.connect(restart_and_fade)
            self._fade_anim.start()



# ═══════════════════════════════════════════════════════════════════
#  SPLASH SCREEN  —  Apple Fluid Motion
# ═══════════════════════════════════════════════════════════════════



    def _fetch_analytics_time_series(self, analytics, start_date, end_date, metrics, dimensions="day"):
        """Fetch time series data from YouTube Analytics API."""
        try:
            response = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics=metrics,
                dimensions=dimensions
            ).execute()
            
            if "rows" not in response:
                return []
            
            headers = [h["name"] for h in response.get("columnHeaders", [])]
            return [dict(zip(headers, row)) for row in response["rows"]]
        except Exception as e:
            print(f"Error fetching time series: {e}")
            return []

    def _fetch_demographics_data(self, analytics, start_date, end_date):
        """Fetch demographic data (age, gender) from YouTube Analytics."""
        demographics = {"age": {}, "gender": {}}
        try:
            # Age demographics
            age_response = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="viewerPercentage",
                dimensions="ageGroup"
            ).execute()
            
            if "rows" in age_response:
                for row in age_response["rows"]:
                    age_group = row[0]
                    percentage = float(row[1])
                    demographics["age"][age_group] = percentage
            
            # Gender demographics
            gender_response = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="viewerPercentage",
                dimensions="gender"
            ).execute()
            
            if "rows" in gender_response:
                for row in gender_response["rows"]:
                    gender = row[0]
                    percentage = float(row[1])
                    demographics["gender"][gender] = percentage
                    
        except Exception as e:
            print(f"Error fetching demographics: {e}")
        
        return demographics

    def _fetch_device_data(self, analytics, start_date, end_date):
        """Fetch device type data from YouTube Analytics."""
        devices = {}
        try:
            response = analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="views",
                dimensions="deviceType"
            ).execute()
            
            if "rows" in response:
                for row in response["rows"]:
                    device = row[0]
                    views = int(row[1])
                    devices[device] = views
        except Exception as e:
            print(f"Error fetching device data: {e}")
        
        return devices

    def _fetch_shorts_traffic_sources(self, analytics, video_ids, days=7):
        """Fetch traffic sources specifically for Shorts."""
        traffic = {}
        try:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            
            if video_ids:
                batch_size = 200
                for i in range(0, len(video_ids), batch_size):
                    batch = video_ids[i:i + batch_size]
                    
                    response = analytics.reports().query(
                        ids="channel==MINE",
                        startDate=start_date,
                        endDate=end_date,
                        metrics="views",
                        dimensions="insightTrafficSourceType",
                        filters=f"video=={','.join(batch)}"
                    ).execute()
                    
                    if "rows" in response:
                        for row in response["rows"]:
                            source = row[0]
                            views = int(row[1])
                            traffic[source] = traffic.get(source, 0) + views
                            
        except Exception as e:
            print(f"Error fetching traffic sources: {e}")
        
        return traffic

    def _fetch_shorts_retention(self, analytics, video_ids, days=7):
        """Fetch audience retention data for Shorts (approximated via avgViewDuration)."""
        retention = {"stayed": 0, "swiped": 0}
        try:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            
            if video_ids:
                response = analytics.reports().query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics="views,estimatedMinutesWatched",
                    dimensions="video",
                    filters=f"video=={','.join(video_ids[:200])}"
                ).execute()
                
                if "rows" in response:
                    total_views = 0
                    total_minutes = 0
                    
                    for row in response["rows"]:
                        views = int(row[1])
                        minutes = float(row[2])
                        total_views += views
                        total_minutes += minutes
                    
                    # Estimate: if avg view duration > 50% of typical Short length (60s), counted as "stayed"
                    if total_views > 0:
                        avg_duration_seconds = (total_minutes * 60) / total_views
                        retention_rate = min(avg_duration_seconds / 30, 1.0)  # Normalize to 30s avg
                        retention["stayed"] = int(retention_rate * 100)
                        retention["swiped"] = 100 - retention["stayed"]
                        
        except Exception as e:
            print(f"Error fetching retention: {e}")
        
        return retention

    def _fetch_top_shorts_this_week(self, youtube, analytics):
        """Fetch top performing Shorts from the last 7 days."""
        top_shorts = []
        try:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            
            # Get channel's uploads
            channels_response = youtube.channels().list(
                part="contentDetails",
                mine=True
            ).execute()
            
            if not channels_response.get("items"):
                return top_shorts
            
            uploads_playlist_id = channels_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            
            # Get recent videos
            playlist_response = youtube.playlistItems().list(
                part="contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=50
            ).execute()
            
            video_ids = [item["contentDetails"]["videoId"] for item in playlist_response.get("items", [])]
            
            if video_ids:
                # Get video details to identify Shorts (duration < 60s)
                videos_response = youtube.videos().list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(video_ids)
                ).execute()
                
                shorts = []
                for video in videos_response.get("items", []):
                    duration = video["contentDetails"].get("duration", "")
                    # Parse PT#M#S format
                    import re
                    match = re.match(r'PT(?:(\d+)M)?(?:(\d+)S)?', duration)
                    if match:
                        minutes = int(match.group(1) or 0)
                        seconds = int(match.group(2) or 0)
                        total_seconds = minutes * 60 + seconds
                        
                        if total_seconds <= 60:  # Shorts are <= 60 seconds
                            shorts.append({
                                "id": video["id"],
                                "title": video["snippet"]["title"],
                                "thumbnail": video["snippet"].get("thumbnails", {}).get("medium", {}).get("url", ""),
                                "views": int(video["statistics"].get("viewCount", 0))
                            })
                
                # Sort by views and get top 5
                shorts.sort(key=lambda x: x["views"], reverse=True)
                top_shorts = shorts[:5]
                
        except Exception as e:
            print(f"Error fetching top shorts: {e}")
        
        return top_shorts

    def _is_short_video(self, duration_str):
        """Check if video duration indicates it's a Short (<= 60 seconds)."""
        import re
        match = re.match(r'PT(?:(\d+)M)?(?:(\d+)S)?', duration_str)
        if match:
            minutes = int(match.group(1) or 0)
            seconds = int(match.group(2) or 0)
            return (minutes * 60 + seconds) <= 60
        return False

    def _calculate_cumulative_growth(self, daily_data):
        """Calculate cumulative growth from daily data."""
        cumulative = []
        running_total = 0
        
        for day_data in daily_data:
            date = day_data.get("day", "")
            value = int(day_data.get("views", 0)) if "views" in day_data else int(day_data.get("subscribersGained", 0))
            running_total += value
            cumulative.append({"date": date, "value": running_total})
        
        return cumulative



# ═══════════════════════════════════════════════════════════════════
#  ANALYTICS THREAD  —  Background data fetching
# ═══════════════════════════════════════════════════════════════════
class AnalyticsFetchThread(QThread):
    """Thread to fetch YouTube analytics data in background."""
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(dict, int, int)
    error = pyqtSignal(str)

    def __init__(self, parent_app):
        super().__init__()
        self.parent_app = parent_app

    def run(self):
        try:
            from googleapiclient.discovery import build as yt_build
            from datetime import datetime, timedelta
            
            self.progress.emit("Authenticating...")
            creds = self.parent_app._get_youtube_credentials()
            if not creds:
                self.error.emit("Not authenticated. Please log in via the YouTube Panel first.")
                return
            
            self.progress.emit("Building API clients...")
            youtube = yt_build("youtube", "v3", credentials=creds)
            analytics = yt_build("youtubeAnalytics", "v2", credentials=creds)
            
            # Determine date range
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date_7d = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            start_date_28d = (datetime.now() - timedelta(days=28)).strftime("%Y-%m-%d")
            start_date_90d = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            start_date_lifetime = (datetime.now().replace(year=datetime.now().year - 5)).strftime("%Y-%m-%d")
            
            self.parent_app._deduct_quota("channels.list", 1)
            self.progress.emit("Fetching channel stats...")
            channel_stats = self.parent_app._fetch_channel_stats(youtube, analytics)
            if not channel_stats:
                self.error.emit("Failed to fetch channel statistics.")
                return
            
            self.progress.emit("Fetching video list...")
            videos = self.parent_app._fetch_video_list(youtube, channel_stats["channel_id"], max_results=50)
            self.parent_app._deduct_quota("videos.list", len(videos) // 50 + 1)
            self.parent_app._deduct_quota("playlistItems.list", 1)
            
            # Separate Shorts and regular videos
            shorts_video_ids = [v["id"] for v in videos if self.parent_app._is_short_video(v.get("duration", ""))]
            all_video_ids = [v["id"] for v in videos]
            
            self.progress.emit("Fetching cumulative growth data...")
            # Cumulative data (last 90 days for trends)
            cumulative_views_data = self.parent_app._fetch_analytics_time_series(
                analytics, start_date_90d, end_date, "views", "day"
            )
            cumulative_subs_data = self.parent_app._fetch_analytics_time_series(
                analytics, start_date_90d, end_date, "subscribersGained", "day"
            )
            cumulative_views = self.parent_app._calculate_cumulative_growth(cumulative_views_data)
            cumulative_subs = self.parent_app._calculate_cumulative_growth(cumulative_subs_data)
            
            self.progress.emit("Fetching daily activity...")
            # Daily activity (last 28 days)
            daily_views = self.parent_app._fetch_analytics_time_series(
                analytics, start_date_28d, end_date, "views", "day"
            )
            daily_subs = self.parent_app._fetch_analytics_time_series(
                analytics, start_date_28d, end_date, "subscribersGained,subscribersLost", "day"
            )
            daily_watch = self.parent_app._fetch_analytics_time_series(
                analytics, start_date_28d, end_date, "estimatedMinutesWatched", "day"
            )
            
            self.progress.emit("Fetching demographics...")
            # Demographics
            demographics = self.parent_app._fetch_demographics_data(analytics, start_date_28d, end_date)
            device_data = self.parent_app._fetch_device_data(analytics, start_date_28d, end_date)
            
            self.progress.emit("Fetching Shorts metrics...")
            # Shorts-specific metrics
            traffic_sources = self.parent_app._fetch_shorts_traffic_sources(analytics, shorts_video_ids, 7)
            retention_data = self.parent_app._fetch_shorts_retention(analytics, shorts_video_ids, 7)
            top_shorts = self.parent_app._fetch_top_shorts_this_week(youtube, analytics)
            
            if videos:
                self.progress.emit("Fetching video analytics...")
                analytics_data = self.parent_app._fetch_analytics_data(analytics, all_video_ids)
                videos = self.parent_app._merge_video_data(videos, analytics_data)
                self.parent_app._deduct_quota("youtubeAnalytics.reports.query", 1)
            
            self.progress.emit("Categorizing content...")
            categorized = {"Funny": [], "Badass": [], "Dramatic": [], "Uncategorized": []}
            for video in videos:
                cat = video.get("category", "Uncategorized")
                categorized[cat].append(video)
            
            by_type = {
                "Funny": len(categorized["Funny"]),
                "Badass": len(categorized["Badass"]),
                "Dramatic": len(categorized["Dramatic"]),
                "Uncategorized": len(categorized["Uncategorized"])
            }
            by_type_views = {
                "Funny": sum(v["views"] for v in categorized["Funny"]),
                "Badass": sum(v["views"] for v in categorized["Badass"]),
                "Dramatic": sum(v["views"] for v in categorized["Dramatic"]),
                "Uncategorized": sum(v["views"] for v in categorized["Uncategorized"])
            }
            
            used_quota, total_quota = self.parent_app._get_quota_status()
            
            result = {
                "channel": channel_stats,
                "videos": videos,
                "categorized": categorized,
                "total_videos": len(videos),
                "by_type": by_type,
                "by_type_views": by_type_views,
                # New comprehensive data
                "cumulative_views": cumulative_views,
                "cumulative_subs": cumulative_subs,
                "daily_views": daily_views,
                "daily_subs": daily_subs,
                "daily_watch": daily_watch,
                "demographics": demographics,
                "device_data": device_data,
                "traffic_sources": traffic_sources,
                "retention": retention_data,
                "top_shorts": top_shorts
            }
            
            self.progress.emit(f"Complete! Quota used: {used_quota}/{total_quota}")
            self.finished_ok.emit(result, used_quota, total_quota)
            
        except Exception as e:
            import traceback
            error_msg = f"Analytics error: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            self.error.emit(str(e))
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

    # ═══════════════════════════════════════════════════════════════════
    #  GPU DETECTION
    # ═══════════════════════════════════════════════════════════════════
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            print(f"[GPU] CUDA available: {gpu_name}")
            print(f"[GPU] Whisper will use: CUDA")
            print(f"[GPU] Demucs will use: CUDA")
        else:
            print("[GPU] CUDA not available — Whisper/Demucs will use CPU")
    except ImportError:
        print("[GPU] torch not installed — cannot detect GPU")

    # Create user_data folder for OAuth credentials (token.json, client_secrets.json)
    os.makedirs("user_data", exist_ok=True)

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