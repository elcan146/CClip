<div align="center">

<br/>

<img src="darklogo.png" alt="CClip" width="420">

# ✂️ CClip

**AI-powered cinematic clip engine — turn full-length movies into viral vertical shorts, automatically.**

<br/>

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PyQt6](https://img.shields.io/badge/UI-PyQt6%20Fluent-41CD52?style=for-the-badge&logo=qt&logoColor=white)](https://www.riverbankcomputing.com/software/pyqt/)
[![FFmpeg](https://img.shields.io/badge/Engine-FFmpeg-007808?style=for-the-badge&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![Whisper](https://img.shields.io/badge/ASR-OpenAI%20Whisper-412991?style=for-the-badge&logo=openai&logoColor=white)](https://github.com/openai/whisper)
[![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue?style=for-the-badge)](LICENSE)

<br/>

*Drop in a movie → get back perfectly cut, subtitled, soundtracked 9:16 shorts —*
*with optional one-click YouTube upload & analytics.*

</div>

---

## 🎬 The Pipeline

```
🎞️  Input Video ──► 🎧 Whisper Transcription ──► 🧠 Gemini Scene Analysis
        │                                              │
        │                                              ▼
        │                              📋 Clip Metadata (titles, hooks, timestamps)
        │                                              │
        ▼                                              ▼
📐 Smart 9:16 Reframe ──► 💬 Animated ASS Subtitles ──► 🎵 Music + Voiceover Mix
        │
        ▼
🔥 FFmpeg Render ──► 🚀 YouTube Upload ──► 📊 Analytics Dashboard
```

## ✨ Features

- **🧠 AI Moment Detection** — Gemini analyzes the transcript and picks the funniest, most dramatic, most badass moments with hook timestamps and viral-ready metadata.
- **🎙️ Local Whisper Transcription** — word-level timestamps on-device (CUDA-accelerated when available). No cloud speech costs.
- **📐 Cinematic 9:16 Reframe** — blur-padding fill, contrast/saturation grading, mirror cuts, motion blur, and floating logo overlays.
- **💬 Karaoke Subtitles** — animated ASS subtitles with per-word fill highlight, pop-in scaling, and fully customizable fonts & colors.
- **🔊 Pro Audio Mixer** — per-source volume, auto-ducking, silence cutting, Edge-TTS impact voiceovers, mood-based background music.
- **🚀 YouTube Automation** — OAuth upload with blind metadata, scheduled posting, thumbnail grid, and a live analytics dashboard (views, likes, comments, watch time).
- **🖥️ Fluent PyQt6 UI** — frameless window, spring-press buttons, animated sidebar, dark/light theming with custom color picker, cinematic splash screen.
- **📈 System Monitor** — real-time CPU / GPU / RAM / disk graphs during renders.

## 🗂️ Project Structure

```
CClip/
├── 🚀 main.py                  # Application entry point (splash + modular app)
├── 📦 cclip_standalone.py      # Self-contained single-file edition
├── ⚙️  config.py               # Global state, fonts, theme bootstrap
├── 🧠 backend/                 # Processing engine
│   ├── ai.py                   # Gemini clip analysis
│   ├── transcription.py        # Whisper ASR
│   ├── subtitles.py            # ASS subtitle builder
│   ├── az_subtitles.py         # Azerbaijani karaoke subtitles (also CLI)
│   ├── render.py / pipeline.py # FFmpeg render pipeline
│   ├── audio.py / voiceover.py # Mixing, ducking, Edge-TTS
│   └── youtube.py / stats.py   # Upload + system stats
├── 🧵 threads/                 # QThread workers (batch, upload, auth, scan, analytics)
├── 🎨 ui/                      # PyQt6 Fluent interface
│   ├── main_window.py          # Main window & pages
│   └── widgets/                # Custom animated widgets
└── 🛠️  utils/                  # Theme, settings, logging, copyright checks
```

## 🚀 Getting Started

### Prerequisites

- **Python 3.11+**
- **FFmpeg** + `ffprobe` on your `PATH`
- *(Recommended)* NVIDIA GPU + CUDA for fast Whisper transcription
- A **Google Gemini API key** — [get one free](https://aistudio.google.com/app/apikey)
- *(Optional)* Google Cloud OAuth `client_secrets.json` for YouTube upload

### Install

```bash
git clone https://github.com/<you>/CClip.git
cd CClip

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux / macOS

pip install -r requirements.txt
```

### Run

```bash
python main.py
```

> 🖼️ First launch plays a cinematic splash — drop an `start3.mp4` + `MenuMusic.mp3` next to `main.py` to enable the video intro.

### ⚡ Standalone Azerbaijani subtitles

```bash
python az_subtitles.py input.mp4 --model medium
```

## 🔑 Configuration

Everything is configured from inside the app — no `.env` files needed:

| Setting | Where |
|---|---|
| Gemini API key | ⚙️ Configuration page |
| YouTube OAuth | ▷ YouTube Panel → place `client_secrets.json` in `user_data/` |
| Input / output / music folders | ⚙️ Configuration page |
| Theme, fonts, subtitle style | ☰ UI Settings page |

> 🔐 `user_data/`, `*_settings.json`, and all credentials are `.gitignore`d — nothing sensitive ever leaves your machine.

## 🛠️ Built With

| Layer | Tech |
|---|---|
| UI | PyQt6 · custom Fluent widgets · matplotlib charts |
| AI | Google Gemini · OpenAI Whisper · Edge-TTS |
| Media | FFmpeg · libass · MediaPipe |
| Cloud | YouTube Data API v3 · YouTube Analytics API |

## 📜 License

<div align="center">

**CClip is free software, licensed under the [GNU General Public License v3.0](LICENSE).**

*You may copy, modify and redistribute it — as long as it stays free.*

[![GPLv3](https://img.shields.io/badge/GPL--3.0-Copyleft-red?style=for-the-badge&logo=gnu&logoColor=white)](LICENSE)

</div>
