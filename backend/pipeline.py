#!/usr/bin/env python3
"""FFmpeg rendering functions for CClip."""

import os
import re
import subprocess
import random
import time
from functools import lru_cache

from config import S, log, CANCEL, update_s, save_settings
from utils.helpers import _audio_dur, _get_audio_track
from backend.transcription import _transcribe, _mock_transcript
from backend.ai import _ai_clips, _mock_clips
from backend.voiceover import _gen_voiceover
from backend.subtitles import _build_subs
from backend.audio import _extract_audio


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
    """Return video codec arguments based on hardware availability."""
    if _nvenc_available():
        if with_pix_fmt:
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-pix_fmt", "yuv420p"]
        return ["-c:v", "h264_nvenc", "-preset", "p4"]
    else:
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
            "setsar=1[fg]"
        )
    elif strategy == "Crop to Center":
        return (
            "[v_fg]setpts=PTS-STARTPTS,"
            "scale=1080:1080:force_original_aspect_ratio=increase,"
            "crop=1080:1080,"
            "setsar=1[fg]"
        )
    else:
        # "Blur Padding" — gentle cinematic zoom drift (±1% over 8s)
        return (
            "[v_fg]setpts=PTS-STARTPTS,"
            "crop=w='min(iw\\,ih)':h='min(iw\\,ih)',"
            "scale=w='trunc(1080*(1.01+0.01*sin(2*PI*t/8))/2)*2':"
                  "h='trunc(1080*(1.01+0.01*sin(2*PI*t/8))/2)*2':eval=frame,"
            "crop=1080:1080,"
            "setsar=1[fg]"
        )


# ═══════════════════════════════════════════════════════════════════
#  FFMPEG PROGRESS WATCHER
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
#  CORE RENDER
# ═══════════════════════════════════════════════════════════════════
def _render(video_path, sub_path, vo_path, vocals_path, music_path,
            start, end, out_path, audio_track, progress_cb, duration,
            clip_data=None, source_video=None):
    """
    Render a single clip cleanly.

    VIDEO PIPELINE:
      • Blurred background + foreground overlay (fill_strategy aware)
      • Color grade (contrast / saturation / brightness from settings)
      • Light sharpening, soft vignette
      • Smooth fade-in / fade-out
      • Subtitle burn-in
      • Optional logo overlay
      • Mild, stable Content-ID defeat:
          - Slight hue shift (imperceptible, constant per render)
          - One-pixel crop offset (removes exact frame borders)
          - Very light film grain (noise=c0s=2, invisible at normal viewing)

    AUDIO PIPELINE:
      • Isolated vocals (or raw movie audio as fallback)
      • AI voiceover
      • Background music
      • Clean volume ducking (VO active → movie audio lowers)
      • EBU R128 loudness normalisation (-14 LUFS)
      • Gentle compression + limiter (no distortion)
    """
    if clip_data is None:
        clip_data = {}
    if source_video is None:
        source_video = video_path

    safe_sub   = sub_path.replace("\\", "/").replace(":", "\\:")
    fade_in_d  = 0.4
    fade_out_d = 0.5
    fade_out   = max(duration - fade_out_d, 0)
    vo_dur     = _audio_dur(vo_path)

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

    # ── Mild Content-ID defeat (stable, won't distort output) ──────
    # 1. Tiny hue rotation — imperceptible shift, unique per render
    rand_hue = round(random.uniform(1.0, 2.0), 2)
    # 2. One-pixel border crop — removes exact frame edges
    # No tblend, no rotation, no afftfilt, no crystalizer

    # ── Logo ──────────────────────────────────────────────────────
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "darklogo.png")
    has_logo  = os.path.exists(logo_path) and S.get("logo_enabled", True)

    if has_logo:
        logo_y_expr = "(H-h)/2-20*cos(2*PI*t*60/240)"
        logo_chain = (
            "[1:v]loop=loop=-1:size=1[logo_loop];"
            "[logo_loop]scale=250:-1,colorchannelmixer=aa=0.24,fps=60[logo_scaled];"
            f"[composed][logo_scaled]overlay=(W-w)/2:'{logo_y_expr}':shortest=1[with_logo];"
            "[with_logo]"
        )
    else:
        logo_chain = "[composed]"

    fg_filter = _build_fg_filter()

    # ── VIDEO FILTERGRAPH ─────────────────────────────────────────
    video_filter = (
        # Split source into background + foreground layers
        f"[0:v]split=2[v_bg][v_fg];"

        # Background: blurred, dimmed, fills full 1080×1920
        f"[v_bg]setpts=PTS-STARTPTS,"
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"boxblur={v_blur}:2,"
        f"eq=brightness={v_bg_bri}:saturation={v_bg_sat}[bg];"

        # Foreground: fill-strategy aware, centred at y=420
        f"{fg_filter};"
        "[bg][fg]overlay=0:420[composed];"

        # Logo (or pass-through if no logo)
        f"{logo_chain}"

        # Color grade
        f"eq=contrast={v_fg_con}:saturation={v_fg_sat}:brightness={v_fg_bri},"

        # Light sharpening (luma only, no haloing)
        "unsharp=3:3:0.5:3:3:0.0,"

        # Soft cinematic vignette
        "vignette=PI/5:a=1.2,"

        # Mild CID defeat: hue shift + border crop + very light grain
        f"hue=h={rand_hue},"
        "crop=w=iw-2:h=ih-2:x=1:y=1,"
        "scale=1080:1920:flags=lanczos,"
        "noise=c0s=2:c0f=t,"   # very light grain, invisible at normal size

        # Fades
        f"fade=t=in:st=0:d={fade_in_d},"
        f"fade=t=out:st={fade_out}:d={fade_out_d},"

        # Subtitle burn-in
        f"ass='{safe_sub}',"

        # Playback speed (atempo handles audio side separately)
        f"setpts=PTS/{v_speed}[final_v]"
    )

    # ── AUDIO FILTERGRAPH ─────────────────────────────────────────
    # Clean audio processing:
    #   • Volume ducking (VO active → movie audio drops)
    #   • Gentle compression on the mixed output
    #   • EBU R128 loudnorm
    #   • Hard limiter as safety net
    # NO pitch shifting, NO crystalizer, NO afftfilt, NO tremolo,
    # NO double-resampling — these were causing all the distortion.

    if vocals_path:
        # ── WITH DEMUCS VOCALS ─────────────────────────────────
        if has_logo:
            # Inputs: 0=video  1=logo  2=voiceover  3=bgm  4=vocals
            movie_audio_idx = 4
            vo_idx  = 2
            bgm_idx = 3
            audio_filter = (
                # Isolated vocals — volume-duck when VO is active
                f"[{movie_audio_idx}:a]"
                f"volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[vocals_a];"

                # AI voiceover
                f"[{vo_idx}:a]"
                f"volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"

                # Background music
                f"[{bgm_idx}:a]volume={v_bgm},"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"

                # Mix → normalise → compress → limit
                "[vocals_a][voice_a][bgm_a]"
                "amix=inputs=3:duration=first:dropout_transition=0,"
                "loudnorm=I=-14:LRA=11:TP=-1.5,"
                "acompressor=threshold=-18dB:ratio=2.5:attack=10:release=150:makeup=2,"
                f"alimiter=limit=0.95,"
                f"atempo={v_speed}[final_a]"
            )
        else:
            # Inputs: 0=video  1=voiceover  2=bgm  3=vocals
            movie_audio_idx = 3
            vo_idx  = 1
            bgm_idx = 2
            audio_filter = (
                f"[{movie_audio_idx}:a]"
                f"volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[vocals_a];"

                f"[{vo_idx}:a]"
                f"volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"

                f"[{bgm_idx}:a]volume={v_bgm},"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"

                "[vocals_a][voice_a][bgm_a]"
                "amix=inputs=3:duration=first:dropout_transition=0,"
                "loudnorm=I=-14:LRA=11:TP=-1.5,"
                "acompressor=threshold=-18dB:ratio=2.5:attack=10:release=150:makeup=2,"
                f"alimiter=limit=0.95,"
                f"atempo={v_speed}[final_a]"
            )

        clip_duration = end - start
        cmd_base = ["ffmpeg", "-y", "-ss", str(start), "-t", str(clip_duration), "-i", video_path]
        if has_logo:
            cmd_base.extend(["-i", logo_path])
        cmd_base.extend([
            "-stream_loop", "-1", "-i", vo_path,
            "-stream_loop", "-1", "-i", music_path,
            "-i", vocals_path,
            "-filter_complex_threads", "1",
            "-filter_complex", f"{video_filter};{audio_filter}",
            "-map", "[final_v]", "-map", "[final_a]",
            *_video_codec_args(with_pix_fmt=True),
            "-c:a", "aac", "-b:a", "192k", out_path
        ])
        command = cmd_base

    else:
        # ── FALLBACK: raw movie audio ──────────────────────────
        if has_logo:
            # Inputs: 0=video  1=logo  2=voiceover  3=bgm
            audio_filter = (
                f"[0:a:{audio_track}]"
                f"volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[movie_a];"

                "[2:a]"
                f"volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"

                f"[3:a]volume={v_bgm},"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"

                "[movie_a][voice_a][bgm_a]"
                "amix=inputs=3:duration=first:dropout_transition=0,"
                "loudnorm=I=-14:LRA=11:TP=-1.5,"
                "bass=g=2:f=100,treble=g=1.5:f=8000,"
                "acompressor=threshold=-18dB:ratio=2.5:attack=10:release=150:makeup=2,"
                f"alimiter=limit=0.95,"
                f"atempo={v_speed}[final_a]"
            )
        else:
            # Inputs: 0=video  1=voiceover  2=bgm
            audio_filter = (
                f"[0:a:{audio_track}]"
                f"volume='if(lt(t,{vo_dur}),{v_m_duck},{v_m_base})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[movie_a];"

                "[1:a]"
                f"volume='if(lt(t,{vo_dur}),{v_vo_base},{v_vo_duck})':eval=frame,"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice_a];"

                f"[2:a]volume={v_bgm},"
                "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bgm_a];"

                "[movie_a][voice_a][bgm_a]"
                "amix=inputs=3:duration=first:dropout_transition=0,"
                "loudnorm=I=-14:LRA=11:TP=-1.5,"
                "bass=g=2:f=100,treble=g=1.5:f=8000,"
                "acompressor=threshold=-18dB:ratio=2.5:attack=10:release=150:makeup=2,"
                f"alimiter=limit=0.95,"
                f"atempo={v_speed}[final_a]"
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
    """Apply optional post-render effects in sequence."""
    out_dir = os.path.dirname(rendered_path)
    base    = os.path.splitext(os.path.basename(rendered_path))[0]

    # 1. Cold-Open Hook
    if S.get("enable_cold_open") and clip_data.get("hook_start") is not None:
        hook_start = clip_data["hook_start"]
        hook_end   = clip_data["hook_end"]
        if hook_end - hook_start >= 1.0:
            log("    Post-process: Cold-Open Hook...", "info")
            _prepend_cold_open(video_path, rendered_path, hook_start, hook_end, out_dir, base, progress_cb)

    # 2. Impact Freeze-Frame
    if S.get("enable_impact_vo") and clip_data.get("impact_time") is not None:
        impact_t       = clip_data["impact_time"] - start
        impact_vo_text = clip_data.get("impact_vo", "")
        if 0 < impact_t < duration and impact_vo_text:
            log("    Post-process: Impact Freeze-Frame...", "info")
            _insert_impact_freeze(rendered_path, impact_t, impact_vo_text, out_dir, base, progress_cb)

    # 3. Auto-Ducking
    if S.get("enable_auto_ducking"):
        log("    Post-process: Auto-Ducking...", "info")
        _apply_auto_ducking(rendered_path, out_dir, base, progress_cb, duration)


# ═══════════════════════════════════════════════════════════════════
#  COLD-OPEN HOOK
# ═══════════════════════════════════════════════════════════════════
def _prepend_cold_open(video_path, rendered_path, hook_start, hook_end, out_dir, base, progress_cb):
    """Extract a short hook from the source movie, concat before rendered clip."""
    hook_path   = os.path.join(out_dir, f"{base}_hook.mp4")
    final_path  = os.path.join(out_dir, f"{base}_hooked.mp4")
    concat_list = os.path.join(out_dir, f"{base}_concat.txt")

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

    for tmp in (hook_path, concat_list):
        try:
            os.remove(tmp)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════
#  IMPACT FREEZE-FRAME
# ═══════════════════════════════════════════════════════════════════
def _insert_impact_freeze(rendered_path, impact_t, impact_vo_text, out_dir, base, progress_cb):
    """Split clip at impact_t, insert a freeze-frame (120% zoom + TTS VO), rejoin."""
    from backend.voiceover import _gen_voiceover

    impact_vo_path = os.path.join(out_dir, f"{base}_impact_vo.mp3")
    _gen_voiceover(impact_vo_text, impact_vo_path)
    if not os.path.exists(impact_vo_path):
        log("    Impact VO TTS failed — skipping freeze.", "warn")
        return

    freeze_dur = max(_audio_dur(impact_vo_path), 2.0)

    frame_path = os.path.join(out_dir, f"{base}_freeze.png")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(impact_t), "-i", rendered_path,
         "-vframes", "1", "-q:v", "2", frame_path],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )

    if not os.path.exists(frame_path):
        log("    Could not extract freeze frame — skipping.", "warn")
        return

    freeze_clip = os.path.join(out_dir, f"{base}_freeze.mp4")
    subprocess.run(
        ["ffmpeg", "-y",
         "-loop", "1", "-i", frame_path,
         "-i", impact_vo_path,
         "-vf", "scale=iw*1.2:ih*1.2,crop=1080:1920",
         *_video_codec_args(with_pix_fmt=True),
         "-c:a", "aac", "-ar", "44100", "-ac", "2",
         "-t", str(freeze_dur), "-shortest",
         freeze_clip],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )

    if not os.path.exists(freeze_clip):
        log("    Freeze clip creation failed — skipping.", "warn")
        return

    part_a      = os.path.join(out_dir, f"{base}_pA.mp4")
    part_b      = os.path.join(out_dir, f"{base}_pB.mp4")
    final       = os.path.join(out_dir, f"{base}_impact.mp4")
    concat_list = os.path.join(out_dir, f"{base}_impact_concat.txt")

    subprocess.run(
        ["ffmpeg", "-y", "-i", rendered_path, "-t", str(impact_t), "-c", "copy", part_a],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", rendered_path, "-ss", str(impact_t), "-c", "copy", part_b],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )

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

    for tmp in (frame_path, freeze_clip, part_a, part_b, concat_list, impact_vo_path):
        try:
            os.remove(tmp)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════
#  AUTO-DUCKING
# ═══════════════════════════════════════════════════════════════════
def _apply_auto_ducking(rendered_path, out_dir, base, progress_cb, duration):
    """Gentle dynamic compression to smooth loudness without pumping."""
    ducked_path = os.path.join(out_dir, f"{base}_ducked.mp4")
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
#  ALTERNATING MIRROR CUTS
# ═══════════════════════════════════════════════════════════════════
def _apply_mirror_cuts(video_path, chunks, out_dir, base, start_offset=0.0):
    """hflip every odd-indexed chunk, rejoin into a single file."""
    if not chunks or len(chunks) < 2:
        return video_path

    segment_files = []
    for i, (cs, ce) in enumerate(chunks):
        seg = os.path.join(out_dir, f"{base}_seg{i}.mp4")
        vf  = "hflip" if i % 2 != 0 else "null"
        abs_start = start_offset + cs
        abs_end   = start_offset + ce
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
    mirrored    = os.path.join(out_dir, f"{base}_mirrored.mp4")
    with open(concat_list, "w", encoding="utf-8") as f:
        for seg in segment_files:
            f.write(f"file '{seg.replace(os.sep, '/')}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c", "copy", mirrored],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )

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
#  MOTION BLUR TRANSITIONS
# ═══════════════════════════════════════════════════════════════════
def _apply_motion_blur(rendered_path, out_dir, base, progress_cb, duration):
    """Apply minterpolate for smooth motion-blur transitions."""
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


# ═══════════════════════════════════════════════════════════════════
#  BATCH ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════
def run_batch(progress_cb, macro_cb, finish_cb):
    """Process every movie in the input folder: transcribe, AI clip select,
    voiceover, subtitles, render, and update stats."""
    import warnings
    warnings.filterwarnings("ignore")

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log("Missing: google-genai  --  pip install google-genai", "error")
        finish_cb(False)
        return

    if not S["test_mode"] and not S["gemini_key"]:
        log("Gemini API key is empty!", "error")
        finish_cb(False)
        return

    valid_extensions = ('.mp4', '.mkv', '.mov', '.avi')
    movies = [f for f in os.listdir(S["in_folder"]) if f.lower().endswith(valid_extensions)]

    if not movies:
        log(f"No movies found in: {S['in_folder']}", "warn")
        finish_cb(False)
        return

    client = None if S["test_mode"] else genai.Client(api_key=S["gemini_key"])
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
                _render(
                    movie_path, sub_path, vo_path, None, music_path,
                    start, end, output_mp4, audio_track, progress_cb,
                    end - start, clip_data=data, source_video=movie_path
                )
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