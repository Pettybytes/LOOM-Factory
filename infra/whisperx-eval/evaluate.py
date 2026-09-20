#!/usr/bin/env python3
"""Segment 5C.1 — WhisperX timestamp/alignment evaluation (isolated harness).

Answers ONE question with evidence: does WhisperX materially improve LOOM's
timestamp/alignment quality enough to justify adopting it?

It is deliberately NOT wired into the production pipeline. It compares, on the
same audio and the same ASR model size:

  BASELINE  faster-whisper with LOOM's real parameters, mirrored from
            openshorts/subtitles.py + transcribe_backends.py (model "small",
            cpu/int8, word_timestamps=True, vad_filter=True, beam_size=5).
            This is LOOM's CURRENT behaviour, not an invented baseline.
  WHISPERX  the same faster-whisper transcription, then WhisperX's wav2vec2
            forced alignment over it.

Because WhisperX transcribes with faster-whisper internally, the measured delta
is precisely the ALIGNMENT step — which is what 5C.1 is evaluating.

Cost: $0. Everything is local; no hosted/paid transcription service is used.

Reproduce (isolated env; see README.md):

    ~/.venvs/loom-whisperx-eval/bin/python evaluate.py \
        --out results.json --audio-dir /tmp/loom-5c1-audio <media> [<media> ...]

Every number printed is measured at runtime; nothing is hard-coded.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# LOOM's real current parameters — mirrored, not invented.
BASELINE_MODEL = os.environ.get("WHISPER_MODEL", "small")
BASELINE_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
BASELINE_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")
BASELINE_PARAMS = {
    "beam_size": 5,
    "vad_filter": True,
    "condition_on_previous_text": False,
    "word_timestamps": True,
}

PAUSE_MIN_S = 0.35          # a gap that reads as a real pause, not word spacing
ALIGN_CHUNK_S = 30          # whisperx recommends ~30s alignment chunks


# --------------------------------------------------------------------------
# media helpers
# --------------------------------------------------------------------------
def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def to_wav16k(src: str, dst: str) -> None:
    """Both backends consume the identical 16 kHz mono wav, so neither gets an
    input advantage."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1",
         "-ar", "16000", "-c:a", "pcm_s16le", dst],
        check=True, capture_output=True,
    )


# --------------------------------------------------------------------------
# baseline: LOOM's current behaviour
# --------------------------------------------------------------------------
def run_baseline(wav: str, model_cache: dict) -> dict:
    from faster_whisper import WhisperModel

    key = (BASELINE_MODEL, BASELINE_DEVICE, BASELINE_COMPUTE)
    if key not in model_cache:
        model_cache[key] = WhisperModel(BASELINE_MODEL, device=BASELINE_DEVICE,
                                        compute_type=BASELINE_COMPUTE)

    started = time.time()
    segments, info = model_cache[key].transcribe(wav, **BASELINE_PARAMS)
    materialized = list(segments)          # force decode inside the timing
    elapsed = time.time() - started

    out_segments, words = [], []
    for s in materialized:
        seg_words = [{"word": w.word, "start": float(w.start), "end": float(w.end)}
                     for w in (s.words or [])]
        out_segments.append({"start": float(s.start), "end": float(s.end),
                             "text": s.text, "words": seg_words})
        words.extend(seg_words)

    return {
        "backend": f"faster-whisper {_ver('faster-whisper')} ({BASELINE_MODEL}/{BASELINE_COMPUTE})",
        "language": info.language,
        "language_probability": float(getattr(info, "language_probability", 0.0)),
        "duration_s": float(getattr(info, "duration", 0.0)),
        "elapsed_s": round(elapsed, 3),
        "segments": out_segments,
        "words": words,
        "text": "".join(s["text"] for s in out_segments).strip(),
    }


# --------------------------------------------------------------------------
# whisperx: transcription + forced alignment
# --------------------------------------------------------------------------
def run_whisperx(wav: str, model_cache: dict, align_cache: dict) -> dict:
    import whisperx

    key = (BASELINE_MODEL, BASELINE_DEVICE, BASELINE_COMPUTE)
    if ("wx",) + key not in model_cache:
        model_cache[("wx",) + key] = whisperx.load_model(
            BASELINE_MODEL, BASELINE_DEVICE, compute_type=BASELINE_COMPUTE)
    model = model_cache[("wx",) + key]

    audio = whisperx.load_audio(wav)

    t0 = time.time()
    result = model.transcribe(audio, batch_size=8)
    transcribe_s = time.time() - t0

    language = result.get("language", "en")
    # Segment bounds BEFORE alignment (the transcribed/VAD candidate window).
    # Comparing these with the aligned word bounds is what answers "semantic
    # candidate vs aligned spoken thought".
    pre_align_segments = [{"start": float(s["start"]), "end": float(s["end"]),
                           "text": s.get("text", "")}
                          for s in result.get("segments", [])]
    aligned = {"segments": []}
    align_s = 0.0
    align_error = ""
    try:
        if language not in align_cache:
            align_cache[language] = whisperx.load_align_model(
                language_code=language, device=BASELINE_DEVICE)
        align_model, align_meta = align_cache[language]
        t1 = time.time()
        aligned = whisperx.align(result["segments"], align_model, align_meta,
                                 audio, BASELINE_DEVICE,
                                 return_char_alignments=False)
        align_s = time.time() - t1
    except Exception as exc:                      # surfaced, never hidden
        align_error = f"{type(exc).__name__}: {exc}"

    words = []
    for s in aligned.get("segments", []):
        for w in (s.get("words") or []):
            if "start" not in w or "end" not in w:
                continue
            words.append({"word": w.get("word", ""), "start": float(w["start"]),
                          "end": float(w["end"]), "score": w.get("score")})

    # WhisperX reports which words it could not align (absent start/end).
    unaligned = []
    for s in aligned.get("segments", []):
        for w in (s.get("words") or []):
            if "start" not in w or "end" not in w:
                unaligned.append(w.get("word", ""))

    return {
        "backend": f"whisperx {_ver('whisperx')} + torch {_ver('torch')}",
        "language": language,
        "elapsed_s": round(transcribe_s + align_s, 3),
        "transcribe_s": round(transcribe_s, 3),
        "align_s": round(align_s, 3),
        "align_error": align_error,
        "pre_align_segments": pre_align_segments,
        "segments": aligned.get("segments", []),
        "words": words,
        "unaligned_words": unaligned,
        "text": " ".join(s.get("text", "").strip() for s in aligned.get("segments", [])).strip(),
    }


def _ver(pkg: str) -> str:
    import importlib.metadata as md
    try:
        return md.version(pkg)
    except Exception:
        return "?"


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------
def norm(tok: str) -> str:
    return re.sub(r"[^a-z0-9']", "", tok.lower())


def match_words(base_words: list, wx_words: list) -> list[dict]:
    """Pair baseline words with WhisperX words by text sequence, so timestamp
    deltas are measured on the SAME words rather than on list positions."""
    a = [norm(w["word"]) for w in base_words]
    b = [norm(w["word"]) for w in wx_words]
    pairs = []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                bw, ww = base_words[i1 + k], wx_words[j1 + k]
                pairs.append({
                    "word": bw["word"].strip(),
                    "base_start": round(bw["start"], 3), "base_end": round(bw["end"], 3),
                    "wx_start": round(ww["start"], 3), "wx_end": round(ww["end"], 3),
                    "d_start": round(ww["start"] - bw["start"], 3),
                    "d_end": round(ww["end"] - bw["end"], 3),
                })
    return pairs


def gaps(words: list) -> list[float]:
    """Silence between consecutive aligned words — the pause evidence."""
    out = []
    for a, b in zip(words, words[1:]):
        g = b["start"] - a["end"]
        if g > 0:
            out.append(round(g, 3))
    return out


def boundary_refinement(pre_align: list, aligned_segments: list,
                        pad: float = 0.75) -> list[dict]:
    """The 'semantic candidate vs aligned spoken thought' measurement.

    For each PRE-ALIGNMENT segment (what the pipeline would hand a cutter),
    find the aligned words that actually fall inside it and report how far the
    spoken span sits from the segment edges — i.e. how much dead air/music sits
    inside the candidate window.

    Words are associated BY TIME, never by index: ``whisperx.align()`` does not
    return the same number of segments it was given, so index pairing silently
    compares unrelated spans.
    """
    words = [w for seg in aligned_segments for w in (seg.get("words") or [])
             if isinstance(w, dict) and "start" in w and "end" in w]
    rows = []
    for s in pre_align:
        inside = [w for w in words
                  if w["start"] >= s["start"] - pad and w["end"] <= s["end"] + pad]
        if not inside:
            rows.append({"pre_align": f"{s['start']:.2f}-{s['end']:.2f}",
                         "spoken": None, "lead_trim_s": None, "tail_trim_s": None,
                         "words": 0})
            continue
        first = min(w["start"] for w in inside)
        last = max(w["end"] for w in inside)
        rows.append({
            "pre_align": f"{s['start']:.2f}-{s['end']:.2f}",
            "spoken": f"{first:.2f}-{last:.2f}",
            "lead_trim_s": round(first - s["start"], 3),
            "tail_trim_s": round(s["end"] - last, 3),
            "words": len(inside),
        })
    return rows


def summarize(clip: dict) -> dict:
    base, wx = clip["baseline"], clip["whisperx"]
    dur = clip["duration_s"]
    pairs = match_words(base["words"], wx["words"]) if base["words"] and wx["words"] else []
    d_start = [abs(p["d_start"]) for p in pairs]
    d_end = [abs(p["d_end"]) for p in pairs]
    wx_words = [w for w in wx["words"] if w["end"] > w["start"]]

    def stats(v):
        if not v:
            return None
        v = sorted(v)
        return {"median": round(v[len(v) // 2], 3), "max": round(v[-1], 3),
                "mean": round(sum(v) / len(v), 3)}

    rows = boundary_refinement(wx.get("pre_align_segments", []), wx["segments"])
    got = [r for r in rows if r["spoken"]]
    leads = [r["lead_trim_s"] for r in got]
    tails = [r["tail_trim_s"] for r in got]

    return {
        "clip": clip["name"],
        "case": clip.get("case", ""),
        "duration_s": round(dur, 2),
        "baseline_model": base["backend"],
        "whisperx_model": wx["backend"],
        "baseline_words": len(base["words"]),
        "whisperx_words": len(wx["words"]),
        "matched_words": len(pairs),
        "baseline_words_missing_from_whisperx": max(0, len(base["words"]) - len(pairs)),
        "whisperx_words_absent_from_baseline": max(0, len(wx["words"]) - len(pairs)),
        "whisperx_unaligned_words": len(wx.get("unaligned_words", [])),
        "align_error": wx.get("align_error", ""),
        "baseline_elapsed_s": base["elapsed_s"],
        "whisperx_transcribe_s": wx["transcribe_s"],
        "whisperx_align_s": wx["align_s"],
        "whisperx_total_s": wx["elapsed_s"],
        "baseline_rtf": round(base["elapsed_s"] / dur, 3) if dur else None,
        "whisperx_rtf": round(wx["elapsed_s"] / dur, 3) if dur else None,
        "align_rtf": round(wx["align_s"] / dur, 3) if dur else None,
        "word_start_delta_s": stats(d_start),
        "word_end_delta_s": stats(d_end),
        "baseline_pauses_ge_0.35s": len([g for g in gaps(base["words"]) if g >= PAUSE_MIN_S]),
        "whisperx_pauses_ge_0.35s": len([g for g in gaps(wx_words) if g >= PAUSE_MIN_S]),
        "whisperx_largest_gap_s": max(gaps(wx_words)) if gaps(wx_words) else 0.0,
        "boundary_refinement": rows,
        "boundary_segments_total": len(rows),
        "boundary_segments_with_words": len(got),
        "boundary_lead_trim_mean_s": round(sum(leads) / len(leads), 3) if leads else None,
        "boundary_lead_trim_max_s": round(max(leads), 3) if leads else None,
        "boundary_tail_trim_mean_s": round(sum(tails) / len(tails), 3) if tails else None,
        "boundary_tail_trim_max_s": round(max(tails), 3) if tails else None,
    }


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("media", nargs="+", help="media files to evaluate")
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--audio-dir", default=None,
                    help="keep the 16k mono wavs here (default: temp)")
    ap.add_argument("--cases", default="",
                    help="optional comma-separated case labels matching media order")
    args = ap.parse_args()

    labels = [c.strip() for c in args.cases.split(",")] if args.cases else []
    model_cache, align_cache = {}, {}
    clips, summaries = [], []

    tmpdir = args.audio_dir or tempfile.mkdtemp(prefix="loom-5c1-audio-")
    Path(tmpdir).mkdir(parents=True, exist_ok=True)

    for i, media in enumerate(args.media):
        name = Path(media).name
        case = labels[i] if i < len(labels) else ""
        print(f"\n=== [{i + 1}/{len(args.media)}] {name}\n    case: {case}", flush=True)
        wav = str(Path(tmpdir) / f"{i:02d}_{norm(Path(media).stem)[:40]}.wav")
        to_wav16k(media, wav)
        dur = probe_duration(wav)
        print(f"    {dur:.2f}s audio @16k mono", flush=True)

        print("    baseline: faster-whisper (LOOM params)...", flush=True)
        base = run_baseline(wav, model_cache)
        print(f"      -> {len(base['words'])} words in {base['elapsed_s']}s "
              f"(rtf {base['elapsed_s'] / dur:.2f})", flush=True)

        print("    whisperx: transcribe + wav2vec2 align...", flush=True)
        wx = run_whisperx(wav, model_cache, align_cache)
        note = f" ALIGN ERROR: {wx['align_error']}" if wx["align_error"] else ""
        print(f"      -> {len(wx['words'])} aligned words, "
              f"transcribe {wx['transcribe_s']}s + align {wx['align_s']}s{note}", flush=True)

        clip = {"name": name, "media": str(media), "case": case,
                "duration_s": dur, "baseline": base, "whisperx": wx}
        clips.append(clip)
        s = summarize(clip)
        summaries.append(s)
        print(f"      deltas vs baseline: start {s['word_start_delta_s']} "
              f"end {s['word_end_delta_s']}", flush=True)

    env = {
        "python": sys.version.split()[0],
        "whisperx": _ver("whisperx"), "faster-whisper": _ver("faster-whisper"),
        "ctranslate2": _ver("ctranslate2"), "torch": _ver("torch"),
        "torchaudio": _ver("torchaudio"), "transformers": _ver("transformers"),
        "numpy": _ver("numpy"),
        "device": BASELINE_DEVICE, "compute_type": BASELINE_COMPUTE,
        "asr_model": BASELINE_MODEL,
    }
    payload = {"environment": env, "summaries": summaries, "clips": clips}
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}", flush=True)

    print("\n=== SUMMARY ===")
    for s in summaries:
        print(f"  {s['clip'][:52]:52s} words {s['baseline_words']:3d}->{s['whisperx_words']:3d} "
              f"| rtf {s['baseline_rtf']}->{s['whisperx_rtf']} "
              f"| align {s['whisperx_align_s']}s "
              f"| pauses {s['baseline_pauses_ge_0.35s']}->{s['whisperx_pauses_ge_0.35s']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
