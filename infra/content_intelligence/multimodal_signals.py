"""Deterministic, local audio + visual signal extraction for candidate salience.

Produces per-second, within-source salience signals (loudness, spike, ZCR
noisiness; motion, scene-cut, flash) with NO model call and $0 marginal cost.
Salience is RELATIVE to the source media (percentile-rank within the clip) and
is never labelled "viral" or treated as semantic importance — it is evidence for
the semantic stage's judgment, nothing more.

Extraction is bounded: audio is downmixed to 16 kHz mono, video is sampled at
1 fps at 64x64 grayscale, every ffmpeg call has a hard timeout, and results are
cached to a ``signal_manifest.json`` when a manifest path is supplied
(deterministic replay).

Candidate-window math (normalization, peak detection, window generation, fusion)
is NOT implemented here. It lives in ``candidate_fusion`` — the single owner of
that contract — and is consumed from there. This module only turns media into
raw per-second signals and into signal-peak windows.

Python requirement: standard library only (no third-party packages).
Binary requirement: ``ffmpeg`` on PATH (decoding). ``ffprobe`` is not used —
duration is derived from the decoded sample counts.
"""

from __future__ import annotations

import array
import json
import os
import subprocess
from typing import Any

try:
    from .candidate_fusion import (  # package context (content_intelligence.*)
        find_peaks,
        gate_by_magnitude,
        generate_signal_windows,
        normalize_signal,
    )
except ImportError:  # top-level context (tests run from this directory)
    from candidate_fusion import (  # type: ignore
        find_peaks,
        gate_by_magnitude,
        generate_signal_windows,
        normalize_signal,
    )

__all__ = [
    "extract_audio_signals",
    "extract_visual_signals",
    "analyze_media_signals",
]

# Fixed extraction parameters. Recorded in every manifest so a signal window can
# always be traced back to the thresholds that produced it.
AUDIO_SAMPLE_RATE = 16000
VISUAL_FPS = 1
VISUAL_SIZE = 64
SPIKE_ROLLING_WINDOW_S = 15
AUDIBILITY_FLOOR = 0.05
PEAK_PERCENTILE = 0.90
SCENE_PERCENTILE = 0.95
PEAK_MIN_DISTANCE_S = 10
WINDOW_BEFORE_S = 5
WINDOW_AFTER_S = 15
SUBPROCESS_TIMEOUT_S = 300

# Salience floors applied AFTER percentile ranking, on the raw scale (see
# gate_by_magnitude). Both are relative to the source itself. Without them a
# steady source manufactures candidates out of float noise, because the max of
# any non-constant signal always ranks 1.0. Calibrated against real media on
# this machine: steady tone = 0.0008x median loudness (gated), real speech/boxing
# clips = 0.28x-1.46x (kept), a 3s burst = 19x (kept); static frames = 0 gray
# levels (gated) vs 7-79 for real footage (kept).
AUDIO_SALIENCE_FLOOR = 0.10   # spike must be >= 10% of the source's median loudness
VISUAL_SALIENCE_FLOOR = 1.0   # >= 1 gray level of mean abs change at 64x64


def _thresholds(max_seconds: int | None) -> dict[str, Any]:
    """The exact parameters that produced a manifest (auditability)."""
    return {
        "normalization": "percentile_within_source",
        "peak_percentile": PEAK_PERCENTILE,
        "scene_percentile": SCENE_PERCENTILE,
        "peak_min_distance_s": PEAK_MIN_DISTANCE_S,
        "window_before_s": WINDOW_BEFORE_S,
        "window_after_s": WINDOW_AFTER_S,
        "audio_sample_rate": AUDIO_SAMPLE_RATE,
        "audibility_floor": AUDIBILITY_FLOOR,
        "spike_rolling_window_s": SPIKE_ROLLING_WINDOW_S,
        "visual_fps": VISUAL_FPS,
        "visual_size": VISUAL_SIZE,
        "audio_salience_floor": AUDIO_SALIENCE_FLOOR,
        "visual_salience_floor": VISUAL_SALIENCE_FLOOR,
        "max_seconds": max_seconds,
        "subprocess_timeout_s": SUBPROCESS_TIMEOUT_S,
    }


def _run_ffmpeg(args: list[str], timeout: int = SUBPROCESS_TIMEOUT_S) -> bytes:
    """Run ffmpeg and return stdout. Bounded and hang-proof: the process is
    killed if it exceeds ``timeout``, so an undecodable or stalled input cannot
    hang a run. A killed decode raises ``TimeoutError`` rather than being
    silently reported as empty media."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise TimeoutError(f"ffmpeg exceeded {timeout}s") from None
    return out or b""


def _decoder_args(video_path: str, max_seconds: int | None) -> list[str]:
    args = ["ffmpeg", "-y", "-nostdin", "-i", video_path]
    if max_seconds:
        args += ["-t", str(int(max_seconds))]
    return args


def _median(ordered: list[float]) -> float:
    """Median of an already-sorted list (mean of the two middle values when even)."""
    k = len(ordered)
    if k == 0:
        return 0.0
    mid = k // 2
    if k % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _rms(chunk: array.array, sample_rate: int) -> float:
    total = 0
    for x in chunk:
        total += x * x
    return (total / sample_rate) ** 0.5


def _zcr(chunk: array.array) -> float:
    """Zero-crossing rate over consecutive samples (n-1 comparisons)."""
    if len(chunk) < 2:
        return 0.0
    changes = 0
    prev_negative = chunk[0] < 0
    for x in chunk:
        negative = x < 0
        if negative != prev_negative:
            changes += 1
            prev_negative = negative
    return changes / (len(chunk) - 1)


def extract_audio_signals(
    video_path: str,
    sample_rate: int = AUDIO_SAMPLE_RATE,
    max_seconds: int | None = None,
) -> dict[str, list[float]]:
    """Per-second loudness (RMS), noisiness (audibility-gated ZCR), and spike
    (deviation from a rolling ~30s median). Empty/missing audio yields empty
    arrays, never a raise. ``max_seconds`` bounds how much audio is decoded."""
    audio = _run_ffmpeg(_decoder_args(video_path, max_seconds) + [
        "-vn", "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", "1",
        "-f", "s16le", "-",
    ])
    if not audio:
        return {"loudness": [], "noisiness_zcr": [], "spike": []}

    pcm = array.array("h")
    pcm.frombytes(audio[: len(audio) // 2 * 2])

    n = len(pcm) // sample_rate
    if n == 0:
        if len(pcm) == 0:
            return {"loudness": [], "noisiness_zcr": [], "spike": []}
        return {
            "loudness": [_rms(pcm, len(pcm))],
            "noisiness_zcr": [_zcr(pcm)],
            "spike": [0.0],
        }

    chunks = [pcm[i * sample_rate:(i + 1) * sample_rate] for i in range(n)]
    rms = [_rms(c, sample_rate) for c in chunks]

    # Audibility gate: near-silent seconds must not report "noisy" ZCR — a
    # quiet second is irrelevant, not salient.
    peak = max(rms) if rms else 0.0
    noisiness: list[float] = []
    for chunk, level in zip(chunks, rms):
        audible = peak > 0 and level > peak * AUDIBILITY_FLOOR
        noisiness.append(_zcr(chunk) if audible else 0.0)

    spikes: list[float] = []
    for i in range(n):
        lo = max(0, i - SPIKE_ROLLING_WINDOW_S)
        hi = min(n, i + SPIKE_ROLLING_WINDOW_S)
        median = _median(sorted(rms[lo:hi]))
        spikes.append(rms[i] - median if median > 0 else 0.0)

    return {"loudness": rms, "noisiness_zcr": noisiness, "spike": spikes}


def extract_visual_signals(
    video_path: str,
    fps: int = VISUAL_FPS,
    size: int = VISUAL_SIZE,
    max_seconds: int | None = None,
) -> dict[str, list[float]]:
    """Per-second motion (mean abs frame diff), scene-cut (positive motion
    derivative), and flash (frame-luminance diff). Sampled at ``fps`` and
    ``size``-px grayscale — bounded and cheap."""
    raw = _run_ffmpeg(_decoder_args(video_path, max_seconds) + [
        "-an", "-vf", f"fps={fps},scale={size}:{size},format=gray",
        "-vcodec", "rawvideo", "-f", "image2pipe", "-",
    ])
    if not raw:
        return {"motion": [], "scene_cut": [], "flash": []}

    frame_bytes = size * size
    n = len(raw) // frame_bytes
    if n < 2:
        return {"motion": [0.0] * n, "scene_cut": [0.0] * n, "flash": [0.0] * n}

    frames = [raw[i * frame_bytes:(i + 1) * frame_bytes] for i in range(n)]

    motion = [0.0]
    for i in range(1, n):
        previous, current = frames[i - 1], frames[i]
        total = 0
        for a, b in zip(previous, current):
            diff = a - b
            total += diff if diff >= 0 else -diff
        motion.append(total / frame_bytes)

    scene_cut = [0.0]
    for i in range(1, n):
        d = motion[i] - motion[i - 1]
        scene_cut.append(d if d > 0 else 0.0)

    lum = [sum(f) / frame_bytes for f in frames]
    flash = [0.0] + [abs(lum[i] - lum[i - 1]) for i in range(1, n)]

    return {"motion": motion, "scene_cut": scene_cut, "flash": flash}


def analyze_media_signals(
    video_path: str,
    manifest_out: str | None = None,
    max_seconds: int | None = None,
) -> dict[str, Any]:
    """Run full deterministic signal extraction; cache to ``manifest_out`` when set.

    Returns a manifest with raw per-second signals, detected peaks, candidate
    signal windows, the thresholds used, and duration. Media that decodes to no
    audio and no video yields ``duration_s: 0`` and an empty window list with
    ``error`` set — never invented peaks. Undecodable media raises
    ``FileNotFoundError`` / ``RuntimeError`` rather than reporting an empty
    result (absence of evidence must not look like evidence of absence).
    """
    if manifest_out and os.path.exists(manifest_out):
        try:
            with open(manifest_out, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass  # stale/unreadable manifest — recompute

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Media not found: {video_path}")

    try:
        audio = extract_audio_signals(video_path, max_seconds=max_seconds)
        visual = extract_visual_signals(video_path, max_seconds=max_seconds)
    except Exception as exc:  # noqa: BLE001 — surfaced as a classified result
        raise RuntimeError(f"Failed to process media {video_path}: {exc}") from exc

    if not audio["loudness"] and not visual["motion"]:
        return {
            "duration_s": 0,
            "signal_windows": [],
            "thresholds": _thresholds(max_seconds),
            "error": "malformed_or_empty_media",
        }

    norm_spike = normalize_signal(audio["spike"], method="percentile")
    norm_motion = normalize_signal(visual["motion"], method="percentile")
    norm_scene = normalize_signal(visual["scene_cut"], method="percentile")

    # Gate the ranks against the raw scale before peak detection. Ranking alone
    # guarantees a "peak" for any non-constant signal, so without this gate a
    # steady tone or a static frame yields a candidate that means nothing.
    if audio["loudness"]:
        audio_floor = AUDIO_SALIENCE_FLOOR * _median(sorted(audio["loudness"]))
        if audio_floor <= 0 and audio["spike"]:
            # Degenerate source (mostly digital silence): fall back to the
            # source's own peak so the floor stays relative, never absolute.
            audio_floor = AUDIO_SALIENCE_FLOOR * max(audio["spike"])
    else:
        audio_floor = 0.0
    norm_spike = gate_by_magnitude(norm_spike, audio["spike"], audio_floor)
    norm_motion = gate_by_magnitude(norm_motion, visual["motion"], VISUAL_SALIENCE_FLOOR)
    norm_scene = gate_by_magnitude(norm_scene, visual["scene_cut"], VISUAL_SALIENCE_FLOOR)

    max_len = max(len(audio["loudness"]), len(visual["motion"]))

    audio_peaks = find_peaks(norm_spike, threshold=PEAK_PERCENTILE,
                             min_distance=PEAK_MIN_DISTANCE_S)
    motion_peaks = find_peaks(norm_motion, threshold=PEAK_PERCENTILE,
                              min_distance=PEAK_MIN_DISTANCE_S)
    scene_peaks = find_peaks(norm_scene, threshold=SCENE_PERCENTILE,
                             min_distance=PEAK_MIN_DISTANCE_S)

    a_wins = generate_signal_windows(audio_peaks, basis="audio_spike", max_len=max_len,
                                     window_before=WINDOW_BEFORE_S, window_after=WINDOW_AFTER_S)
    m_wins = generate_signal_windows(motion_peaks, basis="visual_motion", max_len=max_len,
                                     window_before=WINDOW_BEFORE_S, window_after=WINDOW_AFTER_S)
    s_wins = generate_signal_windows(scene_peaks, basis="scene_cut", max_len=max_len,
                                     window_before=WINDOW_BEFORE_S, window_after=WINDOW_AFTER_S)

    res: dict[str, Any] = {
        "thresholds": _thresholds(max_seconds),
        "raw_signals": {"audio": audio, "visual": visual},
        "peaks": {"audio_spike": audio_peaks, "visual_motion": motion_peaks,
                  "scene_cut": scene_peaks},
        "signal_windows": a_wins + m_wins + s_wins,
        "duration_s": max_len,
    }

    if manifest_out:
        with open(manifest_out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)

    return res
