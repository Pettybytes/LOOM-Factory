# YouTube Exact-URL Acquisition — Increment 1 (URL contract + local adapter)

> **Status update — Phase A wiring COMPLETE (2026-09-18 ~19:00 EDT).**
> Platform dispatch now routes `platform=youtube` to the local adapter (Instagram still on Apify);
> the premium card shows `Local acquisition · $0 provider cost`; a fixed policy refusal
> (`DURATION_LIMIT`, `SIZE_LIMIT`, …) never offers `Retry Manually`; the paid-provider token gate,
> actor allowlist, circuit breaker and reservation are all skipped for local platforms; and the
> planner + `require_runtime_proven` honour an explicit **adapter proof mode**
> (`LOOM_ADAPTER_PROOF_PLATFORMS=youtube`) that permits a bounded attempt **without** claiming the
> platform is runtime-proven. Mocked end-to-end proof: `Approve → Acquire → local yt-dlp → validation
> → Reaction Library → ACQUIRED`, 1 attempt, `$0`, Apify ceiling untouched.
> Suites: acquisition **374** · discord-review **134** · local-helper **76** — all passing.
> **Phase B (posting the two approved-URL REVIEW cards for Bryan's clicks) and Phase C (runtime
> proof + `runtime_proven` flip + commit/push) are NOT done yet.**

**Date:** 2026-09-18 · **Branch:** `master` · **Commit:** none (not authorized)
**Apify calls:** 0 · **Apify ledger mutations:** 0 · **Real YouTube downloads:** 0
**Migrations:** 0 · **Production YouTube card posted:** 0 · **Cookies/accounts used:** 0

---

## 1. Files changed (new, all tested)

| File | Purpose |
|---|---|
| `infra/acquisition/youtube_url.py` | Strict YouTube exact-URL contract: one video, one identity, one canonical form |
| `infra/acquisition/ytdlp_adapter.py` | Local yt-dlp adapter: bounded commands, hygiene guard, validation, merge, atomic finalize, cleanup |
| `infra/acquisition/local_acquisition.py` | Local acquisition path: $0 provider cost, real audit row, never touches the Apify ceiling |
| `infra/acquisition/ledger.py` *(modified)* | Added `reconcile_local()` — audit a local run without moving the ceiling |
| `infra/acquisition/tests/test_youtube_url.py` | 12 tests |
| `infra/acquisition/tests/test_ytdlp_adapter.py` | 21 tests |
| `infra/acquisition/tests/test_local_acquisition.py` | 6 tests |

## 2. Exact flows — current status

**Implemented and proven (mocked):** URL → strict contract → bounded local adapter →
validated media → deterministic filename → atomic finalize into the Reaction Library root →
persisted library record → truthful **$0** audit row → **ceiling untouched**.

**Reused, unchanged:** the two-gate model (`Approve` qualifies only; `Acquire for Reaction` is the
only initial acquisition trigger; `Retry` is separate, explicit, single-shot), attempt-scoped
provider-run lookup, cross-lane dedup by fingerprint, ACK-first Discord handling, the premium card
and its action matrix, and the Reaction Library root resolver.

**Not yet wired (honest gaps):**
* the runner's platform dispatch (so a `youtube` job reaches `local_acquisition` instead of the
  Apify path) — `live_runner.py` is still Apify-shaped;
* the card's `Local acquisition · $0 provider cost` cost line;
* the intake bridge that turns a manual `/submit_source` submission or an automated Source Engine
  candidate into the shared premium REVIEW card with its `manual_submit_source` /
  `automated_source_engine` provenance.

Until those land, nothing will dispatch a YouTube acquisition: the capability registry reports
`youtube = implemented_not_proven`, and `can_acquire("youtube") == False`. That state is deliberate —
flipping it before a bounded proof would be a false claim.

## 3. URL and rejection behaviour (implemented, 12 tests)

Supported, all collapsing to **`https://www.youtube.com/watch?v=<id>`**:
`youtube.com/watch?v=`, `m.youtube.com/watch?v=`, `youtu.be/<id>`, `www.youtu.be/<id>`,
`youtube.com/shorts/<id>` (plus tracking params/fragments stripped).

Rejected with stable codes: playlists and any selection parameter on a watch URL
(`list`, `index`, `start_radio`, `playnext`, …), mixes, channels/handles/profiles, searches, feeds,
`/live/`, `/embed/`, `/v/`, unsupported paths, malformed or missing ids, id drift between path and
`?v=`, more than one requested video, foreign hosts, credentials, non-http(s) schemes.

Identity: `fingerprint = sha256("youtube\x1f" + canonical_url)` — the *same* construction as
`manual_intake.submission_fingerprint`, asserted by test, so an automated candidate and a manual
submission of the same video are one identity.

## 4. yt-dlp command, options and bounds (implemented, 21 tests)

Probe: `--dump-single-json --no-playlist --playlist-items 1 --skip-download --socket-timeout 20 --retries 0`

Download: `--no-playlist --playlist-items 1 --format "bv*+ba/b" --merge-output-format mp4
--max-filesize 104857600 --retries 0 --socket-timeout 20 --no-part --output <tmp>/<id>.%(ext)s`

A **hygiene guard** fails the command if it ever contains `--cookies`, `--cookies-from-browser`,
`--username`, `--password`, `--netrc`, `--add-header`, `--referer` or `--user-agent`, and asserts
`--no-playlist` is present and `--playlist-items` is bounded to 1.

Bounds: **duration ≤ 300 s**, **size ≤ 100 MiB**, **results = 1**, **attempts = 1**, enforced
(a) from metadata *before* downloading, (b) during (`--max-filesize`), and (c) after
(post-download validation). Separate video/audio streams merge via ffmpeg (`--merge-output-format mp4`).

Post-download validation refuses: missing/zero-byte/oversized file, no streams, ≠1 video stream,
missing audio, unsupported video/audio codec, unparseable or >300 s container duration, ffprobe
failure, and media/expected-id drift. Temp files are isolated per attempt, the final file moves
**atomically** (`os.replace`) only after validation, and partials are deleted on any failure.

## 5. Cross-lane deduplication

The shared fingerprint (§3) is the mechanism; a repeated `Approve`, repeated `Acquire`, in-flight
attempt, existing library asset, and explicit retry all remain governed by the existing lane gates
(dedup key, `attempts_allowed`, attempt-scoped provider-run lookup, `library_has_content`).
**The intake bridge that consumes this identity is not wired yet** (§2).

## 6. Reaction Library behaviour

Reuses `library_root()` (`$HOME/Movies/LOOM-Reaction-Library`, with the
`LOOM_REACTION_LIBRARY_ROOT` override), the same DB contract, gallery (`/library`), detail/player
page, Range-capable media route and Desktop shortcut. Filename is deterministic:
**`youtube__<video_id>__<sha256[:12]>.mp4`**.

## 7. Discord card / action behaviour

Unchanged contract: REVIEW `Approve | Deny | Hold | View Source`; APPROVED `Acquire for Reaction |
Hold | Deny | View Source`; ACQUIRING locked with immediate acknowledgement; ACQUIRED metadata +
library status + `Open in Reaction Library` + `Hold`; FAILED classified stage with `Retry Manually`
only when legitimately retryable. Every displayed action is covered by the existing 11-test action
matrix. **The local `$0` cost line is still to be added** (§2).

## 8. Tests and results

```
acquisition     363 passed   ( +39 new: url 12 · ytdlp 21 · local 6 )
discord-review  134 passed
local-helper     76 passed
services        adapter ok (AQ · durable · live_runner enabled) · helper ok
```

Zero Apify calls, zero ceiling movement, zero cookies/accounts/auth headers, no secrets in code.

## 9. Honest remaining limitations

1. **Not integrated yet**: runner platform dispatch, the card's zero-cost label, and the intake
   bridge — without them a YouTube job cannot actually dispatch.
2. `youtube` remains `implemented_not_proven`; `can_acquire("youtube") == False` until a bounded
   proof succeeds. I did not flip it.
3. No real YouTube download has occurred, so the adapter has not been validated against live
   extractor behaviour (poster/native-attachment choice, real merge, real metadata shapes).
4. Local audit rows are written through the existing 034 contract with `actor_id = local:yt-dlp`;
   they are intentionally invisible to the paid-provider ceiling maths.

## 10. The one input needed from Bryan

One **exact public YouTube URL under five minutes**, sent as the production test target, e.g.

```
https://www.youtube.com/watch?v=<11-char-id>
```

With that URL I can (a) wire the remaining three integrations, (b) run the bounded local proof —
one attempt, ≤300 s, ≤100 MiB, no cookies — and (c) flip `youtube` to runtime-proven only if that
proof genuinely succeeds. Nothing else is required from you.
