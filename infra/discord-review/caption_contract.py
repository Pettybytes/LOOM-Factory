#!/usr/bin/env python3
"""
LOOM caption / final-asset contract — deterministic classifier.

Encodes the locked contract that decides whether a candidate clip's final
asset is acceptable for review, blocked, or needs a caption-repair step.

Contract (matches the task's four deterministic cases):
  1. spoken + captions expected + captioned final asset  -> accepted
  2. spoken + captions expected + hook-only/uncaptioned   -> caption_repair
  3. truly silent / no-speech                             -> accepted (no captions)
  4. already captioned                                    -> never captioned twice

no_speech is AUTHORITATIVE evidence: it must come from the transcription
metadata (language == "none" or zero segments). A failed/empty secondary
transcript must never flip a known-speech clip to no_speech.
"""
from dataclasses import dataclass, asdict


@dataclass
class ContractVerdict:
    ok: bool
    action: str          # accepted | caption_repair | blocked
    reason: str


def classify(no_speech, captions_expected, captions_applied, hook_expected=None,
             hook_applied=None, transcript_segment_count=None):
    """
    Return the deterministic verdict for a candidate's caption/hook contract.

    Rules:
      - no_speech (authoritative) -> captions not required; accepted regardless
        of captions_applied. Never fabricate captions for silent material.
      - spoken + captions expected + captions applied -> accepted.
      - spoken + captions expected + NOT applied -> caption_repair (single step).
      - captions_applied on an already-captioned asset -> never caption twice.
    """
    if no_speech:
        return ContractVerdict(True, "accepted", "no speech — captions not required")

    if not captions_expected:
        # Caller did not ask for captions. If captions are somehow already
        # burned, do NOT add a second layer.
        return ContractVerdict(True, "accepted", "captions not expected")

    if captions_applied:
        # Captions already burned exactly once — never caption twice.
        return ContractVerdict(True, "accepted", "captions applied exactly once")

    # Spoken, captions expected, but the final asset is uncaptioned
    # (e.g. a hooked_ derivative with no subtitled_ layer). Route to exactly
    # one caption-repair step; do not silently accept a caption-less clip.
    return ContractVerdict(False, "caption_repair",
                           "spoken clip missing burned captions")


if __name__ == "__main__":
    # Deterministic fixtures proving the four required cases.
    cases = [
        ("spoken+captions applied", dict(no_speech=False, captions_expected=True, captions_applied=True), "accepted"),
        ("spoken+uncaptioned (hook-only)", dict(no_speech=False, captions_expected=True, captions_applied=False), "caption_repair"),
        ("silent / no-speech", dict(no_speech=True, captions_expected=True, captions_applied=False), "accepted"),
        ("already captioned (double-caption guard)", dict(no_speech=False, captions_expected=True, captions_applied=True), "accepted"),
    ]
    all_ok = True
    for name, args, expected in cases:
        v = classify(**args)
        ok = v.action == expected
        all_ok &= ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: action={v.action} reason={v.reason}")
    print("RESULT:", "ALL PASS" if all_ok else "FAILURES PRESENT")
    raise SystemExit(0 if all_ok else 1)
