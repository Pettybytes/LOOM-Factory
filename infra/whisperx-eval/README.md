# Segment 5C.1 — WhisperX evaluation (isolated)

Does WhisperX materially improve LOOM's timestamp/alignment quality enough to
adopt it? This directory holds the harness and the measured answer. **It is not
wired into the production pipeline.**

Verdict: **ADOPT WITH LIMITATIONS** — adopt the *alignment step* on top of LOOM's
existing transcript; do **not** adopt WhisperX's own transcription/VAD path as a
replacement. See "Findings" below.

## Environment (isolated — do not install into the app env)

`openshorts` pins its own stack (`torch==2.11.0`, `faster-whisper==1.2.1`). The
system Python here is 3.14, which has no torch/ctranslate2 wheels, so the
evaluation lives in its own 3.11 venv:

```sh
uv venv --python 3.11 ~/.venvs/loom-whisperx-eval
VIRTUAL_ENV=~/.venvs/loom-whisperx-eval uv pip install whisperx
```

Measured environment (`results.json → environment`):

| | |
|---|---|
| python | 3.11.14 |
| whisperx | 3.8.6 |
| faster-whisper | **1.2.1 — identical to `openshorts/requirements.txt`** |
| ctranslate2 | 4.8.2 (CPU `int8`; **no CUDA on Apple Silicon**) |
| torch / torchaudio | 2.8.0 / 2.8.0 |
| transformers | 4.57.6 |
| pyannote-audio | 4.0.7 (pulled in; diarization is NOT used here) |

`pip install whisperx` pulls ~2–3 GB (torch, torchaudio, torchvision, pyannote,
transformers, lightning, scikit-learn). Two environment defects, stated plainly:

- **`torchcodec` is broken here.** It ships against ffmpeg 4–7 (`libavutil.56–59`)
  while Homebrew ffmpeg provides newer `libavutil`, so loading fails at import
  (noisy warning from pyannote). It did **not** affect this evaluation because
  WhisperX's own `load_audio` shells out to the ffmpeg binary.
- **CPU-only inference.** CTranslate2 offers no Apple-Silicon GPU backend, so
  transcription runs on CPU regardless of the MPS-capable torch build.

## What is measured

Same audio, same ASR model size (`small`), two paths:

- **BASELINE** — faster-whisper with **LOOM's real parameters**, mirrored from
  `openshorts/subtitles.py` and `transcribe_backends.py`:
  `beam_size=5, vad_filter=True, condition_on_previous_text=False,
  word_timestamps=True`, `cpu`/`int8`. This is LOOM's *current* behaviour.
- **WHISPERX** — transcribe, then wav2vec2 forced alignment
  (`whisperx.load_align_model`, en) over that transcript.

WhisperX transcribes *with* faster-whisper internally, so the measured delta is
the alignment step — except that WhisperX's default VAD is pyannote, not
faster-whisper's `vad_filter`, which is a confound called out below.

## Reproduce

```sh
~/.venvs/loom-whisperx-eval/bin/python evaluate.py \
    --out results.json --audio-dir /tmp/loom-5c1-audio \
    --cases "<comma-separated labels>" <media> [<media> ...]
```

The committed `results.json` was produced from 9 clips drawn from the local
`~/Movies/LOOM-Reaction-Library/library` plus `baseline-results/`, covering the
requested cases: clear conversational, rapid/energetic, pauses and sentence
boundaries, background noise, **song/lyrics**, overlapping/jittery speech,
short-form vertical, and two controls (ambiguous audio, non-speech).

Cost: **$0**. Local CPU only; no hosted or paid transcription service.

## Results (316.1 s of audio across 9 clips)

| case | dur | words base→wx | matched | base words missing from wx | wx words absent from base | median Δstart | median Δend | align RTF |
|---|---|---|---|---|---|---|---|---|
| clear conversational | 77.5 | 41→36 | 14 | 27 | 22 | 0.074 | 0.048 | 0.011 |
| rapid energetic | 50.0 | 170→155 | 141 | 29 | 14 | 0.078 | 0.041 | 0.038 |
| pauses / boundaries | 40.6 | 129→129 | 124 | 5 | 5 | 0.092 | 0.062 | 0.026 |
| background noise | 34.3 | 78→59 | 45 | 33 | 14 | 0.079 | 0.059 | 0.026 |
| **song / lyrics** | 30.0 | **0→15** | 0 | 0 | 15 | — | — | 0.018 |
| overlapping / jittery | 23.2 | 34→41 | 34 | 0 | 7 | 0.091 | 0.052 | 0.021 |
| short-form vertical | 8.9 | 0→0 | 0 | 0 | 0 | — | — | 0.0 |
| ambiguous control | 21.5 | 1→6 | 0 | 1 | 6 | — | — | 0.012 |
| non-speech control | 30.0 | 0→0 | 0 | 0 | 0 | — | — | 0.0 |

**Runtime (CPU, `small`/int8):** baseline RTF **0.111**, WhisperX RTF **0.167**
(~51% more compute), alignment alone RTF **0.019** — 5.9 s to align 316 s of
audio. Alignment never failed: **0 alignment errors, 0 unaligned words**.

**Boundary opportunity** (pre-alignment segment edges vs the aligned spoken
span): segments routinely contain seconds of dead air/music — worst observed
tail 13.6 s, 9.7 s, 5.6 s and lead 11.3 s, 3.8 s, 3.2 s. On the *cleanest*
speech clip the segments were already tight (lead −0.30 s, tail −0.17 s), so the
gain is content-dependent, not universal.

## Findings — what the evidence actually supports

1. **Alignment is cheap and reliable.** RTF ~0.02, no failures, no unaligned
   words on any clip. If boundary precision is the goal, the cost of adding the
   alignment step is negligible.
2. **The precision gain over LOOM's current word timestamps is NOT established.**
   Median word-boundary shift is only ~50–80 ms, and this harness has no ground
   truth, so "different" must not be reported as "more accurate".
3. **Alignment is not uniformly good.** On the rapid-energetic clip it stretched
   single words implausibly — `Yeah,` 3.23–5.06 s and `mean` 5.48–8.84 s (3.36 s
   for one word). WhisperX's words cannot be trusted unconditionally.
4. **The real risk is the transcription/VAD switch, not the alignment.**
   WhisperX's default pyannote VAD changed output materially: on the
   rapid-energetic clip it **dropped the entire opening sentence** LOOM's current
   config captured; on the background-noise clip 33 of 78 baseline words are
   absent; on the ambiguous control it **hallucinated** `Hi Tom! Hi Tom! Hi Tom!`.
   Adopting WhisperX wholesale would therefore trade small timestamp precision for
   real content drift/loss.
5. **Song/lyrics partially works.** The baseline produced nothing on the James
   Brown performance; WhisperX produced 15 words — but they are misheard
   (`"This is a man's way"` for *"This is a man's world"*). Useful signal,
   unreliable text.
6. **Heavy footprint, CPU-only.** ~2–3 GB of dependencies, a broken `torchcodec`
   link against current Homebrew ffmpeg, and no Apple-Silicon GPU path.

## Verdict

**ADOPT WITH LIMITATIONS.**

- **Adopt the alignment step only** — run wav2vec2 alignment over LOOM's existing
  transcript, keeping LOOM's own VAD/decoder config. That preserves current
  content behaviour while gaining word-accurate edges.
- **Do not** replace LOOM's transcription with WhisperX's VAD/decoder path: it
  changed or lost content on 3 of 7 speech clips.
- **Do not** treat aligned timings as ground truth; guard against the stretched
  single-word outliers observed here.
- The boundary payoff (trimming seconds of dead air) is the justification worth
  pursuing, and it belongs to **5C.2 (Complete-Thought Boundary Refiner)**, where
  promotion from proposal to automatic is a trust ramp backed by acceptance
  evidence — not a permanent human gate.
