#!/usr/bin/env python3
"""Segment 5C.1-follow — DeepFilterNet bounded A/B harness.

Answers ONE narrow question: does local DeepFilterNet cleanup materially improve
LOOM's EXISTING transcription (and the approved WhisperX alignment-only step) on
difficult audio enough to justify adding a preprocessing stage?

Everything except the audio cleanup is held constant:

    A  original 16k mono wav -> LOOM transcription config -> WhisperX align-only
    B  DeepFilterNet-cleaned  -> SAME configuration       -> SAME align step

Cleanup runs in a SEPARATE venv (`~/.venvs/loom-deepfilter-eval`) because
DeepFilterNet pins numpy<2 while the WhisperX evaluation venv is pinned to the
exact versions that produced the committed 5C.1 `results.json`. This process must
be run by the WhisperX venv:

    ~/.venvs/loom-whisperx-eval/bin/python evaluate_ab.py --out results_ab.json <media...>

Cost: $0 — local CPU only, no hosted or paid audio/transcription service.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Reuse the 5C.1 harness so the baseline configuration and the alignment step are
# literally the same code, not a re-implementation.
_WX = Path(__file__).resolve().parents[1] / "whisperx-eval"
sys.path.insert(0, str(_WX))
import evaluate as W  # noqa: E402

DFN_PY = os.environ.get(
    "DFN_PY", str(Path.home() / ".venvs" / "loom-deepfilter-eval" / "bin" / "python"))
CLEANER = Path(__file__).with_name("clean_audio.py")


def run_cleanup(src_wav: str, dst_wav: str) -> dict:
    """Shell out to the DeepFilterNet venv. Returns timing + preservation stats."""
    t0 = time.time()
    proc = subprocess.run([DFN_PY, str(CLEANER), src_wav, dst_wav],
                          capture_output=True, text=True)
    wall = time.time() - t0
    stats: dict = {}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            stats = json.loads(line)
            break
        except Exception:
            continue
    return {"wall_s": round(wall, 3), "returncode": proc.returncode,
            "stderr_tail": (proc.stderr or "")[-500:], **stats}


def word_text(words: list) -> list[str]:
    return [W.norm(w["word"]) for w in words]


def diff_tokens(a: list[str], b: list[str]) -> dict:
    """Recovered / lost / substituted tokens between two transcripts."""
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    recovered, lost, subs = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            recovered.extend(b[j1:j2])
        elif tag == "delete":
            lost.extend(a[i1:i2])
        elif tag == "replace":
            lost.extend(a[i1:i2])
            recovered.extend(b[j1:j2])
            subs.append({"from": " ".join(a[i1:i2])[:60], "to": " ".join(b[j1:j2])[:60]})
    ratio = sm.ratio()
    return {"recovered": recovered, "lost": lost, "substitutions": subs,
            "similarity_ratio": round(ratio, 4)}


def aligned_deltas(a_words: list, b_words: list) -> dict:
    """Timestamp movement between the two runs, on words present in both."""
    pairs = W.match_words(a_words, b_words)
    if not pairs:
        return {"matched": 0}
    ds = sorted(abs(p["d_start"]) for p in pairs)
    de = sorted(abs(p["d_end"]) for p in pairs)
    return {"matched": len(pairs),
            "d_start_median": round(ds[len(ds) // 2], 3),
            "d_start_max": round(ds[-1], 3),
            "d_end_median": round(de[len(de) // 2], 3),
            "d_end_max": round(de[-1], 3)}


def boundary_stats(rows: list) -> dict:
    got = [r for r in rows if r["spoken"]]
    if not got:
        return {"segments": len(rows), "with_words": 0}
    leads = [r["lead_trim_s"] for r in got]
    tails = [r["tail_trim_s"] for r in got]
    return {"segments": len(rows), "with_words": len(got),
            "lead_trim_mean_s": round(sum(leads) / len(leads), 3),
            "lead_trim_max_s": round(max(leads), 3),
            "tail_trim_mean_s": round(sum(tails) / len(tails), 3),
            "tail_trim_max_s": round(max(tails), 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("media", nargs="+")
    ap.add_argument("--out", default="results_ab.json")
    ap.add_argument("--audio-dir", default=None)
    ap.add_argument("--cases", default="")
    args = ap.parse_args()

    labels = [c.strip() for c in args.cases.split(",")] if args.cases else []
    tmpdir = Path(args.audio_dir or tempfile.mkdtemp(prefix="loom-dfn-ab-"))
    tmpdir.mkdir(parents=True, exist_ok=True)

    base_models: dict = {}
    align_models: dict = {}
    clips, summaries = [], []

    for i, media in enumerate(args.media):
        case = labels[i] if i < len(labels) else ""
        name = Path(media).name
        print(f"\n=== [{i + 1}/{len(args.media)}] {name}\n    case: {case}", flush=True)

        orig = str(tmpdir / f"{i:02d}_orig.wav")
        clean = str(tmpdir / f"{i:02d}_clean.wav")
        W.to_wav16k(media, orig)
        dur = W.probe_duration(orig)
        print(f"    {dur:.2f}s audio @16k mono", flush=True)

        print("    cleanup: DeepFilterNet (separate venv)...", flush=True)
        cl = run_cleanup(orig, clean)
        if cl["returncode"] != 0:
            print(f"      -> CLEANUP FAILED rc={cl['returncode']}: {cl['stderr_tail'][-200:]}", flush=True)
            clips.append({"name": name, "case": case, "duration_s": dur,
                          "cleanup": cl, "error": "cleanup_failed"})
            continue
        print(f"      -> {cl['cleanup_total_s']}s (enhance {cl.get('enhance_s')}s) "
              f"rtf {cl['cleanup_total_s'] / dur:.3f} | rms_ratio {cl.get('rms_ratio')} "
              f"loud_region_ratio {cl.get('loud_region_ratio')}", flush=True)

        print("    A: original -> LOOM transcription + whisperx align...", flush=True)
        a_base = W.run_baseline(orig, base_models)
        a_wx = W.run_whisperx(orig, base_models, align_models)

        print("    B: cleaned  -> same configuration...", flush=True)
        b_base = W.run_baseline(clean, base_models)
        b_wx = W.run_whisperx(clean, base_models, align_models)

        a_txt, b_txt = word_text(a_base["words"]), word_text(b_base["words"])
        d = diff_tokens(a_txt, b_txt)
        a_rows = W.boundary_refinement(a_wx.get("pre_align_segments", []), a_wx["segments"])
        b_rows = W.boundary_refinement(b_wx.get("pre_align_segments", []), b_wx["segments"])

        s = {
            "clip": name, "case": case, "duration_s": round(dur, 2),
            "A_words": len(a_base["words"]), "B_words": len(b_base["words"]),
            "word_delta": len(b_base["words"]) - len(a_base["words"]),
            "recovered_words": d["recovered"], "lost_words": d["lost"],
            "substitutions": d["substitutions"], "similarity_ratio": d["similarity_ratio"],
            "A_text": a_base["text"], "B_text": b_base["text"],
            "A_align_ok": not a_wx["align_error"], "B_align_ok": not b_wx["align_error"],
            "A_align_error": a_wx["align_error"], "B_align_error": b_wx["align_error"],
            "A_aligned_words": len(a_wx["words"]), "B_aligned_words": len(b_wx["words"]),
            "A_unaligned_words": len(a_wx["unaligned_words"]),
            "B_unaligned_words": len(b_wx["unaligned_words"]),
            "timestamp_movement": aligned_deltas(a_wx["words"], b_wx["words"]),
            "A_boundary": boundary_stats(a_rows), "B_boundary": boundary_stats(b_rows),
            "cleanup": cl,
            "A_transcribe_s": a_base["elapsed_s"], "A_align_s": a_wx["align_s"],
            "B_transcribe_s": b_base["elapsed_s"], "B_align_s": b_wx["align_s"],
            "A_total_s": round(a_base["elapsed_s"] + a_wx["elapsed_s"], 3),
            "B_total_s": round(b_base["elapsed_s"] + b_wx["elapsed_s"], 3),
            "cleanup_rtf": round(cl["cleanup_total_s"] / dur, 4) if dur else None,
            "combined_overhead_s": round(cl["cleanup_total_s"] + b_base["elapsed_s"]
                                         + b_wx["elapsed_s"]
                                         - (a_base["elapsed_s"] + a_wx["elapsed_s"]), 3),
        }
        clips.append({"name": name, "case": case, "duration_s": dur,
                      "cleanup": cl, "A": {"baseline": a_base, "whisperx": a_wx},
                      "B": {"baseline": b_base, "whisperx": b_wx}})
        summaries.append(s)
        print(f"      words A={s['A_words']} -> B={s['B_words']} "
              f"(+{len(d['recovered'])}/-{len(d['lost'])}, sim {d['similarity_ratio']})", flush=True)

    env = {
        "python": sys.version.split()[0],
        "whisperx": W._ver("whisperx"), "faster-whisper": W._ver("faster-whisper"),
        "ctranslate2": W._ver("ctranslate2"), "torch": W._ver("torch"),
        "asr_model": W.BASELINE_MODEL, "device": W.BASELINE_DEVICE,
        "compute_type": W.BASELINE_COMPUTE,
        "dfn_venv": DFN_PY,
        "dfn_version": _dfn_version(),
        "dfn_deps": _dfn_deps(),
    }
    Path(args.out).write_text(json.dumps({"environment": env, "summaries": summaries,
                                          "clips": clips}, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")

    print("\n=== A/B SUMMARY ===")
    for s in summaries:
        print(f"  {s['case'][:26]:<28} words {s['A_words']:>4}->{s['B_words']:<4} "
              f"(-{len(s['lost_words'])}/+{len(s['recovered_words'])}) "
              f"sim {s['similarity_ratio']:.2f} | clean rtf {s['cleanup_rtf']} "
              f"| overhead +{s['combined_overhead_s']}s "
              f"| align ok {s['A_align_ok']}->{s['B_align_ok']}")
    return 0


def _dfn_version() -> str:
    out = subprocess.run([DFN_PY, "-c",
                          "import importlib.metadata as m;print(m.version('deepfilternet'))"],
                         capture_output=True, text=True)
    return out.stdout.strip() or "?"


def _dfn_deps() -> dict:
    code = ("import importlib.metadata as m\n"
            "for p in ('deepfilternet','DeepFilterLib','torch','torchaudio','numpy'):\n"
            "    try: print(p+'=='+m.version(p))\n"
            "    except Exception: pass\n")
    out = subprocess.run([DFN_PY, "-c", code], capture_output=True, text=True)
    return dict(line.split("==", 1) for line in out.stdout.strip().splitlines() if "==" in line)


if __name__ == "__main__":
    raise SystemExit(main())
