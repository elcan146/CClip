#!/usr/bin/env python3
"""Audio processing functions for CClip."""

import os
import re
import subprocess

from config import log


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
        # Use CUDA GPU if available, fallback to CPU
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda":
            cmd_d = ["demucs", "-d", "cuda", "--two-stems=vocals", "-o", demucs_out, clip_audio]
            log("    Demucs using GPU (CUDA)...", "info")
        else:
            cmd_d = ["demucs", "--two-stems=vocals", "-o", demucs_out, clip_audio]
            log("    Demucs using CPU (no CUDA available)...", "info")
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


# ═══════════════════════════════════════════════════════════════════
#  SILENCE DETECTION  (Tempo Booster)
# ═══════════════════════════════════════════════════════════════════
def _detect_silences(audio_path, threshold_db=-40, min_silence_dur=0.3):
    """Use FFmpeg silencedetect to find silent regions.
    Returns list of (silence_start, silence_end) tuples in seconds."""
    cmd = [
        "ffmpeg", "-i", audio_path, "-af",
        f"silencedetect=noise={threshold_db}dB:d={min_silence_dur}",
        "-f", "null", "-"
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    output = result.stderr

    silences = []
    starts = re.findall(r"silence_start:\s*([\d.]+)", output)
    ends   = re.findall(r"silence_end:\s*([\d.]+)", output)

    for s, e in zip(starts, ends):
        silences.append((float(s), float(e)))

    log(f"    Silence detect: found {len(silences)} silent region(s).", "info")
    return silences


def _get_non_silent_chunks(audio_path, total_duration, threshold_db=-40, min_silence_dur=0.3):
    """Return list of (chunk_start, chunk_end) for non-silent segments.
    These timestamps are relative to the audio file (0-based)."""
    silences = _detect_silences(audio_path, threshold_db, min_silence_dur)

    if not silences:
        return [(0.0, total_duration)]

    chunks = []
    cursor = 0.0
    for sil_start, sil_end in silences:
        if sil_start > cursor + 0.05:
            chunks.append((cursor, sil_start))
        cursor = sil_end
    if cursor < total_duration - 0.05:
        chunks.append((cursor, total_duration))

    log(f"    Non-silent chunks: {len(chunks)} segment(s), "
        f"total kept: {sum(e - s for s, e in chunks):.1f}s / {total_duration:.1f}s", "info")
    return chunks


# ═══════════════════════════════════════════════════════════════════
#  DYNAMIC SFX INJECTION
# ═══════════════════════════════════════════════════════════════════
def _overlay_sfx_at_cuts(base_audio_path, sfx_path, cut_timestamps, output_path):
    """Overlay a short SFX file (e.g. whoosh) at each timestamp in cut_timestamps.
    Uses FFmpeg amerge/amix to stamp the SFX at precise positions.

    Parameters:
        base_audio_path : str  - path to the main audio track
        sfx_path        : str  - path to the .mp3/.wav SFX file
        cut_timestamps  : list - list of float seconds where SFX should play
        output_path     : str  - where to write the result
    """
    if not cut_timestamps or not os.path.exists(sfx_path):
        if not os.path.exists(sfx_path):
            log(f"    SFX file not found: {sfx_path}", "warn")
        return base_audio_path

    # Build adelay filter chain: for each cut, create a delayed copy of the SFX
    inputs = ["-i", base_audio_path]
    filter_parts = []
    mix_inputs = ["[0:a]"]

    for i, ts in enumerate(cut_timestamps):
        inputs.extend(["-i", sfx_path])
        delay_ms = int(ts * 1000)
        idx = i + 1
        filter_parts.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms},volume=0.7[sfx{i}]")
        mix_inputs.append(f"[sfx{i}]")

    # Limit to 30 SFX overlays to avoid filter complexity explosion
    max_sfx = 30
    if len(cut_timestamps) > max_sfx:
        log(f"    SFX: capping at {max_sfx} overlays (had {len(cut_timestamps)})", "warn")
        filter_parts = filter_parts[:max_sfx]
        mix_inputs = mix_inputs[:max_sfx + 1]
        inputs = inputs[:1 + max_sfx * 2]

    n = len(mix_inputs)
    mix_str = "".join(mix_inputs) + f"amix=inputs={n}:duration=first:dropout_transition=0[out]"
    full_filter = ";".join(filter_parts) + ";" + mix_str if filter_parts else mix_str

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", full_filter,
        "-map", "[out]", "-c:a", "pcm_s16le", output_path
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    if result.returncode != 0:
        log(f"    SFX overlay failed: {result.stderr[-200:]}", "warn")
        return base_audio_path

    log(f"    SFX injected at {len(filter_parts)} cut point(s).", "success")
    return output_path
