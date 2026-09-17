"""Bounded media probe — Pass 2 contract (deterministic lifecycle; fetch deferred).

Defines the TEMPORARY, BOUNDED media-inspection lifecycle. The actual media
fetch is injected (``fetch_window``) so the contract can be built + tested with
zero network / media access. In production, ``fetch_window`` would be a
compliant, platform-appropriate bounded range fetch — NOT a full-source
download. This module NEVER persists media, NEVER downloads a complete source,
and NEVER triggers Acquisition.

Guarantees:
  * hard limits (candidates / windows / seconds / bytes) are enforced;
  * transient probe media is written to a temp dir and DELETED after analysis;
  * only evidence metadata is preserved (never the media itself);
  * a failed probe returns ``ok=False`` and never becomes Acquisition.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable

from .provider import ProbePolicy


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    inspected_windows: list[dict[str, Any]]
    total_seconds: float
    total_bytes: int
    temp_files_deleted: bool
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ProbeLimitError(RuntimeError):
    pass


class BoundedMediaProbe:
    """Deterministic, bounded media-inspection contract."""

    def __init__(
        self,
        policy: ProbePolicy,
        fetch_window: Callable[[float, float], bytes],
    ) -> None:
        self.policy = policy
        self.fetch_window = fetch_window

    def probe(self, windows: list[dict[str, Any]]) -> ProbeResult:
        """Inspect bounded windows; return metadata only. Transient media is
        deleted before returning."""
        # Enforce window-count and per-window duration limits.
        if len(windows) > self.policy.max_windows_per_candidate:
            return ProbeResult(
                ok=False, inspected_windows=[], total_seconds=0.0, total_bytes=0,
                temp_files_deleted=False,
                error=f"too many windows ({len(windows)} > {self.policy.max_windows_per_candidate})",
            )

        total_seconds = 0.0
        normalized: list[dict[str, Any]] = []
        for w in windows:
            start = float(w.get("start_s", 0))
            end = float(w.get("end_s", start))
            dur = end - start
            if dur <= 0 or dur > self.policy.max_seconds_per_window:
                return ProbeResult(
                    ok=False, inspected_windows=[], total_seconds=0.0, total_bytes=0,
                    temp_files_deleted=False,
                    error=f"window duration {dur}s out of policy (max {self.policy.max_seconds_per_window}s)",
                )
            total_seconds += dur
            normalized.append({"start_s": start, "end_s": end})

        if total_seconds > self.policy.max_total_media_seconds:
            return ProbeResult(
                ok=False, inspected_windows=[], total_seconds=total_seconds, total_bytes=0,
                temp_files_deleted=False,
                error=f"total media duration {total_seconds}s exceeds {self.policy.max_total_media_seconds}s",
            )

        total_bytes = 0
        metadata: list[dict[str, Any]] = []
        temp_deleted = False
        # Temp dir is created and DELETED on exit (deterministic cleanup).
        try:
            with tempfile.TemporaryDirectory(prefix="loom-probe-") as tmp:
                for w in normalized:
                    data = self.fetch_window(w["start_s"], w["end_s"])
                    if not isinstance(data, (bytes, bytearray)):
                        return ProbeResult(
                            ok=False, inspected_windows=[], total_seconds=total_seconds,
                            total_bytes=0, temp_files_deleted=False,
                            error="fetch_window did not return bytes",
                        )
                    total_bytes += len(data)
                    if total_bytes > self.policy.max_total_bytes:
                        return ProbeResult(
                            ok=False, inspected_windows=[], total_seconds=total_seconds,
                            total_bytes=total_bytes, temp_files_deleted=False,
                            error=f"total bytes {total_bytes} exceeds {self.policy.max_total_bytes}",
                        )
                    # Write transient file (simulating inspection) then continue.
                    fpath = os.path.join(tmp, f"probe-{int(w['start_s'])}-{int(w['end_s'])}.bin")
                    with open(fpath, "wb") as fh:
                        fh.write(data)
                    metadata.append({
                        "start_s": w["start_s"], "end_s": w["end_s"],
                        "bytes": len(data),
                    })
            temp_deleted = True
        except ProbeLimitError:
            raise
        except Exception as e:  # fetch failure -> ok=False, never Acquisition
            return ProbeResult(
                ok=False, inspected_windows=[], total_seconds=total_seconds,
                total_bytes=total_bytes, temp_files_deleted=temp_deleted,
                error=f"probe failed: {e}",
            )

        return ProbeResult(
            ok=True,
            inspected_windows=normalized,
            total_seconds=total_seconds,
            total_bytes=total_bytes,
            temp_files_deleted=temp_deleted,
            metadata={"windows": metadata},
        )
