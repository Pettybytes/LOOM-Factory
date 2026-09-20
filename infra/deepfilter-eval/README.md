# DeepFilterNet bounded A/B (Segment 5C.1 follow-on, isolated)

Question: does local **DeepFilterNet** audio cleanup materially improve LOOM's
EXISTING transcription (and the approved WhisperX alignment-only step) on
difficult audio, enough to justify adding a preprocessing stage?

**Verdict: DO NOT ADOPT as an automatic preprocessing stage.**

Nothing here is wired into the production pipeline.

## Method — everything except the cleanup is held constant

```
A  original 16 kHz mono  -> LOOM transcription config -> WhisperX align-only
B  DeepFilterNet-cleaned -> SAME config               -> SAME align step
```

The baseline parameters and the alignment step are the *same code* as 5C.1:
`evaluate_ab.py` imports `../whisperx-eval/evaluate.py` rather than
re-implementing it, so `beam_size=5, vad_filter=True,
condition_on_previous_text=False, word_timestamps=True`, `small`, `cpu`/`int8`
apply identically to A and B.

Cleanup runs in a **separate venv** (`~/.venvs/loom-deepfilter-eval`) because
DeepFilterNet pins `numpy<2`, while the WhisperX evaluation venv is pinned to the
exact versions that produced the committed 5C.1 `results.json`. Installing
DeepFilterNet into that venv **did** downgrade `numpy 2.4.6 → 1.26.4` and
`packaging 26.3 → 23.2`; both were **restored** and re-verified equal to the 5C.1
set before any 5C.1 work was considered reproducible. That is why this stage is
process-isolated.

### Environment

| | |
|---|---|
| DeepFilterNet | 0.5.6 (`DeepFilterLib` 0.5.6 wheels for cp311 arm64 — **no Rust toolchain needed**) |
| model | DeepFilterNet3, cache `~/Library/Caches/DeepFilterNet`, **8.3 MB** |
| DFN venv | torch 2.8.0, torchaudio 2.8.0, **numpy 1.26.4 (pin)** |
| ASR/align venv | unchanged 5C.1 set (`numpy 2.4.6`) — verified `ENV_MATCHES_5C1 = True` |

Cleanup I/O deliberately avoids torchaudio: torchaudio 2.8 needs torchcodec for
decoding and **torchcodec cannot load against current Homebrew ffmpeg**
(`libavutil` mismatch). Decoding/resampling therefore uses the ffmpeg binary plus
the stdlib `wave` module.

## Reproduce

```sh
# cleanup stage (its own venv)
~/.venvs/loom-deepfilter-eval/bin/python clean_audio.py in.wav out.wav

# A/B harness (WhisperX venv)
~/.venvs/loom-whisperx-eval/bin/python evaluate_ab.py \
    --out results_ab.json --audio-dir /tmp/loom-dfn-ab \
    --cases "<labels>" <media> [<media> ...]
```

Cost: **$0** — local CPU only, no hosted or paid service.

## Results (254 s of audio, 6 clips, all from the local Reaction Library)

| case | words A→B | lost | recovered | similarity | clean RTF | overhead | loud-region energy kept |
|---|---|---|---|---|---|---|---|
| background noise | 78→64 | 25 | 11 | 0.75 | 0.048 | +0.4 s | **90 %** |
| speech with music | 41→34 | 22 | 15 | **0.51** | 0.046 | +20.8 s | **33 %** |
| difficult low-SNR | 1→0 | 1 | 0 | **0.00** | 0.086 | +14.3 s | **21 %** |
| rapid energetic shouting | 170→154 | 27 | 11 | 0.88 | 0.049 | +1.6 s | **74 %** |
| singing / lyrics | 0→7 | 0 | 7 | 0.00 | 0.058 | +15.7 s | **73 %** |
| clean control | 129→129 | 0 | 0 | **1.00** | 0.065 | +2.6 s | **97 %** |

Alignment succeeded in **every** case (0 unaligned words, no errors) — the
alignment step is not the problem here.

## Findings

1. **No transcription improvement on any difficult case.** Cleanup never gained
   net words: it lost more than it recovered on 4 of 5 non-clean clips
   (25/11, 22/15, 1/0, 27/11). The low-SNR clip went from one word to zero.
2. **The substitutions are degradations, not corrections.** Examples:
   `raised → rage`, `drawers you tell me no drawers → draw`,
   `trey's → trays`, `punching-bag-ish "fuck I won't let y'all have it" →
   "it's it's it's it's it's"`, `lifestyle → last time`. Cleanup traded real
   words for worse ones.
3. **The content-preservation guardrail fails on exactly the content LOOM cares
   about.** Loud-region energy retained: shouting **74 %**, singing **73 %**,
   music **33 %**, low-SNR **21 %** (peak 0.597 → 0.246). A speech enhancer
   removing music is expected; attenuating shouting and singing by a quarter and
   more is the "optimize away the event" failure the brief warned about.
4. **The clean control is untouched** (similarity 1.00, 129→129 words, 97 % energy,
   identical boundaries) — so the stage is *harmless*, just not *helpful*.
5. **Only gain: singing.** Baseline produced 0 words on the James Brown
   performance; cleaned produced 7 — but they are fragments
   (`waa mmmm oh it would be nothin'`), and speech-first LOOM gains nothing.
6. **Segmentation drift dominates on music.** Median timestamp movement on
   matched words is tiny (0.009–0.016 s), but the maximum is **43.6 s** on the
   music clip — the VAD/segment layout moved wholesale. The larger runtimes
   (+20.8 s, +15.7 s) follow from that re-segmentation, not from the cleanup
   itself (cleanup RTF is only 0.046–0.086).
7. **Boundary refinement is not consistently better**: noise lead trim improved
   (5.68 s → 0.85 s) while the low-SNR tail got worse (6.93 s → 13.55 s).
8. **Footprint:** model is tiny (8.3 MB), but the stage needs torch + torchaudio
   (~2 GB) and a **`numpy<2` pin that conflicts** with the rest of the stack.

## Verdict

**DO NOT ADOPT** as an automatic preprocessing stage. It produced no measured
transcription gain on difficult audio, it degraded content on 4 of 5 difficult
clips, and it attenuated the loud, eventful regions that make a moment worth
clipping.

Narrow condition under which it could be revisited (not authorized): as an
**operator-initiated, per-clip** option for a specific unusable recording, never
automatic, never on shouting/singing/music-driven content, and only with the
energy guardrail checked — and it would still need its own acceptance evidence
before promotion.
