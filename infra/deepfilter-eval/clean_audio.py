#!/usr/bin/env python3
"""DeepFilterNet cleanup stage — runs INSIDE the DeepFilterNet venv only.

Reads a 16 kHz mono wav, writes the cleaned wav, and prints a JSON stats line
that the A/B harness consumes. Kept in a separate process so the DeepFilterNet
dependency (which pins numpy<2) never touches the WhisperX evaluation venv.

I/O deliberately avoids torchaudio: torchaudio 2.8 requires torchcodec for
decoding, and torchcodec cannot load against current Homebrew ffmpeg
(`libavutil` mismatch). Decoding/resampling is done with the ffmpeg binary plus
the stdlib `wave` module instead, so this stage has no fragile audio backend.

Usage:  <dfn-venv>/bin/python clean_audio.py <in.wav> <out.wav>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
import wave
from pathlib import Path


def _to_48k_mono_wav(src: str, dst: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1",
         "-ar", "48000", "-c:a", "pcm_s16le", dst],
        check=True, capture_output=True,
    )


def _read_wav(path: str):
    """(torch tensor shaped (1, N), sample_rate) via stdlib wave — no torchaudio."""
    import numpy as np
    import torch
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise RuntimeError(f"expected 16-bit PCM, got {w.getsampwidth() * 8}-bit")
        ch, sr, n = w.getnchannels(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return torch.from_numpy(a).unsqueeze(0), sr


def _write_wav(path: str, tensor, sr: int) -> None:
    import numpy as np
    a = tensor.detach().cpu().numpy()
    if a.ndim > 1:
        a = a.mean(axis=0)
    a = np.clip(a, -1.0, 1.0)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes((a * 32767.0).astype("<i2").tobytes())


def _rms(x) -> float:
    import numpy as np
    a = np.asarray(x, dtype="float64").reshape(-1)
    return float(np.sqrt(np.mean(a ** 2))) if a.size else 0.0


def _loud_region_rms(x, frac: float = 0.10) -> float:
    """RMS of the loudest `frac` of 20 ms frames.

    Content-preservation guardrail: if shouting/laughter/singing is being
    smoothed away, the loud regions lose energy relative to the quiet ones even
    when the file still sounds 'cleaner'.
    """
    import numpy as np
    a = np.asarray(x, dtype="float64").reshape(-1)
    if a.size < 960:
        return _rms(a)
    frames = a[: (a.size // 960) * 960].reshape(-1, 960)
    energies = np.sqrt(np.mean(frames ** 2, axis=1))
    k = max(1, int(len(energies) * frac))
    return float(np.mean(np.sort(energies)[-k:]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    args = ap.parse_args()

    t0 = time.time()
    import torch
    from df.enhance import enhance, init_df

    model, df_state, _suffix = init_df()
    model.eval()
    target_sr = int(df_state.sr())
    t_init = time.time() - t0

    with tempfile.TemporaryDirectory() as td:
        native = str(Path(td) / "native48k.wav")
        _to_48k_mono_wav(args.src, native)
        audio, sr = _read_wav(native)

    t1 = time.time()
    with torch.no_grad():
        enhanced = enhance(model, df_state, audio)
    t_enhance = time.time() - t1

    _write_wav(args.dst, enhanced, sr)

    raw = audio.numpy().reshape(-1)
    out = enhanced.detach().cpu().numpy().reshape(-1)
    in_rms, out_rms = _rms(raw), _rms(out)
    in_loud, out_loud = _loud_region_rms(raw), _loud_region_rms(out)

    print(json.dumps({
        "ok": True,
        "model_init_s": round(t_init, 3),
        "enhance_s": round(t_enhance, 3),
        "cleanup_total_s": round(time.time() - t0, 3),
        "native_sr": sr,
        "dfn_expected_sr": target_sr,
        "in_samples": int(raw.size),
        "out_samples": int(out.size),
        "in_rms": round(in_rms, 6),
        "out_rms": round(out_rms, 6),
        "rms_ratio": round(out_rms / in_rms, 4) if in_rms else None,
        "in_loud_rms": round(in_loud, 6),
        "out_loud_rms": round(out_loud, 6),
        "loud_region_ratio": round(out_loud / in_loud, 4) if in_loud else None,
        "in_peak": round(float(abs(raw).max()), 6) if raw.size else 0.0,
        "out_peak": round(float(abs(out).max()), 6) if out.size else 0.0,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
