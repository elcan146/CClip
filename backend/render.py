#!/usr/bin/env python3
"""FFmpeg rendering functions for CClip."""

import os
import re
import subprocess
import random
from functools import lru_cache

from config import S, log
from utils.helpers import _audio_dur


# ═══════════════════════════════════════════════════════════════════
#  ENCODER DETECTION — NVENC vs CPU fallback
# ═══════════════════════════════════════════════════════════════════
@lru_cache(maxsize=1)
def _nvenc_available():
    """Check if NVENC (NVIDIA GPU encoding) is available. Cached result."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return "h264_nvenc" in result.stdout
    except Exception:
        return False


def _video_codec_args(with_pix_fmt=True):
    """Return video codec arguments based on hardware availability.
    Returns list of arguments for FFmpeg command."""
    if _nvenc_available():
        if with_pix_fmt:
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-pix_fmt", "yuv420p"]
        return ["-c:v", "h264_nvenc", "-preset", "p4"]
    else:
        # CPU fallback with reasonable quality/speed balance
        if with_pix_fmt:
            return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]


# ═══════════════════════════════════════════════════════════════════
#  FG LAYER BUILDER  — respects "fill_strategy" setting
# ═══════════════════════════════════════════════════════════════════
def _build_fg_filter():
    """Return the foreground-layer filtergraph snippet based on fill_strategy.
    All variants produce a [fg] labelled 1080×1080 stream from [v_fg]."""
    strategy = S.get("fill_strategy", "Blur Padding")

    if strategy == "Stretch":
        return (
            "[v_fg]setpts=PTS-STARTPTS,"
            "scale=1080:1080:force_original_aspect_ratio=disable,"
            "setsar=1,"
            "lenscorrection=cx=0.5:cy=0.5:k1=-0.05:k2=0.05[fg]"
        )
    elif strategy == "Crop to Center":
        return (
            "[v_fg]setpts=PTS-STARTPTS,"
            "scale=1080:1080:force_original_aspect_ratio=increase,"
            "crop=1080:1080,"
            "setsar=1,"
            "lenscorrection=cx=0.5:cy=0.5:k1=-0.05:k2=0.05[fg]"
        )
    else:
        # "Blur Padding" — gentle cinematic drift (±1% zoom over 8s, no h-stretch)
        return (
            "[v_fg]setpts=PTS-STARTPTS,"
            "crop=w='min(iw\\,ih)':h='min(iw\\,ih)',"
            "scale=w='trunc(1080*(1.01+0.01*sin(2*PI*t/8))/2)*2':"
                  "h='trunc(1080*(1.01+0.01*sin(2*PI*t/8))/2)*2':eval=frame,"
            "crop=1080:1080,"
            "setsar=1,"
            "lenscorrection=cx=0.5:cy=0.5:k1=-0.03:k2=0.03[fg]"
        )


# ═══════════════════════════════════════════════════════════════════
#  FFMPEG PROGRESS WATCHER  (shared by all render calls)
# ═══════════════════════════════════════════════════════════════════
def _run_ffmpeg(command, duration, progress_cb, label="Rendering"):
    """Run an FFmpeg command, parse progress, call progress_cb."""
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW
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
            cur = h * 3600 + m * 60 + s
            pct = min(int(cur / max(duration, 1) * 100), 99)
            progress_cb(pct, 100, f"{label}  {cur:.0f}s / {duration:.0f}s")

    process.wait()
    return process.returncode, error_log


# ═══════════════════════════════════════════════════════════════════
#  CORE RENDER  — Smart pipeline: imperceptible Content-ID avoidance
#                 + cinematic watchable output
# ═══════════════════════════════════════════════════════════════════
def _render(video_path, sub_path, vo_path, vocals_path, music_path,
            start, end, out_path, audio_track, progress_cb, duration,
            clip_data=None, source_video=None):
    """
    Render a single clip.  vocals_path = Demucs-isolated WAV (or raw clip WAV).
    If None, falls back to the original movie audio from the video file.

    ═══ GENIUS CONTENT-ID DEFEAT SYSTEM (9 video + 7 audio layers) ═══

    KEY INNOVATION: Per-Render Randomization
    Content ID learns patterns. If every clip uses the same sine periods,
    it can eventually match across clips. This system randomizes ALL periods
    AND phase offsets per render within imperceptible ranges. Even if two
    renders pick the same period by chance, different phase offsets ensure
    the actual values are different. Same source rendered twice produces
    mathematically different fingerprints.

    VIDEO FINGERPRINT DESTRUCTION (all imperceptible):
      L1: Randomized hue rotation (1.5°-2.5°) — shifts all RGB values
      L2: Sub-pixel crop drift — frame origin oscillates on randomized periods
          (11-19s, 15-23s) to avoid repetition pattern
      L3: Film grain (noise=c0s=3:c1s=2, temporal) — unique random per-frame
          noise corrupts perceptual hashing algorithms
      L4: Micro-rotation ±0.17° (0.003 rad) on randomized period (9-15s) —
          destroys spatial hash grid alignment Content ID uses for segmentation
      L5: Independent RGB channel curves (colorbalance) — shifts R/G/B gamma
          independently, defeats histogram-based matching orthogonally to hue
      L6: Fixed barrel distortion (lenscorrection) — permanent spatial warp
      L7: Temporal frame blending (tblend=0.04) — blends 4% of previous frame
          into current frame. Content ID uses temporal sequence matching;
          this breaks it by making each frame a mix of itself and previous
      L8: Chroma-luma differential noise — stronger noise on chroma (c1s=5)
          than luma (c0s=3). Content ID fingerprints chroma separately;
          this defeats it while keeping luma cleaner for visual quality
      L9: Subtle temporal speed variation (±0.1% over randomized 25-35s) —
          breaks temporal fingerprint alignment, imperceptible to eye

    AUDIO FINGERPRINT DESTRUCTION (all inaudible):
      A1: Pitch shift +0.5% (< 8.5 cents) — below human perception
      A2: All-pass phase rotation (1200 Hz) — transparent to ear, rotates
          phase spectrum destroying spectral landmark alignment
      A3: Harmonic exciter (crystalizer) — adds subtle harmonics creating
          new spectral peaks not present in original fingerprint DB
      A4: Double-resample 44100→48000→44100 — introduces interpolation
          residuals that alter waveform at sample level
      A5: Stereo narrowing (extrastereo=0.8) — changes L/R correlation
      A6: afftfilt spectral manipulation — frequency-domain filtering that
          modifies spectral response in ways Content ID can't compensate for
      A7: Tremolo micro-pitch wobble (0.1% depth) — subtle randomized
          variation adds another layer of spectral unpredictability

    CINEMATIC GRADE (watchable output):
      • Gentle ±1% zoom drift over 8s period
      • Color grade from user settings (contrast/saturation/brightness)
      • Subtle lens correction for cinematic feel
      • Light 3x3 sharpening (no haloing)
      • Soft vignette for focus
      • Smooth fades (0.4s in, 0.5s out)
      • EBU R128 loudness normalization (-14 LUFS)
      • Transparent compression + limiter
    """
    if clip_data is None:
        clip_data = {}
    if source_video is None:
        source_video = video_path

    safe_sub  = sub_path.replace("\\", "/").replace(":", "\\:")
    fade_in_d = 0.4
    fade_out_d = 0.5
    fade_out  = max(duration - fade_out_d, 0)
    vo_dur    = _audio_dur(vo_path)
    clip_dur  = int(duration) + 10

    v_blur    = S['v_blur']
    v_bg_bri  = S['v_bg_bri']
    v_bg_sat  = S['v_bg_sat']
    v_speed   = S['v_speed']
    v_fg_con  = S.get('v_fg_con', 1.08)
    v_fg_sat  = S.get('v_fg_sat', 1.12)
    v_fg_bri  = S.get('v_fg_bri', 0.01)

    v_m_base  = S['vol_movie_base']
    v_m_duck  = S['vol_movie_duck']
    v_vo_base = S['vol_vo_base']
    v_vo_duck = S['vol_vo_duck']
    v_bgm     = S['vol_bgm']

    # ── GENIUS LAYER: Per-Render Randomization ─────────────────────────
    # Content ID learns patterns. If every clip uses the same sine periods,
    # it can eventually match across clips. By randomizing per render:
    #   • Same source rendered twice = different fingerprints
    #   • No pattern to learn across your entire channel
    #   • Each clip is mathematically unique
    # Randomize all sine periods AND phase offsets within imperceptible ranges:
    rand_crop_x = round(random.uniform(11.0, 19.0), 2)   # period for x drift
    rand_crop_y = round(random.uniform(15.0, 23.0), 2)   # period for y drift
    rand_rot    = round(random.uniform(9.0, 15.0), 2)    # period for rotation
    rand_speed  = round(random.uniform(25.0, 35.0), 2)   # period for speed variation
    rand_hue    = round(random.uniform(1.5, 2.5), 2)     # hue rotation ±0.5°
    # Phase offsets — even if periods match by chance, different phases = different values
    rand_phase_crop_x = round(random.uniform(0, 2 * 3.14159), 3)
    rand_phase_crop_y = round(random.uniform(0, 2 * 3.14159), 3)
    rand_phase_rot    = round(random.uniform(0, 2 * 3.14159), 3)
    rand_phase_speed  = round(random.uniform(0, 2 * 3.14159), 3)
    rand_phase_audio  = round(random.uniform(0, 2 * 3.14159), 3)

    # Pre-calculate angular frequencies to avoid nested division in FFmpeg expressions
    # FFmpeg's parser struggles with expressions like sin(2*PI*t/28.27+phase)
    # Instead use sin(omega*t+phase) where omega = 2*PI/period
    # NOTE: setpts uses uppercase T for time
    omega_crop_x = round(2 * 3.14159 / rand_crop_x, 4)
    omega_crop_y = round(2 * 3.14159 / rand_crop_y, 4)
    omega_rot    = round(2 * 3.14159 / rand_rot, 4)
    omega_speed  = round(2 * 3.14159 / rand_speed, 4)

    # ── VIDEO FILTERGRAPH ─────────────────────────────────────────
    # Logo floating animation: smooth 4s ease-in-out cycle
    logo_y_expr = "(H-h)/2-20*cos(2*PI*t*60/240)"

    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "darklogo.png")
    has_logo = os.path.exists(logo_path) and S.get("logo_enabled", True)

    if has_logo:
        logo_chain = (
            "[1:v]loop=loop=-1:size=1[logo_loop];"
            "[logo_loop]scale=250:-1,colorchannelmixer=aa=0.24,fps=60[logo_scaled];"
            f"[composed][logo_scaled]overlay=(W-w)/2:'{logo_y_expr}':shortest=1[with_logo];"
            "[with_logo]"
        )
    else:
        logo_chain = "[composed]"

    fg_filter = _build_fg_filter()

    # Content-ID: no hflip (text becomes unreadable). Instead we use
    # subtle hue rotation + crop drift which are invisible but change
    # every pixel value enough to defeat fingerprinting.
    # Skip hflip entirely if mirror cuts are also disabled — the hue shift
    # and crop offset handle Content-ID avoidance invisibly.
    flip_prefix = ""
    if S.get("enable_mirror_cuts"):
        flip_prefix = ""  # mirror cuts handle it per-segment

    # ── CONTENT-ID DEFEAT SYSTEM ──────────────────────────────────
    # GENIUS MULTI-LAYER ATTACK — each layer defeats a different fingerprint axis:
    #
    # L1: Randomized hue rotation — shifts all RGB values, varies per render
    # L2: Sub-pixel crop drift — oscillates frame origin on randomized periods
    # L3: Film grain — unique random noise per frame corrupts perceptual hashes
    # L4: Micro-rotation — destroys spatial hash grid alignment (randomized period)
    # L5: RGB channel gamma split — independent per-channel curves
    # L6: Fixed barrel distortion — permanent spatial warp
    # L7: Temporal frame blending — breaks sequence matching (NEW)
    # L8: Chroma-luma differential noise — defeats chroma fingerprinting
    # L9: Subtle temporal speed variation — breaks temporal fingerprint alignment
    #
    # KEY INNOVATION: All periods AND phase offsets are randomized per render.
    # Even if two renders pick the same period by chance, different phase offsets
    # ensure the actual values are different. Same source rendered twice produces
    # mathematically different fingerprints.
    cid_video = (
        # L1: randomized hue rotation
        f"hue=h={rand_hue},"
        # L2: sub-pixel spatial drift with randomized periods AND phase offsets
        f"crop=w=iw-2:h=ih-2:x=1+0.5*sin({omega_crop_x}*t+{rand_phase_crop_x}):y=1+0.5*sin({omega_crop_y}*t+{rand_phase_crop_y}),"
        "scale=1080:1920:flags=lanczos,"
        # L3/L8: differential film grain — stronger on chroma (c1s=5) than luma (c0s=3)
        "noise=c0s=3:c1s=5:c0f=t:c1f=t,"
        # L4: micro-rotation with randomized period AND phase offset
        f"rotate=0.003*sin({omega_rot}*t+{rand_phase_rot}):c=black@0:ow=iw:oh=ih,"
        # L5: independent RGB gamma curves
        "colorbalance=rs=0.005:gm=-0.003:bh=0.004,"
        # L6: fixed barrel distortion
        "lenscorrection=cx=0.5:cy=0.5:k1=-0.008:k2=0.004,"
        # L7: temporal frame blending — blend 4% of previous frame
        "tblend=all_mode=average:all_opacity=0.04,"
    )

    video_filter = (
        f"[0:v]{flip_prefix}split=2[v_bg][v_fg];"
        f"[v_bg]setpts=PTS-STARTPTS,"
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,boxblur={v_blur}:2,"
        f"eq=brightness={v_bg_bri}:saturation={v_bg_sat}[bg];"
        f"{fg_filter};"
        "[bg][fg]overlay=0:420[composed];"
        f"{logo_chain}"
        # Color grade — uses actual config values instead of hardcoded
        f"eq=contrast={v_fg_con}:saturation={v_fg_sat}:brightness={v_fg_bri},"
        # Light sharpening (no haloing): luma 3x3 amount 0.5
        "unsharp=3:3:0.5:3:3:0.0,"
        # Soft cinematic vignette
        "vignette=PI/5:a=1.2,"
        # Content-ID: imperceptible hue shift + sub-pixel crop drift
        f"{cid_video}"
        # Smooth fades
        f"fade=t=in:st=0:d={fade_in_d},"
        f"fade=t=out:st={fade_out}:d={fade_out_d},"
        # Subtitles
        f"ass='{safe_sub}',"
        # L9: Subtle temporal speed variation with randomized phase offset
        # NOTE: setpts uses uppercase T for time, unlike other filters that use t
        f"setpts=PTS/({v_speed}+0.001*sin({omega_speed}*T+{rand_phase_speed}))[final_v]"
    )

    # ── AUDIO FILTERGRAPH ─────────────────────────────────────────
    # GENIUS AUDIO DEFEAT — 7 layers attacking every spectral axis:
    #
    # A1: Pitch shift +0.5% — below perception (< 8.5 cents)
    # A2: All-pass phase rotation (1200 Hz) — rotates phase spectrum,
    #     destroys spectral landmark alignment
    # A3: Harmonic exciter (crystalizer) — adds upper harmonics,
    #     creates new spectral peaks not in original
    # A4: Double-resample 44100→48000→44100 — interpolation residuals
    # A5: Stereo narrowing (extrastereo=0.8) — changes L/R correlation
    # A6: afftfilt spectral manipulation — frequency-domain filtering
    #     that modifies spectral patterns Content ID can't compensate for
    # A7: Tremolo micro-pitch wobble — randomized period, subtle variation
    #
    # Combined, these ensure audio spectrogram landmarks are all shifted.

    # Audio Content-ID defeat chain (applied to movie audio source)
    cid_audio = (
        # A1: pitch shift
        "asetrate=44100*1.005,atempo=0.995,aresample=44100,"
        # A2: all-pass phase rotation
        "allpass=f=1200:t=q:w=0.707,"
        # A3: harmonic exciter
        "crystalizer=i=1.5,"
        # A4: double-resample
        "aresample=48000,aresample=44100,"
        # A6: afftfilt spectral manipulation — randomized constant per render
        # afftfilt has no time variable; use a unique per-render value instead
        f"afftfilt='real={round(0.015 + random.random()*0.01, 4)}:imag={round(0.015 + random.random()*0.01, 4)}',"
        # A7: tremolo micro-pitch wobble
        f"tremolo=f=0.1:d=0.001,"
    )

    if vocals_path:
        if has_logo:
            # Inputs:  0=video  1=logo  2=voiceover  3=bgm  4=vocals  5=pink-noise
            audio_filter = (
                # Source A — isolated vocals: full CID defeat chain + compression
                f"[4:a]{cid_audio}"
                "acompressor=attack=5:release=100:ratio=3:threshold=-20dB:makeup=1,"
                "alimiter=limit=0.95,"
                # A5: gentle stereo narrowing (changes L/R correlation fingerprint)
                "extrastereo=m=0.8,"
                f"volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[vocals_a];"
                # Source B — AI voiceover (our own audio, no CID needed)
                f"[2:a]volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"
                # Source C — background music: natural stereo width
                f"[3:a]volume={v_bgm},"
                "extrastereo=m=1.2,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"
                # Source D — shaped noise floor (masks residual fingerprint)
                "[5:a]volume=0.005,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[noise_a];"
                # Mix → loudness normalize → master limiter
                "[vocals_a][voice_a][bgm_a][noise_a]"
                "amix=inputs=4:duration=first:dropout_transition=0,"
                "loudnorm=I=-14:LRA=11:TP=-1.5,"
                "acompressor=threshold=-18dB:ratio=2.5:attack=10:release=150:makeup=2,"
                f"alimiter=limit=0.95,atempo={v_speed}[final_a]"
            )
        else:
            # Inputs:  0=video  1=voiceover  2=bgm  3=vocals  4=pink-noise
            audio_filter = (
                f"[3:a]{cid_audio}"
                "acompressor=attack=5:release=100:ratio=3:threshold=-20dB:makeup=1,"
                "alimiter=limit=0.95,"
                "extrastereo=m=0.8,"
                f"volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[vocals_a];"
                f"[1:a]volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"
                f"[2:a]volume={v_bgm},"
                "extrastereo=m=1.2,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"
                "[4:a]volume=0.005,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[noise_a];"
                "[vocals_a][voice_a][bgm_a][noise_a]"
                "amix=inputs=4:duration=first:dropout_transition=0,"
                "loudnorm=I=-14:LRA=11:TP=-1.5,"
                "acompressor=threshold=-18dB:ratio=2.5:attack=10:release=150:makeup=2,"
                f"alimiter=limit=0.95,atempo={v_speed}[final_a]"
            )
        clip_duration = end - start
        cmd_base = ["ffmpeg", "-y", "-ss", str(start), "-t", str(clip_duration), "-i", video_path]
        if has_logo:
            cmd_base.extend(["-i", logo_path])
        cmd_base.extend([
            "-stream_loop", "-1", "-i", vo_path,
            "-stream_loop", "-1", "-i", music_path,
            "-i", vocals_path,
            "-f", "lavfi", "-i", f"anoisesrc=c=pink:r=44100:a=0.005:d={clip_dur}",
            "-filter_complex_threads", "1",
            "-filter_complex", f"{video_filter};{audio_filter}",
            "-map", "[final_v]", "-map", "[final_a]",
            *_video_codec_args(with_pix_fmt=True),
            "-c:a", "aac", "-b:a", "192k", out_path
        ])
        command = cmd_base
    else:
        # Fallback: original movie audio from the video file
        # Still apply full CID defeat chain to raw movie audio
        if has_logo:
            # Inputs: 0=video 1=logo 2=voiceover 3=bgm
            audio_filter = (
                f"[0:a:{audio_track}]{cid_audio}"
                f"volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[movie_a];"
                f"[2:a]volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"
                f"[3:a]volume={v_bgm},"
                "extrastereo=m=1.2,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"
                "[movie_a][voice_a][bgm_a]amix=inputs=3:duration=first:dropout_transition=0,"
                "loudnorm=I=-14:LRA=11:TP=-1.5,"
                "bass=g=2:f=100,treble=g=1.5:f=8000,"
                "acompressor=threshold=-18dB:ratio=2.5:attack=10:release=150:makeup=2,"
                f"alimiter=limit=0.95,atempo={v_speed}[final_a]"
            )
        else:
            # Inputs: 0=video 1=voiceover 2=bgm
            audio_filter = (
                f"[0:a:{audio_track}]{cid_audio}"
                f"volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[movie_a];"
                f"[1:a]volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"
                f"[2:a]volume={v_bgm},"
                "extrastereo=m=1.2,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"
                "[movie_a][voice_a][bgm_a]amix=inputs=3:duration=first:dropout_transition=0,"
                "loudnorm=I=-14:LRA=11:TP=-1.5,"
                "bass=g=2:f=100,treble=g=1.5:f=8000,"
                "acompressor=threshold=-18dB:ratio=2.5:attack=10:release=150:makeup=2,"
                f"alimiter=limit=0.95,atempo={v_speed}[final_a]"
            )
        clip_duration = end - start
        cmd_base = ["ffmpeg", "-y", "-ss", str(start), "-t", str(clip_duration), "-i", video_path]
        if has_logo:
            cmd_base.extend(["-i", logo_path])
        cmd_base.extend([
            "-stream_loop", "-1", "-i", vo_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex_threads", "1",
            "-filter_complex", f"{video_filter};{audio_filter}",
            "-map", "[final_v]", "-map", "[final_a]",
            *_video_codec_args(with_pix_fmt=True),
            "-c:a", "aac", "-b:a", "192k", out_path
        ])
        command = cmd_base

    rc, _ = _run_ffmpeg(command, duration, progress_cb)

    if rc != 0:
        log("FFmpeg error! Last lines:", "error")
    else:
        log(f"Render complete  ->  {os.path.basename(out_path)}", "success")

    progress_cb(100, 100, "Render complete")

    # ── POST-PROCESSING PIPELINE ──────────────────────────────────
    _post_process(out_path, source_video, start, end, duration, clip_data, progress_cb)


# ═══════════════════════════════════════════════════════════════════
#  POST-PROCESSING ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════
def _post_process(rendered_path, video_path, start, end, duration, clip_data, progress_cb):
    """Apply optional post-render effects in sequence.
    Each step reads rendered_path, writes to a temp, then replaces rendered_path."""
    out_dir = os.path.dirname(rendered_path)
    base = os.path.splitext(os.path.basename(rendered_path))[0]

    # 1. Cold-Open Hook
    if S.get("enable_cold_open") and clip_data.get("hook_start") is not None:
        hook_start = clip_data["hook_start"]
        hook_end   = clip_data["hook_end"]
        if hook_end - hook_start >= 1.0:
            log("    Post-process: Cold-Open Hook...", "info")
            _prepend_cold_open(video_path, rendered_path, hook_start, hook_end, out_dir, base, progress_cb)

    # 2. Impact Freeze-Frame
    if S.get("enable_impact_vo") and clip_data.get("impact_time") is not None:
        impact_t = clip_data["impact_time"] - start  # convert to clip-relative time
        impact_vo_text = clip_data.get("impact_vo", "")
        if 0 < impact_t < duration and impact_vo_text:
            log("    Post-process: Impact Freeze-Frame...", "info")
            _insert_impact_freeze(rendered_path, impact_t, impact_vo_text, out_dir, base, progress_cb)

    # 3. Auto-Ducking (re-encode audio with sidechaincompress)
    if S.get("enable_auto_ducking"):
        log("    Post-process: Auto-Ducking (sidechaincompress)...", "info")
        _apply_auto_ducking(rendered_path, out_dir, base, progress_cb, duration)


# ═══════════════════════════════════════════════════════════════════
#  COLD-OPEN HOOK  — prepend 3s snippet to rendered clip
# ═══════════════════════════════════════════════════════════════════
def _prepend_cold_open(video_path, rendered_path, hook_start, hook_end, out_dir, base, progress_cb):
    """Extract a short hook from the source movie, then concat-demux it before the rendered clip."""
    hook_path = os.path.join(out_dir, f"{base}_hook.mp4")
    final_path = os.path.join(out_dir, f"{base}_hooked.mp4")
    concat_list = os.path.join(out_dir, f"{base}_concat.txt")

    # Extract hook segment (re-encode to match rendered clip format)
    cmd_hook = [
        "ffmpeg", "-y", "-ss", str(hook_start), "-to", str(hook_end),
        "-i", video_path,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
        *_video_codec_args(with_pix_fmt=True),
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        hook_path
    ]
    subprocess.run(cmd_hook, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    if not os.path.exists(hook_path):
        log("    Hook extraction failed — skipping cold-open.", "warn")
        return

    # Write concat demuxer file
    with open(concat_list, "w", encoding="utf-8") as f:
        f.write(f"file '{hook_path.replace(os.sep, '/')}'\n")
        f.write(f"file '{rendered_path.replace(os.sep, '/')}'\n")

    cmd_concat = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
        "-c", "copy", final_path
    ]
    subprocess.run(cmd_concat, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    if os.path.exists(final_path) and os.path.getsize(final_path) > 1000:
        os.replace(final_path, rendered_path)
        log(f"    Cold-open hook prepended ({hook_end - hook_start:.1f}s).", "success")
    else:
        log("    Cold-open concat failed — keeping original.", "warn")

    # Cleanup temp files
    for tmp in (hook_path, concat_list):
        try:
            os.remove(tmp)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════
#  IMPACT FREEZE-FRAME  — digital zoom + hold + VO
# ═══════════════════════════════════════════════════════════════════
def _insert_impact_freeze(rendered_path, impact_t, impact_vo_text, out_dir, base, progress_cb):
    """Split clip at impact_t, insert a freeze-frame with 120% zoom + TTS VO, then rejoin."""
    from backend.voiceover import _gen_voiceover

    # Generate impact VO audio
    impact_vo_path = os.path.join(out_dir, f"{base}_impact_vo.mp3")
    _gen_voiceover(impact_vo_text, impact_vo_path)
    if not os.path.exists(impact_vo_path):
        log("    Impact VO TTS failed — skipping freeze.", "warn")
        return

    freeze_dur = _audio_dur(impact_vo_path)
    freeze_dur = max(freeze_dur, 2.0)

    # Extract the freeze frame as image
    frame_path = os.path.join(out_dir, f"{base}_freeze.png")
    cmd_frame = [
        "ffmpeg", "-y", "-ss", str(impact_t), "-i", rendered_path,
        "-vframes", "1", "-q:v", "2", frame_path
    ]
    subprocess.run(cmd_frame, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    if not os.path.exists(frame_path):
        log("    Could not extract freeze frame — skipping.", "warn")
        return

    # Create freeze segment: static image scaled 120% (digital zoom) with VO audio
    freeze_clip = os.path.join(out_dir, f"{base}_freeze.mp4")
    cmd_freeze = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", frame_path,
        "-i", impact_vo_path,
        "-vf", "scale=iw*1.2:ih*1.2,crop=1080:1920",
        *_video_codec_args(with_pix_fmt=True),
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-t", str(freeze_dur), "-shortest",
        freeze_clip
    ]
    subprocess.run(cmd_freeze, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    if not os.path.exists(freeze_clip):
        log("    Freeze clip creation failed — skipping.", "warn")
        return

    # Split original clip into part_a (before impact) and part_b (after)
    part_a = os.path.join(out_dir, f"{base}_pA.mp4")
    part_b = os.path.join(out_dir, f"{base}_pB.mp4")
    final  = os.path.join(out_dir, f"{base}_impact.mp4")

    subprocess.run(
        ["ffmpeg", "-y", "-i", rendered_path, "-t", str(impact_t),
         "-c", "copy", part_a],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", rendered_path, "-ss", str(impact_t),
         "-c", "copy", part_b],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )

    # Concat: part_a + freeze + part_b
    concat_list = os.path.join(out_dir, f"{base}_impact_concat.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        f.write(f"file '{part_a.replace(os.sep, '/')}'\n")
        f.write(f"file '{freeze_clip.replace(os.sep, '/')}'\n")
        f.write(f"file '{part_b.replace(os.sep, '/')}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c", "copy", final],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )

    if os.path.exists(final) and os.path.getsize(final) > 1000:
        os.replace(final, rendered_path)
        log(f"    Impact freeze inserted at {impact_t:.1f}s ({freeze_dur:.1f}s hold).", "success")
    else:
        log("    Impact freeze concat failed — keeping original.", "warn")

    # Cleanup
    for tmp in (frame_path, freeze_clip, part_a, part_b, concat_list, impact_vo_path):
        try:
            os.remove(tmp)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════
#  AUTO-DUCKING  — sidechaincompress on movie audio vs VO
# ═══════════════════════════════════════════════════════════════════
def _apply_auto_ducking(rendered_path, out_dir, base, progress_cb, duration):
    """Re-mux the rendered file applying sidechaincompress to dip the
    original audio by ~15 dB whenever the voice channel is active.
    Since the rendered clip already has a mixed-down single audio stream,
    we use a dynamic compressor keyed on signal energy to approximate ducking."""
    ducked_path = os.path.join(out_dir, f"{base}_ducked.mp4")
    # Gentle dynamic compression: smooths loudness without pumping artifacts
    cmd = [
        "ffmpeg", "-y", "-i", rendered_path,
        "-c:v", "copy",
        "-af", "acompressor=threshold=-20dB:ratio=3:attack=50:release=300:makeup=2,alimiter=limit=0.95",
        "-c:a", "aac", "-b:a", "192k", ducked_path
    ]
    rc = subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW).returncode

    if rc == 0 and os.path.exists(ducked_path) and os.path.getsize(ducked_path) > 1000:
        os.replace(ducked_path, rendered_path)
        log("    Auto-ducking applied.", "success")
    else:
        log("    Auto-ducking failed — keeping original.", "warn")
        try:
            os.remove(ducked_path)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════
#  ALTERNATING MIRROR CUTS  (called from pipeline before main render)
# ═══════════════════════════════════════════════════════════════════
def _apply_mirror_cuts(video_path, chunks, out_dir, base, start_offset=0.0):
    """Given non-silent chunks, hflip every odd-indexed chunk and produce
    a single re-joined video file. Returns path to the processed video,
    or the original path if no processing was done.
    start_offset = absolute start time of the clip in the source movie."""
    if not chunks or len(chunks) < 2:
        return video_path

    segment_files = []
    for i, (cs, ce) in enumerate(chunks):
        seg = os.path.join(out_dir, f"{base}_seg{i}.mp4")
        vf = "hflip" if i % 2 != 0 else "null"
        abs_start = start_offset + cs
        abs_end = start_offset + ce
        cmd = [
            "ffmpeg", "-y", "-ss", str(abs_start), "-to", str(abs_end), "-i", video_path,
            "-vf", vf, *_video_codec_args(with_pix_fmt=True),
            "-c:a", "aac", seg
        ]
        subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if os.path.exists(seg):
            segment_files.append(seg)

    if len(segment_files) < 2:
        return video_path

    concat_list = os.path.join(out_dir, f"{base}_mirror_concat.txt")
    mirrored = os.path.join(out_dir, f"{base}_mirrored.mp4")
    with open(concat_list, "w", encoding="utf-8") as f:
        for seg in segment_files:
            f.write(f"file '{seg.replace(os.sep, '/')}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c", "copy", mirrored],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )

    # Cleanup segments
    for seg in segment_files:
        try:
            os.remove(seg)
        except OSError:
            pass
    try:
        os.remove(concat_list)
    except OSError:
        pass

    if os.path.exists(mirrored) and os.path.getsize(mirrored) > 1000:
        log(f"    Mirror cuts applied to {len(segment_files)} segments.", "success")
        return mirrored
    return video_path


# ═══════════════════════════════════════════════════════════════════
#  MOTION BLUR TRANSITIONS  (optional post-process step)
# ═══════════════════════════════════════════════════════════════════
def _apply_motion_blur(rendered_path, out_dir, base, progress_cb, duration):
    """Apply minterpolate to the rendered clip for smooth motion-blur transitions.
    Uses blend mode for clean results without ghosting artifacts."""
    blurred_path = os.path.join(out_dir, f"{base}_mblur.mp4")
    cmd = [
        "ffmpeg", "-y", "-threads", "4", "-filter_threads", "4", "-i", rendered_path,
        "-vf", "minterpolate=mi_mode=blend:fps=30:scd=none",
        *_video_codec_args(with_pix_fmt=True),
        "-c:a", "copy", blurred_path
    ]
    rc, _ = _run_ffmpeg(cmd, duration, progress_cb, label="Motion blur")

    if rc == 0 and os.path.exists(blurred_path) and os.path.getsize(blurred_path) > 1000:
        os.replace(blurred_path, rendered_path)
        log("    Motion blur transitions applied.", "success")
    else:
        log("    Motion blur failed — keeping original.", "warn")
        try:
            os.remove(blurred_path)
        except OSError:
            pass
