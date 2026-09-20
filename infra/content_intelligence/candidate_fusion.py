"""Deterministic candidate-window math: normalization, peak detection, window
generation, and overlap fusion/dedup with provenance preservation.

Pure stdlib; no model call, no media I/O. Salience is RELATIVE — every signal is
normalized as percentile-rank within its own source only, never presented as an
absolute or global claim of importance, and never labelled "viral".

The candidate-window contract (shared by every origin):

    transcript origin   {start_s, end_s, basis?, source: "transcript"}
    signal origin       {start_s, end_s, peak_s, basis, source: "signal_peak"}

After fusion, provenance is preserved without displacing transcript windows:

    * a transcript window overlapped by a signal window keeps its ``source``,
      gains ``origin: "hybrid"``, and records the signal basis in ``supported_by``;
    * a signal window that overlaps nothing is kept as ``origin: "signal"`` so
      audio/visual salience can still surface where the transcript is weak;
    * signal windows that overlap EACH OTHER collapse into one window that
      records every contributing basis in ``supported_by``, so a single salient
      moment cannot enter the candidate list several times over.

Fusion is order-preserving and deterministic: the earliest window's bounds and
peak survive a merge, and only its ``supported_by`` grows. Signal windows are
never allowed to displace a transcript window — the transcript is the semantic
anchor.
"""

from __future__ import annotations

from typing import Any


def normalize_signal(signal: list[float], method: str = "percentile") -> list[float]:
    """Normalize a signal to 0..1 WITHIN its own source.

    ``percentile`` (default) ranks each sample against the whole source so
    salience is relative to this media only. ``minmax`` scales to the observed
    range. A constant (flat) signal is all-zero — no fake salience is invented.
    """
    if not signal:
        return []
    lo, hi = min(signal), max(signal)
    if hi == lo:
        return [0.0] * len(signal)

    if method == "percentile":
        # Percentile rank by value; equal values share the rank of their first
        # occurrence, so a flat plateau never registers as a peak.
        order = sorted(enumerate(signal), key=lambda p: p[1])
        ranks = [0.0] * len(signal)
        n = len(signal) - 1
        cur_val = order[0][1]
        cur_rank = 0.0
        for i, (idx, val) in enumerate(order):
            if val > cur_val:
                cur_val = val
                cur_rank = i / n
            ranks[idx] = cur_rank
        return ranks

    if method == "minmax":
        return [(s - lo) / (hi - lo) for s in signal]

    return list(signal)


def gate_by_magnitude(
    normalized: list[float],
    raw: list[float],
    floor: float,
) -> list[float]:
    """Zero the normalized ranks whose RAW value fails ``floor``.

    Percentile ranking alone can never return "nothing salient": the largest
    sample of any non-constant signal ranks 1.0, so peak detection would report
    a candidate moment for a perfectly steady source whose only variation is
    float noise. Measured on this machine, a constant tone varies by ~0.1 RMS
    out of ~145 — meaningless, yet ranked 0..1 and duly reported as a peak.

    A floor therefore has to clear the raw signal too. Callers express it
    relative to the source's own level (a fraction of its median loudness, or
    gray levels of the source's own frame differences), so this stays a
    within-source relative judgement: it removes noise, it does not impose an
    external standard of importance.
    """
    return [n if r >= floor else 0.0 for n, r in zip(normalized, raw)]


def find_peaks(signal: list[float], threshold: float, min_distance: int) -> list[int]:
    """Indices of local maxima above ``threshold``, at least ``min_distance`` apart.

    When two qualifying peaks are closer than ``min_distance``, the higher one
    wins; equal peaks keep the first.
    """
    peaks: list[int] = []
    if not signal:
        return peaks
    for i in range(1, len(signal) - 1):
        if signal[i] > threshold and signal[i] > signal[i - 1] and signal[i] >= signal[i + 1]:
            if not peaks or (i - peaks[-1]) >= min_distance:
                peaks.append(i)
            elif signal[i] > signal[peaks[-1]]:
                peaks[-1] = i
    return peaks


def generate_signal_windows(
    peaks: list[int],
    basis: str,
    window_before: int = 5,
    window_after: int = 15,
    max_len: int = 0,
) -> list[dict[str, Any]]:
    """Convert peak seconds into candidate windows with provenance.

    Each window records its origin basis and is tagged ``source: "signal_peak"``
    so fusion can preserve provenance. ``max_len`` caps ``end_s`` at the media
    duration (0 = no cap).
    """
    windows: list[dict[str, Any]] = []
    for p in peaks:
        start = max(0, p - window_before)
        end = p + window_after
        if max_len > 0:
            end = min(end, max_len)
        windows.append({
            "start_s": float(start),
            "end_s": float(end),
            "peak_s": float(p),
            "basis": basis,
            "source": "signal_peak",
        })
    return windows


def fuse_candidates(
    transcript_windows: list[dict[str, Any]],
    signal_windows: list[dict[str, Any]],
    overlap_threshold: float = 0.5,
) -> list[dict[str, Any]]:
    """Fuse transcript + signal candidate windows, deduping overlaps.

    Each signal window is resolved against the windows already fused, in this
    precedence order:

    1. an overlapping TRANSCRIPT window wins — it is annotated ``origin:
       "hybrid"`` and gains the signal basis in ``supported_by`` (the signal
       window itself is consumed, never appended);
    2. otherwise an overlapping SIGNAL window wins — the basis is merged into
       its ``supported_by`` and the duplicate window is consumed, so one moment
       is reported once no matter how many signals fired on it;
    3. otherwise the signal window is kept standalone as ``origin: "signal"``
       so audio/visual salience survives where the transcript is weak.
    """
    fused: list[dict[str, Any]] = []
    for tw in transcript_windows:
        base = dict(tw)
        base.setdefault("source", "transcript")
        base.setdefault("origin", "transcript")
        base.setdefault("supported_by", [])
        fused.append(base)

    def overlap_ratio(w1: dict[str, Any], w2: dict[str, Any]) -> float:
        s1, e1 = w1["start_s"], w1["end_s"]
        s2, e2 = w2["start_s"], w2["end_s"]
        overlap = max(0.0, min(e1, e2) - max(s1, s2))
        span = min(max(0.1, e1 - s1), max(0.1, e2 - s2))
        return overlap / span

    def absorb(target: dict[str, Any], basis: str) -> None:
        if basis not in target["supported_by"]:
            target["supported_by"].append(basis)

    for sw in signal_windows:
        basis = sw.get("basis", "signal_peak")

        transcript_hit = None
        signal_hit = None
        for base in fused:
            if overlap_ratio(base, sw) < overlap_threshold:
                continue
            if base["source"] == "transcript":
                transcript_hit = base
                break
            if signal_hit is None:
                signal_hit = base

        if transcript_hit is not None:
            transcript_hit["origin"] = "hybrid"
            absorb(transcript_hit, basis)
            continue

        if signal_hit is not None:
            absorb(signal_hit, basis)
            continue

        sig = dict(sw)
        sig.setdefault("source", "signal_peak")
        sig.setdefault("origin", "signal")
        sig["supported_by"] = [basis]
        fused.append(sig)

    return sorted(fused, key=lambda w: w["start_s"])
