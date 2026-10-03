# LOOM — NEXT SESSION HANDOFF

## START HERE — 2026-10-03 — GHL UPLOAD TRANSPORT

**Current scope: fix GHL uploads, then hand off. No CapCut/editor/discovery work.**
This section supersedes the older next-action instructions below, including the
September 29 `ASTRA_START` file. **Transport repair is not yet verified.**

### Confirmed request and invariant

- Caller: `adapter.ghl_upload_file`, shared by Manual Intake and existing media storage.
- Exact endpoint: `POST https://services.leadconnectorhq.com/medias/upload-file`.
- Headers: environment-read Bearer credential, `Version: v3`,
  `Content-Type: multipart/form-data; boundary=...`, `User-Agent: LOOMReviewAdapter/1.0`.
- Parts: `file` with filename and `Content-Type: video/mp4`, `name`, `hosted=false`,
  `parentId` targeting the existing LOOM folder. `urllib` calculates Content-Length.
- Official documentation explicitly allows **video up to 500 MB**:
  https://marketplace.gohighlevel.com/docs/ghl/medias/upload-media-content/index.html
  **Do not diagnose a size limit without an actual HTTP response proving it.**
- **Cloudflare is the active LOOM ingress tunnel. Tailscale was decommissioned.**
  Do not re-enable, modify, or troubleshoot Tailscale. The failing transfer is the
  independent **outbound Mac → GHL HTTPS request**, not the inbound Discord tunnel.

### Exact reproduction and evidence

Retained, unchanged valid Kick media:
`/tmp/loom-kick-probe/clip_01M33XASMP29XTQ1QK0EV7TXDC.mp4`
— **30,782,671 bytes**, SHA256
`e8a303d41b3acbd58e017bd75d302c42761a2f60fc6a84c75724df1af8cc2360`.

- Production uploader with the real credential: `Broken pipe`, no HTTP response.
- Same body via curl HTTP/1.1/HTTP/2, both IPv4 edge addresses, native curl multipart,
  Python streamed/chunked bodies and the existing Linux container: also failed.
- A paced curl attempt reached **23,854,796 transmitted bytes / 45.54 s**, then
  received TLS `bad record mac`. This contradicts a universal 15-second cutoff.
- IPv6 could not establish a connection. TLS 1.2/alternate cipher did not cure it.
- A small valid upload returned **201** and verified the configured folder.
  Its one probe object was deleted: `6ac139c822bc27f58f37485a`; absence verified,
  folder count returned from 12 to 11. Subsequent completed probes created no Kick object.
- `hosted=true/fileUrl` is **not a repair**: prior measured behavior registers an
  external reference, not a durable GHL copy. No temporary public media route was created.
- Reduced send buffer did not repair it: 4 KiB timed out writing at 60.08 s;
  16 KiB with a 180-second client timeout still hit `Broken pipe` at 67.30 s.
- An alternate-network/hotspot control was requested from Bryan; no response or
  network switch was recorded before this checkpoint. Do not claim it occurred.

### Source Review comparison — requested by Bryan

There is no separate working GHL uploader to transplant. Media registration and
Manual Intake already converge on `adapter.ghl_upload_file`; posting a Source Review
card can also reuse an already-stored asset and is not by itself a new GHL upload.

- AST comparison: the uploader at pre-chooser **`c690137`** is identical to current
  after removing three `_set_ghl_upload_error` diagnostic calls. No request-body,
  endpoint, timeout, or transport regression was introduced by the chooser.
- Commit **`9973fb7`** (September 19) added retries for the same `Broken pipe` on
  the earlier media/review flow, including a 1.4 MB file. It predates the chooser.
- September 28 logs show `twitch__2880604198.mp4` failed once, succeeded on attempt 2,
  verified its LOOM folder, then reached the review card: **32,956,932 bytes**.
- That existing GHL object remains present: `6abaee9bb4d207ff9d28516c`.
  Its bytes were downloaded without altering the object to
  `/tmp/loom-ghl-previously-stored-twitch.mp4` and re-submitted with the same uploader.
  **Current control: `Broken pipe` at 15.53 s; no new object.**
- Thus the failure is neither specific to the Kick file nor evidence of a new
  chooser/upload implementation. The precise fault location within the outbound
  network/provider path remains **UNRESOLVED**, not automatically the Mac or GHL.

### Next action — avoid repeating the same unsuccessful probes

Run one unchanged multipart upload over an actually different outbound network
(for example, a user-connected hotspot), or from an existing authorized remote host.
Preserve the endpoint, credentials, folder, original bytes and SHA256; record HTTP
status/body if any. The local Docker control is **not** a different outbound network.
If it succeeds, locate/repair the failing outbound path without changing LOOM ingress.
If it also fails, preserve a sanitized reproducible request and obtain provider-side
failure evidence; no support message has been sent or authorized here.

Do not shrink/re-encode the file, raise arbitrary retry counts, weaken TLS verification,
create an alternate durable store, or claim `hosted=true` owns the media bytes.
Only ship a transport change after the original file uploads and GHL's returned media
is independently retrieved with matching byte count/hash and verified folder placement.

### Repository and proof state

- Code baseline: outer **`59674a3`**, equal to origin before this documentation update.
  Only `CURRENT_STATE.md` and this handoff changed. No production code changes,
  restart, network configuration changes or quality reduction.
- Earlier repairs already present: selector fallback `d60edee`; Whole-resume fix
  `4124252` / upload coverage `8aaccd5`. Do not rebuild those paths.
- Prior focused baseline this session: **56 passed + 3 subtests**.
- Discord aggregate, run once: **711 passed, 1 skipped + 54 subtests**.
  Logs: `/tmp/loom-focused-baseline.log`, `/tmp/loom-discord-aggregate-20261003.log`.
- `/health` OK; loaded adapter SHA256 equals disk and committed source:
  `0d090994514a5d96f5b21627f49a7c6fb730d8b933e145b8e98cf8b88b6278a6`.
- `038` remains untracked, untouched, SHA1
  `3da12826ef96dd05ce089b490ee490e66baf01c0`. Never stage/stash/modify it.
- Full Manual Intake acceptance is **not complete**. The existing attachment contract
  requires the original source URL; supplied bytes are not platform-reacquired.

---

## HISTORICAL RECORDS BELOW — NOT THE CURRENT NEXT ACTION

---

# START HERE — NEXT SESSION

## KNOWN-GOOD BASELINE (2026-09-29 closeout)

**Outer `LOOM-Factory`** — branch `master` · HEAD **`d954cecbe44338bb983362e998375783bdcd080c`**
(`fix(intake): publish short-item kind from the adapter that owns it`) · remote `origin`
(`github.com/Pettybytes/LOOM-Factory`) **HEAD == origin**. Worktree clean except the
**deliberately untracked** `infra/supabase/038_loom_editor_session_tokens.sql`.

**Nested OpenShorts** (`openshorts/`, its own repo) — branch `loom/factory-mvp-baseline-fixes` ·
HEAD **`7e25c6050b16877bc3e670871a3f7b4e0740a716`** (`editor: one-time auto-start of the existing
Auto Edit flow`) · remote `loom` (`github.com/Pettybytes/loom-openshorts`) **HEAD == loom/branch**.
Working tree carries **pre-existing unrelated changes that must NOT be swept into a LOOM commit**:
`.gitignore`, `dashboard/src/components/LoomActivityRoot.jsx`, `dashboard/src/lib/api.js`,
`tests/test_header_rich_text.py`, plus untracked `aborted-runs/` and
`security_utils.py.bak-20260908212436`.

**Recovery backup tags (pushed):**
- outer: **`loom-backup-2026-09-29-manual-intake`** → `b2151ca411ed0138e22e9c7564d72fe73bc913ed`
- nested: **`loom-backup-2026-09-29-manual-intake-openshorts`** → `d66e06e77f5f86bb1e7b03b4844a7b2af85af567`

**Measured test state (code/test green — NOT live acceptance):** focused Manual Intake repair tests
**20 passed**; aggregate `infra/discord-review/tests` **682 passed, 54 subtests**. (Re-measure only if
repository state gives concrete reason to doubt these.)

**`038` invariant:** `infra/supabase/038_loom_editor_session_tokens.sql` =
`3da12826ef96dd05ce089b490ee490e66baf01c0` — untracked, unchanged. **Do not touch `038`.**

**Runtime:** adapter healthy; live `source_sha256` == on-disk `adapter.py` (`de3f1f221c91109b…`)
when last checked; process started 2026-09-29 00:06:38 EDT.

**Migrations applied:** `042` (`loom_capcut_handoffs`) and **`043`**
(`register_edited_asset` clean-source params — authored in `f5cc272`, applied by the operator,
confirmed by them). `038` remains unapplied/untracked by design.

**Recently landed milestones (outer repo):**
- CapCut **visual-state producer/consumer** (`3f25685`, nested `30bd7e5`) — resolved canvas/AUTO/FULL/TRACK
  persisted beside the render and consumed by the handoff.
- CapCut **durable handoff provenance** (`e44e64e`, migration `042`) and the vendored optional lane (`c52e2fb`).
- **Clean pre-hook source** for finalized edits (`335041d` + `043`) — the finalize path now persists
  `clean_source_*` so an edited version stays editable.
- **One-time editor Auto-Start** (nested `7e25c60`) — reuses the existing Auto Edit flow; idempotent guard.
- **Manual Intake short-item kind propagation** (`d954cec`) — the platform adapter that owns
  clip-vs-long now publishes `kind`; Twitch/Kick subscript hardened; the intake path no longer erases it.
- **Improved Manual Intake diagnostics** (`d954cec`) — `WINDOW_REJECTED` / `DURABLE_STORE_FAILED` now log
  their preserved underlying reason (log only, never Discord); the Discord interaction update is labelled
  `stage=interaction_patch` with transient transport as `TRANSPORT_FAILED` (HTTPError excluded).

## LIVE ACCEPTANCE STATUS

**Do not claim live acceptance for anything below — it has NOT passed live acceptance.**

- **Manual Intake (short-item kind propagation + diagnostics): CODE/TEST GREEN, NOT LIVE ACCEPTED.**
- **Editor Auto-Start (one-time, `7e25c60`): CODE/TEST GREEN, NOT LIVE ACCEPTED.**
- **Clean pre-hook source / `043` finalize: CODE/TEST GREEN, NOT LIVE ACCEPTED.**
- **CapCut visual-state parity (AUTO/FULL/TRACK/canvas): NOT LIVE ACCEPTED** — parked.

### Live evidence from the most recent Manual Intake test (2026-09-28/29)

**Kick clip** — `https://kick.com/deenthegreat/clips/clip_01M33XASMP29XTQ1QK0EV7TXDC`
- **Whole Video** route was attempted; it reached downstream processing but failed:
  `stage=acquire_media` · `DOWNLOAD_FAILED`.
- **Select Segment** was ALSO attempted on the same Kick clip and did **not** succeed.
- **Therefore do NOT reopen the Whole Video eligibility / window-gate investigation first.**
  Both modes failing points at the **shared downstream Kick acquisition/resolver path**.
- Exact underlying log detail still needs to be retrieved via the improved diagnostics.

**YouTube Short** — source ID `U0cN4rdLxk4`
- The chooser recognized **~20s**; the **Whole Video button was ENABLED**; Whole Video was attempted;
  it did **not** successfully complete intake. Exact downstream failure still to be established from logs.

### IMPORTANT NEXT DIAGNOSTIC (before changing any code)

Retrieve the adapter/runtime logs for the exact Kick and YouTube attempts. Search for:
`clip_01M33XASMP29XTQ1QK0EV7TXDC` · `U0cN4rdLxk4` · `DOWNLOAD_FAILED` · `manual-intake` · `kick` ·
`youtube` · `yt-dlp` · resolver/acquisition errors.
The improved diagnostic seam should now expose the preserved underlying reason.
**Do not assume Kick and YouTube share one root cause.**

## PARKED ACCEPTANCE (resume in EXACTLY this order, after Manual Intake acquisition is stable)

1. Fresh candidate reaches review.
2. Click **Edit** exactly once.
3. Existing OpenShorts/Gemini **Auto Edit must auto-start** without manually pressing Auto Edit.
4. Existing stages must execute: clean source → rerender/reframe → hook → subtitles → register version →
   review card → complete.
5. The new version retains a **verified clean pre-hook source** and remains editable.
6. **Reopen/refresh must NOT auto-create another version.**
7. Only after that passes, resume **CapCut AUTO** visual-parity acceptance.
8. **FULL / TRACK / alternate canvas remain parked until AUTO passes.**

## SCOPE WARNING FOR NEXT SESSION

- Do **not** begin by refactoring LOOM.
- Do **not** redesign Manual Intake.
- Do **not** weaken the long-source window gate.
- Do **not** introduce another downloader/storage provider until the exact failure is proven.
- Do **not** touch CapCut or visual-state while diagnosing acquisition.
- Do **not** solve the YouTube bot challenge unless new evidence establishes it as the blocker for the tested source.
- Do **not** touch `038`.

## NEXT-SESSION STARTING TASK

> READ THIS HANDOFF FIRST. Do not modify code. Retrieve the exact underlying failure evidence for the
> Kick clip and YouTube Short live Manual Intake attempts. Determine whether each failure is
> acquisition/resolver, external platform refusal, transport, or durable-storage related. Only then
> propose the smallest repair.

---

**Editor launch seam: 2026-09-26 ~21:36 EDT. LIVE PASS.** Bryan drove a fresh post-reload Manual Intake card. Not a timeline-UI pass.

**Timeline Editor Phase 2A: LIVE PASS (2026-09-26 ~22:11 EDT). CLOSED — do not redesign or further polish.** Bryan drove the visual timeline on the real editor surface (measured source ~33.30s; START ~3.37s; END ~30.70s; selected ~27.34s). Full evidence, provenance and preserved invariants: **`INCREMENT — TIMELINE EDITOR PHASE 2A`** below.

## INCREMENT — 2026-09-28 (later still #3) — KICK VOD MANUAL INTAKE — **LIVE PASS**

**`KICK VOD MANUAL INTAKE — LIVE PASS`** (operator-driven 2026-09-28 ~19:14–19:17 EDT).
`…/videos/05e14bff-67ca-492a-ba79-3f3248ca41a5?t=1800` → ORIGINAL **30:00 → 31:00**.

Independently verified (logs + runtime + persistence + provider, not the operator report):
**ORIGINAL 1800→1860** · **WORKING 0→60** with `working_timebase_origin_s 1800.0` ·
**durable object** `kick__05e14bff-….mp4` **15,147,726 B** in the LOOM folder (matches recorded size,
placement verified) · **canonical identity hint-free** with `original_url` keeping `?t=1800` ·
**same asset, no second acquisition** (`media_already_acquired: true`, **0** acquisition jobs) ·
**decision actions intact** (Approve Clip / Edit / Reject / Hold / Open Source).

**Bounded acquisition (item 9) — instrumented, not inferred:** the production `acquire_url_media` on
the identical URL+window returned in **25.0 s** with an artifact of **15,147,726 B / 60.016016 s**,
`mode=bounded_segment`, window `1800000→1860000`, one file in the out dir — byte-identical to the
durable object. A full 8.02 h fetch cannot complete in 25 s nor fit the 100 MiB ceiling. *Gap:* no
section-arg/transfer-size is logged for the successful attempt (future instrumentation).

**Gate repair (approved, narrow):** `media_qualification`'s Kick branch was clip-only, so **no Kick
VOD could ever qualify** — every Kick VOD persisted `filtered_media` / `video_type_unproven` before
scoring. Now `/videos/<id>` qualifies (id required; list/channel pages do not) and clips are
preserved. No other semantics touched. +6 regression tests.

**Recorded accurately, against the report:** the working media was **not** playable in Discord —
`mode=poster playable=False` because 14.4 MiB exceeds the tier-0 10 MiB upload limit. The card showed
a poster preview; the graceful fallback worked.

**OPEN FOLLOW-UPS (not fixed):** A) transcript/subtitle reliability across platforms; B) a review
proxy so oversized working assets can be playable in Discord without touching the durable master.

Suites: content_intelligence **482** (+6) · discord-review **479** · acquisition **596** (1
pre-existing pytest-guard failure).

---

## INCREMENT — 2026-09-28 (later still) — TWITCH VOD MANUAL INTAKE — **LIVE PASS**

**`TWITCH VOD MANUAL INTAKE — LIVE PASS`** (operator-driven 2026-09-28 ~18:47 EDT, HEAD `16a6c86`).
Bryan drove `https://www.twitch.tv/videos/2880604198?t=00h26m10s` and chose ORIGINAL 26:10 → 27:10.

Independently verified from logs + runtime + persistence (not from the operator report):
**ORIGINAL 26:10→27:10** (`time_start_s/time_end_s` 1570/1630, `acquisition.original_*`, candidate
`original_window`) · **WORKING 00:00→01:00** (candidate `recommended_clip_*` 0.0/60.0,
`working_timebase_origin_s` 1570.0, `working_source_duration_s` 60.016016) · **durable object is the
bounded 60 s artifact** (provider read: `twitch__2880604198.mp4` 32,956,932 B,
`parentId 6aa61446df2d015553434919`, placement verified) · **same asset on the review card, no second
acquisition** (`lineage.media_sha256` matches, `media_already_acquired: true`, 0 acquisition jobs) ·
**canonical identity is the VOD without `?t=`** · **hint kept as non-identity `original_url`**.

**Bounded acquisition (the important one):** `[intake] bounded window 26:10–27:10 (60.000s)
range=8c082452b496` with `--download-sections` present on the only code path used (no download-then-trim
exists there); a 60.016 s / 33 MB artifact where a full ~6 GB source would have breached the 100 MiB
ceiling (`--max-filesize` + `validate_finalized_media`) and been refused; **no large local residue**
(temp dirs empty) and the run finished within seconds of the modal submit. Full detail:
`CURRENT_STATE.md` → `INCREMENT — 2026-09-28 (later still) — TWITCH VOD MANUAL INTAKE — LIVE PASS`.

Supporting commits: `3f62e86` (#clip-review working media) → `16a6c86` (Twitch VOD contract + the
identity fix that stopped `?t=` splitting dedup identity). **Not** done and not implied: Kick, OBS.

---

## INCREMENT — 2026-09-27 (later) — BOUNDED-INTAKE SEAM REPAIR

**Status: `CODE/SEAM VERIFIED — LIVE ACCEPTANCE EXTERNALLY BLOCKED`.**
Two seam defects closed. **Not** called LIVE PASS: the real Discord run has not succeeded
(YouTube is issuing an anti-bot challenge — see below).

### Defect 1 — window intent could silently downgrade to full source

Discord text → `START/END` parsing dropped several natural forms, and the loss was **silent**:
the intake proceeded as a **full-source** acquisition and reported success. Parser truth table
before → after:

| Operator form | BEFORE | AFTER |
|---|---|---|
| `start=1:23 end=2:45` | parsed | parsed (unchanged) |
| `start=83 end=165` | parsed | parsed (unchanged) |
| `START: 120` / `END: 180` | parsed | parsed (unchanged) |
| **`START 120` (space, no delimiter)** | **dropped → full source** | **parsed** |
| **`START 0:02:00` (clock, no delimiter)** | **dropped → full source** | **parsed** |
| **`window: 120-180`** | **dropped → full source** | **parsed** |
| **bare line `120-180`** | **dropped → full source** | **parsed** |
| **`START` alone / `END` alone / `START: abc`** | **silently full source** | **FAILS CLOSED** |
| URL only · prose containing "start" · a date like `2026-09-27` | full source | full source (unchanged) |

Enforcement is in two places, both before any network work: the operator-facing validation in
`adapter._handle_manual_media_intake` (immediate, explicit refusal), and a `window_validate` stage in
`_manual_media_worker` (defence in depth). **No window intent at all** still allows full source.
The URL is stripped before window scanning, so a query string like `&start=` is never mistaken for an
operator instruction.

### Defect 2 — P0 ORIGINAL-vs-WORKING coordinate collapse

`boundary_plan.recommended_clip_*` is read by every downstream consumer (acquisition handoff,
source-lineage bootstrap, the Phase 2A editor) as **relative to the durable working asset**. It was
being overwritten with the operator's **ORIGINAL-absolute** window.

**Concrete example — ORIGINAL 120 → 180, working asset 0 → 60, origin 120:**

| Field | Value | Coordinate system |
|---|---|---|
| `original_window.start_s/end_s` | 120 / 180 | **ORIGINAL** source |
| `working_timebase_origin_s` | 120 | ORIGINAL time of working t=0 |
| `recommended_clip_start/end` | **0 / 60** | **WORKING asset** |
| working duration | 60 | WORKING asset |

The conversion happens at the smallest authoritative seam (`manual_intake_intelligence`, both
`run_manual_intelligence` and the retry path `enrich_manual_candidate`), and the ORIGINAL pair is
preserved alongside. **A full-source acquisition has origin 0, so its behaviour is unchanged.**
The editor is NOT made to translate ORIGINAL coordinates — it keeps receiving coordinates native to
the media file it edits.

### Defence in depth — working-range assertion

`source_window.validate_working_range()` rejects a plan that does not fit the working asset
(`WORKING_RANGE_REVERSED`, `WORKING_RANGE_NEGATIVE`, `WORKING_RANGE_BEYOND_WORKING_SOURCE`,
`WORKING_RANGE_UNUSABLE`, `WORKING_SOURCE_DURATION_UNKNOWN`), tolerance 2.0 s. It runs on the FINAL
plan whatever produced it, so bad coordinates fail at the CI seam rather than being hidden later by
editor-side clamping. Regression test reproduces the audit's exact case: ORIGINAL 42:00 → 47:30,
origin 2520, working 330 s → plan must satisfy `0 <= start < end <= 330`, and neither 2520 nor 2850
may appear in a working-relative range, while ORIGINAL provenance still records 2520 → 2850.

### Files changed

`infra/acquisition/source_window.py` · `infra/discord-review/adapter.py` ·
`infra/discord-review/manual_intake_intelligence.py` · `infra/discord-review/manual_media_intake.py` ·
**new** `infra/discord-review/tests/test_window_intent_and_coordinates.py`

### Tests

acquisition **596 OK** · discord-review **389 OK** (**+14** new focused) · content_intelligence
**477 OK** · dashboard editor/bootstrap **191 pass / 0 fail**.

### Live Discord acceptance — EXTERNALLY BLOCKED

The single live Discord retest is a **Bryan-only** action. YouTube was returning
*"Sign in to confirm you're not a bot"* at 23:03, and the standing instruction is not to hammer it,
so **no further YouTube requests were issued in this pass**. Do not call bounded Manual Intake
LIVE PASS until the Discord run actually succeeds. Retry when YouTube allows:
`https://www.youtube.com/watch?v=59xxeJCbzaM` with `START: 120` / `END: 180`, expecting a ~60 s
working source, `--download-sections` present, LOOM-folder storage, a review card, **Edit** opening
the ~60 s working source, handles unable to escape real media, and ORIGINAL 120–180 provenance
retained.

**Unchanged by this pass:** Phase 2A · OBS (not started) · the 300 s safety limit · `038` · the
nested OpenShorts repo · GHL routing · decision semantics.

---

## INCREMENT — 2026-09-27 — MANUAL INTAKE: BOUNDED SOURCE + SHARED VALIDATION + PLATFORM ROUTING

**Two commits, in this order. `eeff60c` is deliberately its own commit.**

| Commit | Subject |
|---|---|
| **`eeff60c`** | `fix(intake): fail-closed acquisition + media integrity gate` |
| **`a9df6f3`** | `feat(intake): bounded source windows + shared validation + platform routing` |

`source_window.MS_MAX = 300_000` is **UNCHANGED**.

---

### 1. Media integrity fix (`eeff60c`)

**Root cause** (measured on the retained forensic artifact): a truncated video download that
never finalized, after which the old result selector's *"newest non-zero file wins"* fallback
returned the **audio-only half** — surfacing as the misleading *"supplied file has no video
stream"*. `ffprobe` passed the truncated file cleanly (`has_video=True`, 1920x1080, 466.6 s) while
it was **19,611 bytes short**, so metadata alone was never proof.

**Fix:** `--no-part` removed, bounded retries **3/3** (was 0), fail-closed artifact selection,
integrity gate = **tail decode** (`ffmpeg -v error -xerror -sseof -2 … -f null -`).
**Cost:** 0.06 s healthy / 0.10 s truncated on a 7:46 1080p file. **The 100 MiB ceiling is
unchanged.** Note `--max-filesize` applies **per stream**, which is exactly why a post-download
ceiling check must exist.

**Representation choice is two-stage:** pre-flight (free metadata estimate) picks the **starting**
rung so an obviously oversized representation is never fetched; the **verified ladder** stays the
safety net and still decides. On the incident source the estimate skipped 1080p (245 MB reported)
and 720p (144 MB) and settled at 480p — the exact rung that succeeded.

### 2. Shared validation contract — `infra/acquisition/media_validation.py` (NEW)

ONE platform-independent post-acquisition contract. Normalized checks, in order:
**exists → non-zero → within ceiling → video-bearing → positive duration → integrity valid →
eligible**. Reason codes: `MISSING_FILE`, `ZERO_BYTES`, `MEDIA_EXCEEDS_LIMIT`, `NO_VIDEO_STREAM`,
`BAD_DURATION`, `MEDIA_INTEGRITY_FAILED`. It also owns the probe and the integrity gate;
`ytdlp_adapter` and `manual_media_intake` now **delegate** to it (their public names survive as
thin aliases). It contains no yt-dlp, platform or network knowledge, so a future OBS/provider/
FFmpeg mechanism can be gated identically.

### 3. Platform routing + identity policy

Manual Intake no longer assumes "every platform = generic yt-dlp".
`resolve_platform_capability()` resolves **adapter_id / implemented / runtime_proven / proven
modes / long-form + window requirements / capability state** from `source_adapters` +
`platform_capabilities`. Identity is cross-checked by the **platform's own rule**
(`source_adapters.reconcile_identity`).

**Identity policy — four distinct verdicts, only ONE may block:**

| Verdict | Meaning | Effect |
|---|---|---|
| `reconciled` | authoritative and consistent | proceed |
| **`mismatch`** | **authoritative AND positively conflicting** | **FAIL CLOSED** |
| `unverified` | adapter exists, but no usable identity signal / undecidable | proceed, record |
| `not_applicable` | no adapter registered for the platform | proceed, record |

Absence of proof is **never** manufactured into a conflict, so a platform without authoritative
identity resolution stays usable. Focused tests cover all four states.

### 4. Bounded source windows (URL **and** local upload)

Optional START/END on **both** ingress modes, validated through ONE helper
(`build_operator_window` → `source_window.validate_window`, origin `operator_adjusted`).

* **URL:** bounded retrieval via `--download-sections` + `--force-keyframes-at-cuts`; then
  `validate_resulting_duration` — a mismatch **rejects that representation**.
* **Local upload:** FFmpeg extraction becomes the working source; **fails closed**, never
downgraded to the whole file.

**A bounded extraction is never stored as if it were the source** — it is always identified as an
extracted window in provenance and in the durable filename.

### 5. Dual-coordinate provenance (`media_reference.acquisition`)

TWO coordinate systems, kept explicitly distinct so neither is ever inferred from the other:

* **ORIGINAL source coordinates** — `original_start_s`, `original_end_s` (what the operator asked for)
* **WORKING source coordinates** — `working_source_duration_s`, `working_timebase_origin_s` (the
  durable asset's own 0-based timeline; working `t=0` is that origin in the source)

plus `acquisition_mode`, `acquisition_mechanism`, `window_seconds`, `range_fingerprint`,
`platform_capability_state`, `platform_proven_modes`, `identity_reconciliation`.

### 6. PLATFORM TRUTH TABLE — do NOT upgrade these claims

| Platform | Full source | Bounded | Exact outstanding proof |
|---|---|---|---|
| **YouTube** | ✅ proven | ✅ **LIVE PROVEN through Manual Intake** (120–180 s → exactly 60.0 s working source, integrity OK, durable-placed) | — |
| **Twitch** | ✅ proven | ✅ proven **via the adapter lane** (2026-09-18); through **Manual Intake = WIRED / LIVE ACCEPTANCE OUTSTANDING** | a reachable Twitch **VOD** URL + one Manual Intake bounded run |
| **Kick** | ✅ **CLIP only** | ❌ **NOT PROVEN** | a reachable Kick VOD (`01a02be6-…` is **externally 404**); then a bounded Kick VOD proof |
| **Reddit / TikTok / Instagram** | — | ❌ **NOT CLAIMED** — no adapter registered (`not_applicable`) | no bounded claim is made |

**Kick ranged-VOD: does NOT work / NOT PROVEN.** Do not manufacture URLs to change this.

### 7. OBS / local long-form master — FOUNDATION vs FUTURE

**Architectural foundation (documented, NOT new work):**
`local long-form master → bounded FFmpeg extraction → validated working source →
downstream LOOM-compatible media`. This is the foundation for future OBS ingestion.

**Explicitly NOT proven (future proofs, do not claim):** multi-hour OBS performance · multitrack
synchronization · camera/audio alignment · multiple-source splicing · automatic moment discovery
across an OBS master. **Do not redesign bounded extraction.**

### 8. 300-second limit — classified

**`source_window.MS_MAX = 300_000` is a CURRENT SAFETY LIMIT — NOT a permanent LOOM architectural
invariant.** It applies to the current bounded acquisition/intake contract. Future OBS/editing
workflows may require longer working windows. **Do not increase it now.** It is enforced from ONE
place: both ingress modes route through `build_operator_window` → `source_window.validate_window`.

### 9. Bryan-only Discord acceptance — the ONLY remaining live step

Submit the long YouTube source through Discord Manual Intake **with a bounded window**:

* URL: `https://www.youtube.com/watch?v=59xxeJCbzaM`
* `START: 120`
* `END: 180`

Expected: long source → **only the ~60-second working source** acquired → validated → stored in the
**LOOM** GHL folder → normal review card → **no full 7:46 durable asset** → **Edit** opens the
already LIVE-PASS **Phase 2A** editor → the editor has the **whole ~60-second working source**
available. (The initial CI-selected clip may be smaller — that is correct.)

---

 the accepted TikTok candidate (`platform_content_id` `7671018311492193567`, tenant `dreemzy`) has lineage media and window `0.0–11.166667`, and **zero** `loom_media` rows. Existing bootstrap keys only `loom_media`, so that hop was FAIL (`no_versions`). OpenShorts `127.0.0.1:8000` was up. Token table `loom_editor_session_tokens` was readable. No GHL browse.

What changed: source-review cards now include **Edit** (`se_<fingerprint>`). Edit is not a decision. It mints the existing editor session as `src-<fingerprint>:0` / `source-1.0`, sends an ephemeral `/edit?token=` link, and restores the same card. Bootstrap for `src-` clip ids reads `moment_evidence.lineage.media_reference` plus `boundary_plan`. Rendered-clip rehydration is unchanged.

Tests: `python3 -m unittest discover -s infra/discord-review/tests` — **350 OK**. `038` still untracked, SHA1 `3da12826ef96dd05ce089b490ee490e66baf01c0`.

**Bryan live acceptance — LIVE PASS (2026-09-26 ~21:36 EDT):** fresh post-reload `#clip-review` card showed `Approve Clip | Edit | Reject | Hold | Open Source`. Edit returned the private Open Editor action. The original card stayed present and undecided. Open Editor opened `app.loomz.stream/edit`. The correct source loaded at `0.0 → 20.897959s`, matching the card's `00:00 → 00:20.89`. No approval, rejection, or hold. No second card. No acquisition.

**Next editor phase — AUTHORIZED, BUILT, AND NOW LIVE ACCEPTED.** Trim start/end + ±1s were built on the existing OpenShorts edit/rerender path and Bryan accepted them live on 2026-09-26 ~22:11 EDT. No second renderer was built and no full timeline UI was added. Future typed edit events ride in `loom_edit_corrections.edl_metadata`. **Do not reopen Phase 2A.**

---

## INCREMENT — 2026-09-26 (later) — TIMELINE EDITOR PHASE 2A — **LIVE PASS**

**Status: `PHASE 2A — LIVE PASS` (2026-09-26 ~22:11 EDT). CLOSED — do not redesign or further polish.**

### Live acceptance evidence (Bryan, real editor surface)

| Observable | Live value |
|---|---|
| Source duration measured by the editor | **~33.30s** |
| START moved to | **~3.37s** |
| END moved to | **~30.70s** |
| Selected duration | **~27.34s** |

**Live-verified:** Edit → Open Editor works · the correct source media loads · the timeline renders
against the **measured** source duration · START handle works · END handle works · dragging updates
the selection correctly · numeric START/END values stay synchronized with the handles · ±1 second
controls function correctly · the selection remains bounded by the real source · **Preview
Selection** functions correctly · the preview is **non-destructive** · editor interaction behaved
properly.

The accepted figures also confirm the §7 contract end to end: the measured source (~33.30s) is
longer than the previously recommended window, and Bryan was able to move START later and END
earlier/later *within the real source* — the LOOM window behaved as a recommendation, not a bound.

**Scope built:** trim start/end + ±1s, as authorized. No SFX, transitions, overlays, commentary, or
timeline-UI expansion. No second renderer. No Discord Activity editor resurrection.

### Reuse audit (§1A) — what was found, and what was done with it

The editor page Bryan LIVE-tested is `openshorts/dashboard/src/components/ManualTrimEditor.jsx`,
mounted at `main.jsx:112` (`view === 'editor'`, the `/edit` route) and `LoomActivityRoot.jsx:195`
(Discord Activity). Its existing state: `clipUrl` (source video), `clipDurationSec`,
`trimStart`/`trimEnd`, `videoTime` (playback/current time), `edl` (edit recipe), `framing`, `words`,
`scenes`. **No parallel editor was created.**

`openshorts/dashboard/src/components/ClipEditor.jsx` is the EXISTING OpenShorts range/trim UI. It is
a **multi-segment NLE track** inlined in a 1613-line modal: a source monitor with rAF-throttled
scrubbing, draggable trim handles (`edge: start|end|move`), a program track with per-segment handles
and render-coverage status, three-point in/out marks, undo/redo, word snapping, and **original-source
bounds** (`sourceAvailable ? {lo:0, hi:sourceDuration} : {lo:canonical.start, hi:canonical.end}`),
rendering through `POST /api/clip/rerender`. It already provided most of this capability.

| Capability | Verdict |
|---|---|
| Video preview / playback | **EXISTING + EXTENDED** — `PreviewSurface` (additive `muted`, `onDurationChange` props; defaults preserve accepted behaviour) |
| Source media loading | **EXISTING + REUSED** — durable bootstrap `media_url` via `getApiUrl` |
| Trim start/end state | **EXISTING + REUSED** — `trimStart`/`trimEnd` stay the single source of truth |
| Source duration | **EXISTING + EXTENDED** — bootstrap `duration_seconds` now split from the measured media duration |
| Seeking | **EXISTING + REUSED** — `seekTo` |
| EDL / edit state | **EXISTING + REUSED** — `edl`; new control writes through `setRange` |
| Clip rerender | **EXISTING + REUSED** — still `segments: [{start,end}]` → `POST /api/clip/rerender` |
| Reframe | **EXISTING + REUSED** — `framing` |
| Subtitles / hook / header | **EXISTING + REUSED** — `SubtitleModal`, `HookModal`, `HeaderModal` |
| Render requests | **EXISTING + REUSED** — `/discord/editor/complete` → existing registration |
| Word snapping / drag / scrub semantics | **EXISTING + REUSED** — ClipEditor's mechanics reused verbatim (primary-button-only + `preventDefault`, `setPointerCapture`, pxPerSec from the rect, scrub-to-edge while dragging, snap on RELEASE, ms rounding) |
| Range RULES (min window, snap, rounding, bounds) | **EXISTING + EXTENDED** — moved to `src/lib/timelineState.js`, pinned to ClipEditor's old inline behaviour by parity tests |
| Styling range controls (`RangeStyleControls`) | **NOT APPLICABLE** — it styles TEXT ranges, not time ranges |
| Single-range visual track for `/edit` | **NEW — smallest possible.** Justification: ClipEditor's tracks are multi-segment, load their EDL from the live endpoint, and authenticate as an OpenShorts user; the `/edit` surface needs one range fed by the LOOM durable bootstrap under an editor-session Bearer. Its rules are NOT duplicated — they come from the shared module. |

**No translation layer.** The timeline writes the same `trimStart`/`trimEnd` → `segments` →
`POST /api/clip/rerender` contract OpenShorts already renders, and persists through the existing
`edl_metadata` jsonb (LOOM's correction/event persistence). Nothing LOOM-only sits between the
control and the render pipeline.

### §7 answer — SOURCE RANGE vs SELECTED RANGE

The timeline now shows the whole durable source as the track and the LOOM window as a band inside
it, so a boundary can be extended into real source beyond the recommendation.

**Measured, not assumed (read-only probes):** the accepted TikTok candidate
(`platform_content_id 7671018311492193567`) carries `moment_evidence.lineage.media_reference` as a
**bare URL string with no `duration_seconds`**, and its `boundary_plan` window is `0.0 → 11.166667`.
`source_review.resolve_source_editor_identity` therefore derives `duration_seconds` from
`boundary_plan.duration` and **floors it at the window end**. An `ffprobe` of the durable asset
returned `duration=11.166667`, i.e. for that asset the window genuinely IS the whole file.

**Smallest missing contract (reported, NOT built, no launch-seam redesign):** the durable source
media reference can carry no duration, so LOOM cannot *declare* the true available source duration
server-side; the declared value is a window-derived floor. The authoritative bound is therefore the
**media element's own measured duration** of `media_url` — which the editor already loads. The
editor takes `max(measured, declared, range.end)`, so a longer real source can never be under-reported
and a range can never sit outside its own source. Closing this properly later means adding
`duration_seconds` to `moment_evidence.lineage.media_reference` at intake (a data-completeness fix,
not an editor change).

### Preview selection (§8) — browser-native only

`preview selection` seeks the existing preview element to START, plays, and pauses at END, and
unmutes only while previewing so the cut can be *heard*. It issues **no** render, registration, asset
version, review card, approval, or acquisition. Verified by construction: the handler only calls
`currentTime`, `play()`, `pause()` and a `timeupdate` listener on the element that already holds the
media; no API call is made.

### Tests + build

| Suite | Result |
|---|---|
| `node --test src/lib/*.test.mjs` (dashboard) | **191 pass** (157 baseline + **34 new** in `timelineState.test.mjs`) |
| `npm run build` | **✓ built** (no errors) |
| `infra/discord-review` | **350 OK** |
| `infra/content_intelligence` | **477 OK** |
| ESLint on changed files | no NEW errors (6 errors + 2 warnings are pre-existing in `ManualTrimEditor.jsx` at the starting commit) |

The new tests include a **parity guard** that pins `clampSegmentToBounds` and `snapToWords` to
verbatim copies of ClipEditor's former inline `clampSeg`/`snapEdge`, so the shared module cannot
silently change what a legal cut is.

### Provenance, version control + preserved invariants

**Resolved.** `openshorts/` is gitignored by the ROOT repo (`.gitignore:2`, deliberately, added in the
initial commit `2bfb421` as *"Upstream OpenShorts clone (separate repo with its own remote/history)"*).
It is therefore an **embedded upstream checkout, not a submodule** — `openshorts/.git` is a real
directory and there is no `.gitmodules`. Phase 2A was committed in that nested repo instead of being
force-added to the root repo; the root ignore rules were left untouched.

| Item | Value |
|---|---|
| Nested OpenShorts commit | **`c2d89beaf5a0da52b205b72b0d821a4cdb961228`** — 5 files, +1302/−6 |
| Nested branch | **`loom/factory-mvp-baseline-fixes`** (tracking `loom/loom/factory-mvp-baseline-fixes`) |
| Private backup | **`Pettybytes/loom-openshorts`** (PRIVATE, created 2026-09-27T02:01:42Z) |
| Third-party upstream | `origin` = `https://github.com/mutonby/openshorts.git` — **unchanged, received no push** |
| Upstream provenance | merge-base with `origin/main` still `a602a2aa96f3eeb436ef740a011b3b9092350a43`; 420 upstream commits reachable |
| Integrity | SHA-256 of all 5 Phase 2A files **identical** pre- and post-commit (no Git normalization) |
| Root LOOM repo | `HEAD == origin/master == 45b9d12201e2a39f1b0e5bcce106824c975a8991`, pushed to `Pettybytes/LOOM-Factory` |
| Root worktree | clean **except** the preserved untracked `038` |
| **Preserved `038` invariant** | `infra/supabase/038_loom_editor_session_tokens.sql` — untracked, **do not add, do not commit, do not modify, do not execute**; SHA1 **`3da12826ef96dd05ce089b490ee490e66baf01c0`** unchanged |
| Untouched by design | the 6 pre-existing unrelated nested changes (`.gitignore`, `LoomActivityRoot.jsx`, `api.js`, `test_header_rich_text.py`, `aborted-runs/`, `security_utils.py.bak-*`) were left uncommitted |

**Non-blocking observation:** the push warned that the pre-existing upstream file
`churchil_queen_vertical.gif` (62.36 MB) exceeds GitHub's 50 MB recommendation. It is **not** part of
Phase 2A (commit `b485854` upstream) and the push succeeded; a candidate for Git LFS if the repo grows.

### Remaining follow-up (recorded, NOT built)

**`duration_seconds` chain for Manual Intake.** The durable source media reference can be a bare URL
with no duration, so the bootstrap's declared `duration_seconds` is window-derived (floored at the
window end) rather than the true source length. Phase 2A already handles this correctly by taking the
**measured** media duration as authoritative and treating declared as a fallback — which is exactly
what the live acceptance exercised (~33.30s measured). The chain to close it properly: Manual Intake
records the real duration into `media_reference` → the source editor bootstrap uses it → browser
measurement remains verification/fallback. This is a data-completeness fix, **not** an editor change,
and it must not be used to reopen Phase 2A.

### Deliberate non-goals (with reasons)

* **Word snapping on `/edit`** — NOT wired. Source-launch bootstraps return `captions: []`, and the
  `words` on this surface are **clip-relative ms** (`startMs`/`endMs`) while the shared snapper reads
  **source seconds** (`w.s`/`w.e`). A toggle here would have silently done nothing, so no control was
  shipped. Revisit only with a durable source-time word set.
* **ClipEditor dedup left undone** — ClipEditor still holds its own copies of the min-segment,
  snap-window and rounding rules that now also live in `timelineState.js`. Consolidating it is a real
  improvement but touches a working, unversioned production component, so it is flagged as a
  follow-up rather than done blind. The shared module is ready for it and parity tests exist.

### Live acceptance — PASSED (2026-09-26 ~22:11 EDT)

Bryan drove the real editor surface. The five checks below were executed live and all passed; the
observed values are recorded in the evidence table at the top of this increment.

1. Opened a `#clip-review` card and clicked **Edit** — passed.
2. Dragged the timeline START/END handles and used **start ±1s** / **end ±1s**; the numeric
   start/end fields tracked the handles exactly (one state, two views) — passed.
3. Confirmed neither boundary could leave the real source — passed.
4. Clicked **preview selection**: it played the selected range and stopped at END, and was
   non-destructive — passed.
5. Moved boundaries within the real source (measured ~33.30s) — passed.

Nothing was registered, approved, distributed, or rendered by these interactions. **Phase 2A is
CLOSED.**

---

**Authoritative closeout: 2026-09-26 (session ended ~19:30 EDT).**
Supersedes the 2026-09-25 handoff and the 2026-09-26 Manual-Intake closeout.
**Read this file first.** `HANDOFF.md` defers to it; `CURRENT_STATE.md` is the execution authority
and carries the long-form history.

---

# START HERE — TIMELINE EDITOR

**Read this section only, then open the handful of files it names. Do not search the repo.**

### 1. Current HEAD

Code HEAD **`253d503b000925f8561a0c6ea340395266c3e8c9`** (`fix(media): route LOOM uploads into the
existing GHL LOOM folder, verified`), plus this documentation commit. The documentation commit that
introduced this section is the live HEAD — **verify, never assume**:

```
git rev-parse HEAD && git rev-parse origin/master && git status --porcelain
git log --oneline -6
```

### 2. Stage / segment

Stage 2 · **Segment 5 — Multi-Source Moment Mining** (active). Segment 6 not started. RECUT on hold.
`Segment 5 is NOT closed`: the real authenticated long-form YouTube run, the FIRE-APPROVE →
n8n/OpenShorts bridge verification, and remaining six-platform / FIRE-path proofs are still open.
**Segment 5 remains OPEN.**

### 3. LIVE ACCEPTED — do NOT reopen (see §11 closed list)

Manual Intake operator-driven, real Discord, all **nine** paths: Reddit URL + file, Twitch URL +
file, Kick URL + file, Instagram URL, TikTok URL, **YouTube URL**. Engagement metadata capture is
live accepted (YouTube: 2,010,656 views / 13,411 likes / 645 comments / Market 100/100 / coverage
3/5). **GHL FOLDER ROUTING — LIVE PASS** (see §13a). Full lifecycle, decisions and terminology: §3,
§4, §10, §11, §13.

### 4. Exact next task

Build the **missing timeline-editor capability** — the only item between Manual Intake finalization
and the already-authorized launch spine / Segment 6. **This is a NEW capability; do not resurrect
the old Discord Activity editor or any abandoned approach** (see §5C below and §7).

### 5. The gap (authoritative, from `EDITOR_IMPLEMENTATION_PLAN.md`)

**OpenShorts has NO manual timeline editor today.** Its "editor" is LLM-driven
(`VideoEditor` → Gemini → FFmpeg filter/EDL). A lightweight **manual correction interface must be
ADDED**, reusing the existing transcript / scenes / EDL / Remotion-preview / reframe / subtitle /
rerender infrastructure. The plan above is **APPROVED (2026-09-13)** and is the schema + exact-files
plan — start there, do not re-plan it.

### 6. Files to inspect FIRST (small, explicit set)

| Purpose | File |
|---|---|
| Approved editor plan (schema + files) | `EDITOR_IMPLEMENTATION_PLAN.md` |
| Editor gateway, sessions, render/return seams | `infra/discord-review/adapter.py` |
| Review card + decisions (Discord seam) | `infra/discord-review/source_review.py` |
| Durable manual lineage | `infra/discord-review/manual_intake_intelligence.py` |
| Durable media + folder upload | `infra/discord-review/manual_media_intake.py` |
| Edit-session schema | `infra/supabase/012_loom_edit_sessions.sql`, `013_register_edited_asset.sql`, `014_loom_editor_session_token.sql`, `015_loom_editor_launch_context.sql`, `016_loom_editor_draft.sql`, `037_loom_edited_asset_storage_status.sql` |
| OpenShorts backend + render | `openshorts/app.py`, `openshorts/clip_selection.py`, `openshorts/dashboard/` |

### 7. Exact symbols / routes likely involved (`infra/discord-review/adapter.py`)

* **Editor gateway:** `_handle_editor_api(method)`, `_authorize_editor_path(...)` (strict endpoint
  allow-list + job/clip ownership), `_forward_to_backend(...)`, `_backend_request(...)` — the backend
  is the **loopback OpenShorts server `http://127.0.0.1:8000`**, and the adapter translates the
  durable job identity to the rehydrated `temp_job_id` (clip index collapses to `0`).
* **Allow-listed editor endpoints:** `GET /api/status/{job}`, `GET /api/source/{job}`,
  `GET /api/clip/{job}/{idx}/{transcript|edl|scenes}`, `GET /api/render/{id}`,
  `POST /api/{subtitle|clip/rerender|clip/reframe|hook|edit|header}`.
* **Session / launch:** `/discord/editor/bootstrap` (durable bootstrap — resolves the review asset
  from LOOM media/version records, **not** transient `/api/status`), `/discord/editor/exchange`,
  `/discord/editor/oauth`, `/discord/editor/pending`, `/discord/editor/draft`;
  `mint_session_token_diag`, `resolve_editor_session_token_diag`, `register_editor_token`,
  `consume_editor_token`, `resolve_edit_session`, `fetch_session_by_id`,
  `EDITOR_TOKEN_TABLE = "loom_editor_session_tokens"`.
* **Edit completion / render:** `_handle_editor_complete`, `register_edited_asset_rpc(payload)`,
  `register_clip_media(...)`.
* **Review card + decisions:** `_post_source_card(...)`, `_handle_interaction()`, and in
  `source_review.py`: `format_source_embed`, `format_source_buttons`, `make_source_custom_id`,
  `parse_source_custom_id`, `persist_decision`, `decided_embed`.

### 8. Editor source-resolution contract (MANDATORY)

Resolve media through LOOM's **durable identity/lineage**. Never locate media by browsing GHL,
searching folders, guessing or fuzzy-matching filenames, scraping Discord attachments, or
re-discovering the platform URL when durable media already exists.

Conceptual chain: `Discord review card → candidate/review identity → tenant_id → durable
source/media reference → exact GHL asset / durable media URL → selected source window → editor
session → edit instructions → render → review`.

Where those identities live TODAY:

| Identity | Where it lives |
|---|---|
| Candidate / review identity | `loom_source_candidates.duplicate_fingerprint` (+ `tenant_id`, `profile_id`, `platform`, `platform_content_id`) |
| Review card ↔ candidate | `loom_source_candidates.moment_evidence.lineage.card_message_id` |
| Human decision | `loom_source_reviews` (keyed by `duplicate_fingerprint`, first terminal decision wins) |
| Durable manual media | `loom_manual_submissions.media_reference` (`url`, `file_id`, `folder_id`, `sha256`, `size_bytes`, `placement_*`) and `moment_evidence.lineage.media_reference` |
| Source window | `loom_source_candidates.boundary_plan` (+ `moment_evidence.lineage.boundary_provenance`) |
| Clip/burned + clean pre-hook source | `loom_media` (`canonical_url`, `clean_source_url`, `ghl_media_id`, `ghl_folder_id`, `previous_asset_version`) |

Shortest existing path: **candidate row → `moment_evidence.lineage.media_reference` (GHL filesafe
URL) → that asset**. For rendered clips: `loom_media.canonical_url` + `clean_source_url`.

### 9. Discord integration seam

`#clip-review` cards are built by `source_review.format_source_embed` with buttons from
`format_source_buttons`; custom_ids are `make_source_custom_id(decision, fingerprint)` =
`<code>_<sha256>` and parsed by `parse_source_custom_id`. Clicks arrive at
`adapter.Handler._handle_interaction` and are dispatched by that parsed custom_id; decisions persist
via `source_review.persist_decision`, and the same card is then edited in place. **An `Edit` /
`Open Editor` action should be ADDED as another button on this existing machinery — reuse it, do not
replace it.**

### 10. Existing editor seam / render seam / return-to-review seam

* **Existing editor seam:** there is no manual timeline editor (see §5). What exists and is reusable:
  edit sessions (`loom_edit_sessions`), single-use hashed session tokens, the durable bootstrap, the
  `/api/*` allow-list gateway to OpenShorts 127.0.0.1:8000, and `loom_editor_draft`.
* **Render seam:** OpenShorts is the ONLY rendering engine — reuse it, never build a second one.
  Render/export is reached through the gateway (`POST /api/edit`, `/api/clip/rerender`,
  `/api/subtitle`, `/api/clip/reframe`, `/api/hook`, `/api/header`; `GET /api/render/{id}` for
  status), and `register_edited_asset_rpc` atomically allocates the next `asset_version`
  (`render-<major>.<minor>`, `previous_asset_version` = lineage) and marks the session completed.
* **Return-to-review seam:** `register_clip_media(...)` persists the durable artifacts to
  `loom_media`, and `send_review_card(payload)` + `check_existing_card(tenant_id, clip_id,
  asset_version)` + `submission_fingerprint(body)` keep **exactly one active review object per
  asset**. A new version is `final_review_pending` — never approved, never promoted, never
  distributed. Reuse the same one-active-review principle; never mint a duplicate card.

### 11. Known constraints (hard)

* **Editor acceptance is LOCKED** — do not re-audit, re-test, or add Editor feature work, and do not
  resurrect the Discord Activity editor architecture or abandoned approaches.
* **Preserved migration `038_loom_editor_session_tokens.sql`**: untracked, **do not add, do not
  commit, do not modify, do not execute** (SHA1 `3da12826ef96dd05ce089b490ee490e66baf01c0`).
* **Do NOT restructure GHL folders.** The LOOM folder now also contains pre-existing subfolders
  `Intake Reviews` and `dreemzy`; leave them alone.
* **The editor must not depend on browsing GHL folders.** Folder targeting is a proven storage
  primitive; the eventual durable organization must stay **tenant-aware**
  (`LOOM → tenant_id → lifecycle assets`) but the exact hierarchy is **NOT locked**.
* No Manual Intake acceptance re-runs, no paid acquisition, no RECUT, no Segment 6, and no
  age/adult gate (`infra/content_intelligence/tests/test_age_gate_removed.py` is the guard).

### 12. What requires Bryan's live acceptance

Any editor launch from a real Discord card, and any new-asset acceptance, must be Bryan driving the
real Discord surface. Tests prove wiring, never acceptance.

### 13. What NOT to investigate

See the closed list in §14. If a fact is closed there, do not spend tokens re-deriving it.

---

## 1. Repository state (verified, not assumed)

| Item | Value |
|---|---|
| Branch | `master` |
| Final pushed commit | **`c7eaedd0acf74f991d510d0ad54cb85f073c6c53`** (engagement-metadata increment, §10) → later `253d503` (GHL folder routing, §13); see `git log` |
| Previous code commit | `925a8343062e382b540ef20795fa2e2d8805bef9` |
| `origin/master` | identical to HEAD — `HEAD == origin/master` proven |
| Ahead / behind | `0 / 0` |
| Expected worktree | clean **except** one intentionally preserved untracked file |
| Preserved file | `infra/supabase/038_loom_editor_session_tokens.sql` — untracked, **do not add, do not touch** |
| Its SHA1 | `3da12826ef96dd05ce089b490ee490e66baf01c0` |

Re-verify with:
```
git rev-parse --abbrev-ref HEAD && git rev-parse HEAD && git rev-parse origin/master
git status --porcelain && shasum infra/supabase/038_loom_editor_session_tokens.sql
```

---

## 2. Runtime state (no secrets)

| Item | Value |
|---|---|
| Discord adapter | `infra/discord-review/adapter.py` (host process) |
| Supervision | `infra/discord-review/supervise.sh` → `run.sh` (sources the `~/.openclaw/.env` whitelist); stdout/stderr → `adapter.log` |
| Bind | `127.0.0.1:8090` |
| Health check | `curl -s http://127.0.0.1:8090/health` → `{"status":"ok", ...}` |
| Restart | `pkill -f adapter.py` — the supervisor relaunches it within ~3s. **Always restart after editing adapter/source-review code**: a stale process silently serves old code and previously made a real click look like a no-op. |
| Paid acquisition | `LOOM_ACQUISITION_LIVE=0` — manual intake must never need it |
| Manual intake channel | `LOOM_MANUAL_INTAKE_CHANNEL_ID` (`#manual-intake-📸`) |
| Review channel | `LOOM_MANUAL_REVIEW_CHANNEL_ID` (`#clip-review`) |

---

## 3. What is LIVE PROVEN (operator acceptance)

**Bryan personally drove every one of these through the real Discord interface.** This was not an
in-process test, fixture, simulated click, or Bentley-submitted transaction.

| # | Path | Result |
|---|---|---|
| 1 | Reddit — URL intake | **LIVE PASS** |
| 2 | Reddit — downloaded/file intake | **LIVE PASS** |
| 3 | Twitch — URL intake | **LIVE PASS** |
| 4 | Twitch — downloaded/file intake | **LIVE PASS** |
| 5 | Kick — URL intake | **LIVE PASS** |
| 6 | Kick — downloaded/file intake | **LIVE PASS** |
| 7 | Instagram — URL intake | **LIVE PASS** |
| 8 | TikTok — URL intake | **LIVE PASS** |

Do **not** claim platform/input-mode combinations beyond this table.

### Proven lifecycle
```
operator-supplied source → #manual-intake → intake-media → ingestion
→ durable GHL media → Content Intelligence (advisory) → durable candidate/review object
→ exactly one active review card → human decision → durable loom_source_reviews row
→ same Discord card visibly reflects the decision
→ manual media is not downloaded again after approval
```

The approved manual-media path correctly reports:
*"Media already stored at intake — no acquisition needed (nothing to download)."*
That behaviour is **intentional**.

---

## 4. ACCEPTED SEMANTICS — preserve these

**Manual intake is HUMAN-ARBITRATED. Content Intelligence scores are advisory.**

A low score, `FIRE FAIL`, missing market evidence, unsupported classification, or a
`CI: NOT RECOMMENDED` result **MUST NOT** prevent an operator-supplied asset from reaching human
review. **Bryan is the final arbiter.**

Kick proved this specifically: CI returned `filtered_media` / `video_type_unproven` /
`CI: NOT RECOMMENDED`, and the asset still remained reviewable, exposed
`Approve Clip / Reject / Hold`, was approved, persisted the decision, updated the same card, and
exists durably in GHL.

Do **not** later reintroduce automated hard filtering into manual intake.

### Fixes preserved by commit `925a834` (exact terminology)
1. **post-persist render failure can no longer make a successful decision appear to have failed** —
   the rich render is best-effort (`format_source_embed`) with a never-raising
   `minimal_decision_embed` fallback, so `persist decision` can never be hidden by `render updated card`.
2. **CI-declined manual media has an explicit honest UI state** —
   `format_manual_not_recommended_embed` renders `📥 MANUAL INTAKE · CI: NOT RECOMMENDED`, the
   `Reason` (e.g. `video_type_unproven`), and `Analysis: … unavailable` instead of coerced zeros
   masquerading as analysis.
3. **operator-supplied content remains human-reviewable when CI does not recommend it.**
4. **manual re-analysis/re-ingest is bound deterministically** — newest-first content-id lookup,
   older duplicates marked `SUPERSEDED_MANUAL_REINGEST` (evidence retained, never deleted), and the
   card id remembered in `moment_evidence.lineage.card_message_id` so a re-analysis **edits the same
   card** instead of minting another (`remember_card_message`).
5. **existing manual media does not trigger unnecessary acquisition/redownload after approval**
   (`_source_approve_handoff` short-circuits on `media_already_acquired`).

---

## 5. What NOT to reopen

Unless **new runtime evidence** contradicts today's acceptance, do not reopen:
manual intake basic architecture · Reddit downloader acceptance · Twitch downloader acceptance ·
Kick downloader acceptance · operator override semantics · decision persistence ·
same-card decision rendering · no-redownload-after-approval behaviour.

---

## 6. Known non-blocking follow-up

**GHL durable storage: LIVE PASS.** The newly tested Reddit, Twitch, Kick, Instagram and TikTok
assets are present in GHL Media Storage.

**GHL folder routing/organization: OPEN FOLLOW-UP — NON-BLOCKING.**
Historical LOOM folder organization was observed previously, but the mechanism responsible has
**not been conclusively located**. Status is **UNLOCATED / NOT YET RECONCILED** — not "does not
exist". Do not build folder routing during any closeout; do not claim the old mechanism is absent.

---

## 7. Architectural guardrail for future work

**Manual intake and automated discovery are separate trust contexts.**

* **MANUAL** — Bryan deliberately supplied the asset ⇒ *CI informs; Bryan decides.*
* **AUTOMATED DISCOVERY** — LOOM may rank/score to control queue volume and prioritize stronger
  candidates. However, do **not** automatically inherit old `filtered_media`, `video_type_unproven`,
  `FIRE FAIL`, or similar hard-rejection logic as *sourcing policy* merely because those mechanisms
  exist in CI.

When automated discovery is resumed, its filtering/ranking policy must be explicitly reviewed
against: **FIND FIRE CONTENT FIRST → RANK IT → SURFACE RISK/UNCERTAINTY → LET HUMAN POLICY CONTROL
WHAT IS ACTUALLY EXCLUDED.** Do not redesign automated discovery now; preserve the requirement.

---

## 8. Exact next build target (from the existing roadmap — not invented)

* **Current stage/segment:** Stage 2 · **Segment 5 — Multi-Source Moment Mining** (active).
  Segment 6 not started. RECUT on hold.
* **Completed gate:** Manual Intake finalization — **live accepted** across the eight paths in §3.
* **Next build target:** the roadmap order is
  **`finalize Manual Intake` → `timeline-editor capability` → already-authorized launch spine /
  Segment 6`** (see `CURRENT_STATE.md` START HERE and §12.6).
  Manual Intake is now finalized, so the next item is the **missing timeline-editor capability** —
  and it **requires Bryan's explicit authorization before any work starts**.
  *The previous editor acceptance remains locked: do not re-audit, re-test, or add Editor feature
  work, and do not resurrect the old Discord Activity editor architecture or abandoned approaches.*
* **Also still open in Segment 5 (pre-existing, not superseded by today):** the mandatory
  **FIRE-APPROVE → n8n/OpenShorts bridge verification**, the real authenticated long-form YouTube
  run, and the remaining six-platform / FIRE-path proofs. Do not invent a new segment for these.
* **Acceptance criteria already established** for the next task: read `CURRENT_STATE.md` START HERE
  plus the relevant section for that item. Do **not** spend another session auditing already-proven
  work — §3 is the proof.

---

## 9. Regression state

`infra/discord-review` — **333 OK** · `infra/content_intelligence` — **477 OK**
(discord-review = 309 baseline + 24 new engagement-metadata tests; content_intelligence re-run
at the §10 increment on commit `c7eaedd`).

These are **regression protection only** — they are not a reason to reopen accepted architecture,
and they are not a substitute for the live operator acceptance recorded in §3.

---

## 10. INCREMENT — Manual Intake engagement metadata captured (2026-09-26, later)

Commit **`c7eaedd`**, pushed. This does **not** reopen anything in §5; it fixes a defect that
sat underneath the accepted lifecycle (the card's market/engagement fields), not the lifecycle.

**Defect.** Operator-supplied sources reached `#clip-review` with Views/Likes/Comments/age =
`Unavailable` and `Market 0/100`, even though the evidence was free and obtainable through the
extractor path LOOM already runs.

**Root cause.** Manual Intake never captured platform metadata at all: `build_record()` built
`observed_metrics` from the local `ffprobe` alone, and `run_manual_intelligence()` hard-set
`record.publication_timestamp = None`. With no views/likes/comments and no publication time,
`market_evidence` was empty for every manual asset — and the card rendered that **absence** as a
measured `0/100`. Unknown was being displayed as zero.

**Fix (no new provider, no migration, no new gate, no new dependency).**
* `manual_media_intake.fetch_source_metadata()` reuses the EXISTING bounded metadata probe
  (`ytdlp_adapter.build_probe_command`: metadata only, `--skip-download`, no cookies, no auth, no
  paid provider) and maps provider fields onto the keys the existing market policy reads, per
  platform. Reddit keeps its native `score`/`num_comments` and is never given invented views.
  Never raises, never fabricates; a field the platform does not expose stays absent.
* Evidence is durable **twice**: the submission row's `media_reference.source_metadata` and the
  candidate's `moment_evidence.lineage.source_metadata`. Both JSONB, so it survives a CI decline
  before persistence — the Kick `filtered_media` asset had only `lineage` and no `market_evidence`.
* `market_evidence` now carries `evidence_available` / `evidence_coverage` /
  `unavailable_metrics`, so **unknown is distinguishable from a measured zero**. The card prints
  `Market unavailable — no market evidence observed (not a zero score)` with coverage, shows the
  available engagement rather than hiding the section when one field is missing, and still renders
  an observed `0` as `0`.
* Human arbitration unchanged: no hard filter, no FIRE input, no acquisition behaviour.
  **LOOM informs. Bryan decides.**

**What was obtainable for free, measured on the real assets** (existing path): TikTok
**771,600 views / 13,400 likes / 173 comments / 2,632 reposts / 5,513 saves**; Instagram partial
(18,914 likes, 1,149 comments — views need auth); Reddit native score 128 / 16 comments; Kick
2,429 views. Twitch has the existing Helix `/helix/clips` app-token path (not live-probed here).

**Tests.** `discord-review` **333 OK** (309 + 24 new in
`tests/test_manual_engagement_metadata.py`) · `content_intelligence` **477 OK**. No gate was
weakened and no accepted semantics from §3–§4 changed.

**Live.** Adapter restarted after the commit and verified serving `c7eaedd`: PID 71579,
`/health` → `ok`, new symbols confirmed present in the loaded bytecode.

---

## 11. LIVE ACCEPTED — engagement-metadata capture through the real Discord flow (2026-09-26)

Bryan personally submitted a real YouTube URL through the live Manual Intake workflow **after** the
§10 patch and adapter restart. **This is live operator acceptance, not a unit test.**

| Item | Observed on the live `#clip-review` card |
|---|---|
| Source | `https://www.youtube.com/watch?v=eFHfe7AArlE` |
| Views/plays | **2,010,656** |
| Likes | **13,411** |
| Comments | **645** |
| Native demand | 2,010,656 views |
| Age | ~74.34 h |
| Lifetime avg VPH | ~27,047.7 |
| Engagement | ~0.006991 |
| Market score | **100 / 100** |
| Evidence coverage | **3/5** |
| Route | `established` |
| Durable media | appeared in GHL Media Storage as `youtube__eFHfe7AArlE.mp4` |

Bryan then clicked **Approve Clip** on that same card, which visibly persisted:
decision `Approve for Acquisition`, reviewer `passportpetty`, timestamp recorded.

### Scope of this acceptance (read carefully)

It covers the **engagement-metadata / Manual Intake path**: real platform evidence reached the
card, durable media was stored, and human arbitration persisted on the same card.

The reported figures are internally consistent with the §10 contract: engagement `~0.006991`
= (13,411 + 645) / 2,010,656, and coverage `3/5` reflects YouTube exposing no share/save
counters — those two stay honestly **Unavailable** rather than becoming 0.

### Explicitly NOT part of this acceptance

The same asset separately showed `Moment unavailable`, `Content 0`, `Selection 0`, `FIRE FAIL`.
That is a **separate CI / moment-analysis concern**. It is not a failure of this acceptance and
must not be used to reopen the accepted Manual Intake architecture (§3–§5).

### Platform metadata capabilities are unchanged

The §10 table still governs: TikTok full · Instagram / Reddit / Kick partial-native ·
**YouTube = views / likes / comments only** · Twitch via the existing Helix app-token path.
No new platform capability is claimed by this acceptance.

---

## 12. GHL folder routing — archaeology verdict + discovered folder, BLOCKED on one detail

Investigation (read-only except as disclosed): **GHL upload/storage remains LIVE PASS**; only
folder *placement* is at issue.

### The first question answered: did LOOM ever know the folder ID or have a working mechanism?

**No.** The `LOOM` folder id appears in **no commit** (`git grep 6aa61446… $(git rev-list --all)`
→ empty), `folderId` appears **nowhere in history** (`git log --all -S folderId` → empty), and no
commit ever implemented folder targeting. Supporting evidence:

* `adapter.ghl_upload_file()` posts multipart with only `file`, `name`, `hosted` to
  `POST /medias/upload-file` — **no folder field at all**.
* `manual_media_intake.store_durable_media(folder_id=…)` **records** the id but never passes it
  to the upload (its own docstring calls targeting "only attempted when an id is supplied").
* `LOOM_GHL_INTAKE_FOLDER_ID` is whitelisted in `run.sh` and read into `MANUAL_MEDIA_FOLDER_ID`,
  but is **unset in every env file** (`.env`, `.env.bak-loom-ingress-20260921181937`,
  `.env.bak-v12-routing` — zero folder-named keys).
* `loom_media.ghl_folder_id` exists (migration 002) but is **never written by any code**.
* Blueprint §5B specifies `LOOM/{tenant_id}/{...}`, but Segment 5 is still marked **PENDING** —
  the convention was designed, never built.
* No other GHL upload implementation exists (n8n workflows, chrome-extension, browser_flow,
  local-helper): none.

This is a finding about **this repository**, not a claim about anything Bryan may have created
manually in the GHL UI (consistent with §6's caution). The folder is real; LOOM never targeted it.

### The folder IS discoverable through the existing integration

Using the SAME token already in use, and the production `User-Agent` (without it Cloudflare
returns `1010 Access denied`):

```
GET /medias/files?locationId=<loc>&type=folder      -> 200
```

It returns **exactly one** folder named `LOOM`, unambiguous:

| Field | Value |
|---|---|
| `_id` | **`6aa61446df2d015553434919`** |
| name | `LOOM` |
| `parentId` | `null` (top level) |
| createdAt | 2026-09-13T03:11:02.826Z |
| appFolder / isAutoGenerated | `false` / `false` |

Other folders present are unrelated (`Derm 7 `, `Brand Boards`, `Relocated Item`) — no second `LOOM`.

### Root placement is confirmed from the API itself

Every uploaded file (all **64**) returns `parentId: ""` — i.e. **My Media root**, including
`youtube__eFHfe7AArlE.mp4` and the manual-intake TikTok/Kick/Reddit/Twitch assets.

### The blocker (why no code change shipped)

The **upload parameter name for folder targeting could not be established** from the repo, the git
history, GHL's docs (JS-rendered, no extractable content), or read-only API probes. A probe with a
deliberately invalid folder id returned **HTTP 201 and placed the file at root** — so an
unrecognised or wrong folder value fails **silently**. Shipping a guessed parameter would therefore
reproduce this exact bug while looking fixed. Per the standing rule, **do not guess**.

Disclosed: one such probe accidentally persisted a 0-byte `probe.mp4`; it was **deleted**
(`DELETE /medias/{id}?altId=<loc>&altType=location` → `{"deleted":true}`) and the library is back
to **64 files**. No other asset was created, moved, or altered.

### Status — SUPERSEDED BY §13 (resolved in the same session)

**BLOCKED — GHL media-upload folder parameter unverified.** Exact need is one of: (a) authorize a
single controlled self-cleaning probe upload against `6aa61446df2d015553434919`, (b) supply the
field name(s) `POST /medias/upload-file` accepts, or (c) ship the plumbing with post-upload
placement verification so a wrong value fails loudly, then prove it with Bryan's live Discord test.

Regardless of route, final routing acceptance is Bryan's own Discord action — never a test result.

---

## 13. GHL FOLDER ROUTING — CONTRACT PROVEN + CODE READY (2026-09-26)

Bryan authorized **Option 1** (a tightly bounded, self-cleaning live probe). Status now:

**`CODE READY — LIVE ACCEPTANCE REQUIRED`** — not LIVE PASS until Bryan's own Discord submit.

### 1. Proven GHL folder-targeting request contract

The targeting field is the **multipart form field `parentId`**, whose value is the target folder id:

```
POST https://services.leadconnectorhq.com/medias/upload-file
Authorization: Bearer <token>   Version: v3
Content-Type: multipart/form-data

file=<bytes>  name=<filename>  hosted=false  parentId=6aa61446df2d015553434919
```

`folderId` is **NOT honoured** — GHL answers **HTTP 201**, silently ignores it, and stores the asset
in My Media root. That silent-ignore behaviour is why HTTP success can never be treated as placement
proof.

### 2. Probe evidence

Every candidate used the same 1,709-byte disposable `loom-probe-*.mp4`; success was judged ONLY by
the stored record's own `parentId`:

| Probe (multipart fields) | HTTP | resulting `parentId` | verdict |
|---|---|---|---|
| `folderId` only | 201 | `""` (root) | ❌ not the contract |
| `parentId` only | 201 | `6aa61446df2d015553434919` | ✅ **contract** |
| `parentId` only (repeat) | 201 | `6aa61446df2d015553434919` | ✅ contract |
| `folderId` + `parentId` | 201 | `6aa61446df2d015553434919` | ✅ contract |
| `folderId` + `parentId` (repeat) | 201 | `6aa61446df2d015553434919` | ✅ contract |
| `?parentId=` query | 201 | `""` (root) | ❌ |
| `?folderId=` query | 201 | `""` (root) | ❌ |

All four probe records were located via
`GET /medias/files?locationId=<loc>&type=file&parentId=<folder>` and **deleted**
(`DELETE /medias/{id}?altId=<loc>&altType=location` → `{"deleted":true}`).

**Final state: LOOM folder contents 0 · root listing 64 · `count` 64 · zero probe residue.**

One earlier probe (before authorization) accidentally persisted a 0-byte `probe.mp4`; it was also
deleted and is covered by the same final counts.

**Note:** the LOOM folder was EMPTY before this work — every one of the 64 assets was at root, and
the folder's own contents contained only probe files. The folder existed (created 2026-09-13) but
LOOM had never populated it.

### 3. Root cause

LOOM never targeted a folder: `ghl_upload_file()` sent only `file`/`name`/`hosted`, and
`store_durable_media(folder_id=…)` recorded the id without passing it. With the env var also unset,
every upload landed in My Media root by default.

### 4. Files changed

* `infra/discord-review/adapter.py` — folder-aware upload + placement verification
  (`_ghl_list_media_files`, `ghl_verify_placement`, `ghl_upload_file(..., parent_id=None)`,
  `GHL_MEDIA_PARENT_ID`).
* `infra/discord-review/manual_media_intake.py` — passes the folder through and propagates
  placement evidence into the returned durable reference.
* `infra/discord-review/tests/test_ghl_folder_routing.py` — new, 11 focused tests.

### 5. Configuration

`LOOM_GHL_INTAKE_FOLDER_ID=6aa61446df2d015553434919` — the **existing** pattern (already whitelisted
in `run.sh`), now actually consumed. It is read once into `MANUAL_MEDIA_FOLDER_ID` /
`GHL_MEDIA_PARENT_ID` and is the **default target for every LOOM upload**, not just Manual Intake.
The folder id is not a secret; no value is hardcoded in application code. With the variable unset the
request is byte-identical to the previous behaviour (root).

### 6. Post-upload verification (fail-loud)

HTTP success is never treated as placement evidence. After every upload the durable record is read
back and its `parentId` compared to the target:

* match → `placement_verified: true`, `placement_reason: in_target_folder`;
* mismatch → loud `[media] ROUTING FAILURE …`, `placement_verified: false`, with
  `placement_expected/actual/reason` carried into the durable media reference for diagnosis.

The asset is still kept (the durable-media lifecycle is unchanged) but root placement is **never**
reported as folder success. Verification can never fail an upload that already succeeded.

### 7. Tests

Focused: **11 OK** (`test_ghl_folder_routing.py`) covering the proven contract, matching placement,
HTTP-201-but-root → fail loud, missing configuration, and no regression to the upload shape.
Regressions: `discord-review` **344 OK** (333 + 11) · `content_intelligence` **477 OK**.

### 8-12. Repo / runtime state

Committed and pushed; `HEAD == origin/master`; worktree clean except the preserved
`infra/supabase/038_loom_editor_session_tokens.sql` (untouched, SHA1
`3da12826ef96dd05ce089b490ee490e66baf01c0`). Runtime **restarted** (folder config is read at process
start): PID **86696**, `/health` → `ok`, and the folder variable is present in the running process
environment.

### Live acceptance — Bryan's exact action

1. In Discord, post a source in `#manual-intake-📸` and run the `intake-media` command
   (the normal Manual Intake flow — one new source only).
2. Let it complete to the `#clip-review` card.
3. Confirm BOTH:
   * the asset appears **inside the existing `LOOM` folder** in GHL Media Storage (not My Media root), and
   * API evidence: `GET /medias/files?locationId=<loc>&type=file&parentId=6aa61446df2d015553434919`
     lists it, i.e. its `parentId` equals `6aa61446df2d015553434919`.

If the card or logs show `ROUTING FAILURE`, the placement guard worked — report it rather than
retrying blindly.

---

## 13a. GHL FOLDER ROUTING — LIVE PASS (2026-09-26, operator-driven)

Bryan submitted a fresh TikTok source through the real Discord Manual Intake flow and confirmed the
asset appears inside `GHL Media Storage → LOOM`. Verified independently from read-only evidence —
not from the visual confirmation alone:

| Check | Evidence |
|---|---|
| Asset exists | `tiktok_comountainmarine_7671018311492193567.mp4`, GHL `_id 6ab860781f3be2be1bd92a0d`, 4,653,539 B, created 2026-09-27T00:16:56Z |
| Inside the LOOM folder | stored `parentId = 6aa61446df2d015553434919` |
| Durable record matches | `GET /medias/files?…&type=file&parentId=6aa61446…` lists it |
| No routing failure | `ROUTING FAILURE` count in `adapter.log` = **0**; instead `folder placement verified … parentId=6aa61446…` |
| Durable placement evidence | `loom_manual_submissions.media_reference`: `folder_id`, `placement_verified: true`, `placement_expected = placement_actual = 6aa61446…`, `placement_reason: in_target_folder` |
| No duplicate acquisition/redownload | `moment_evidence.lineage.media_already_acquired: true`; **0** acquisition jobs created; reuse guard hit once |
| Root untouched | root still **64** files; LOOM folder holds exactly the one accepted asset |

Bonus confirmation: this same asset also live-proves the §10 engagement capture —
`source_metadata.metrics = {views: 3,500,000, likes: 209,100, comments: 866, shares: 34,300,
saves: 30,782}`, candidate `market_score = 100`, `market_evidence.views = 3,500,000`,
`evidence_available: true`, `coverage: 1.0`, real publication timestamp retained.

**Status: `GHL FOLDER ROUTING — LIVE PASS`.** Do not re-run a live intake to "confirm" this.

---

## 14. CLOSED MILESTONES + DO-NOT-REOPEN LIST

Unless **new runtime evidence directly contradicts** them, these are CLOSED and must not be
rediscovered, re-tested, or reopened:

**Manual Intake** — operator-driven live-proven for Reddit (URL + file), Twitch (URL + file),
Kick (URL + file), Instagram (URL), TikTok (URL), **YouTube (URL)**. Lifecycle accepted end to end:
operator source → `#manual-intake` → `intake-media` → durable GHL media → CI advisory analysis →
durable candidate → exactly one active review card → human decision → durable `loom_source_reviews`
row → same card visibly updated → no unnecessary redownload.

**Engagement metadata** — captured where platforms expose it; publication timestamp retained when
available; **unknown ≠ measured zero**; evidence coverage reported honestly; market evidence survives
durable persistence; YouTube exposes no share/save counters and they are **not fabricated**.

**GHL folder routing** — `POST /medias/upload-file` targets a folder with the multipart field
**`parentId`**; **`folderId` does NOT work** (HTTP 201, silently ignored, stored at root) and neither
does a query-string folder parameter; **HTTP 201 alone is NOT placement proof**; LOOM reads the
durable record back and compares `parentId`; wrong/root placement **fails loudly**; existing root
assets are untouched; the LOOM folder id is `6aa61446df2d015553434919`; folder listing is
`GET /medias/files?locationId=<loc>&type=folder` and folder contents are listed with
`…&type=file&parentId=<folder>`.

**Also closed:** CI advisory semantics for Manual Intake (a low score, `FIRE FAIL`,
`filtered_media`, `video_type_unproven`, `no qualifying moment`, missing market evidence, or
unsupported classification must **never** prevent an operator-supplied asset reaching human review —
LOOM informs, Bryan decides); the age/adult gate removal; no-redownload-after-approval;
one-active-review-card behaviour; automated-discovery ranking/filtering policy (unchanged, and CI
rejections must **not** silently become sourcing policy); the old Cloudinary architecture; and that
**GHL is canonical durable storage**.

### Tenant architecture requirement (record, do not build now)

Bryan observed the existing GHL `LOOM` folder already contains subfolders `Intake Reviews` and
`dreemzy`. **Do not restructure, rename, move, delete, or create tenant folders during this work.**

GHL folder targeting is now a **proven storage primitive**. The eventual durable organization must
stay **tenant-aware**, conceptually `LOOM → tenant_id → lifecycle assets`. The exact hierarchy is
**NOT yet locked**. Critically: **the editor must not depend on browsing GHL folders** — it resolves
media through durable identity/lineage (§8 above).

### Future edit-event extensibility (requirement, do not build now)

Editor V1 is **not** Premiere/CapCut. No SFX, transitions, overlays, or commentary audio are
authorized yet. But the session/timeline representation must stay extensible for future **typed edit
events** (`trim`, `split`, `remove`, `reorder`, `sfx`, `transition`, `overlay`, `commentary`,
captions, hook text).

**Honest state of today's representation:** `loom_edit_corrections` has first-class columns for
`trim_start`/`trim_end` plus `caption_changes` (jsonb) and `crop_reframe_changes` (jsonb), and a
free-form `edl_metadata` (jsonb) — so additional op types can ride in `edl_metadata` and/or additive
columns **without replacing the editor**. The plan's requirement is therefore satisfiable, but only
if V1 extends that representation instead of hard-coding it around trim alone. This is a design
constraint on the next task, not a claim that the capability already exists.

---

## 15. ROADMAP — RECORDED REQUESTS, DELIBERATELY *NOT IMPLEMENTED*

**Status: NOT IMPLEMENTED. NOT DESIGNED. NO CODE, NO TABLES, NO JOBS.**

These two items are recorded so the intent is not lost. They are **not** authorized work, and
nothing in LOOM reads, depends on, or prepares for them today. Do not build them without an
explicit new instruction. Recorded 2026-09-28.

### 15a. Editorial Feedback Learning Loop — RECORDED, NOT IMPLEMENTED

**The eventual intent (conceptual shape only):**

> LOOM-generated edit → operator finishes it in CapCut → compare the BEFORE project against the
> AFTER project → structured, **typed** edit corrections → tenant-specific editing preferences /
> policy → eventually post-publication performance signals.

**Future correction classes to consider** (this is a list of *candidates*, not a schema): `trim`,
`split`, `reorder`, `source`/`reaction switching`, `caption changes`, `SFX`, `transitions`,
`overlays`, `zoom`/`reframe`, `commentary`/`voiceover`, `pacing`/`dead-air removal`.

**Constraints recorded now, so a later design cannot quietly violate them:**

* **Tenant-aware.** Editing preferences are per tenant; one tenant's taste must never become
  another's policy.
* **Not every operator correction is automatically a superior rule.** A single manual change is an
  *operator preference* and evidence of nothing more. A repeatable pattern is stronger evidence, and
  only a pattern later supported by performance signals could ever justify becoming policy. The
  design must keep these three tiers genuinely distinct — **preference ≠ repeated pattern ≠
  performance-supported pattern** — and must be able to decline to learn from any of them.
* **The BEFORE state is already preserved.** `handoff_id` (migration 042,
  `loom_capcut_handoffs`) records the exact immutable handoff specification, the working-media hash,
  the working-timebase origin and the compiler identity. It exists **specifically so a future
  BEFORE/AFTER comparison needs no archaeology** — do not re-derive the before state from source
  files, logs or guesswork later.
* **No return path exists.** The handoff is one-way; nothing imports from CapCut today, and any
  future importer is a separate, separately-authorized project.
* **No speculative tables.** The current provenance record is deliberately the *minimum* structure
  needed to preserve the authoritative BEFORE state. No learning, preference or policy tables may be
  added ahead of an authorized design.

### 15b. Source Mining Intelligence V2 — RECORDED, NOT IMPLEMENTED

**The eventual intent (conceptual shape only):** source-mining / Content Intelligence is expected to
receive a later **intelligence upgrade** once LOOM has real operating evidence to learn from.

**Future inputs to that upgrade** (from the operating record, not from inference):

> discovered source → human approve/reject → selected moment → edit decisions → publication →
> platform performance

…so that discovery can learn **which sources and moments actually become useful inventory, per
tenant**.

**Explicitly NOT in scope now:** do **not** redesign discovery, scoring, acquisition or Content
Intelligence. This item records a future direction only. Today's ranking/filtering policy is
unchanged, and CI rejections must **not** silently become sourcing policy (see §14).

---

# START HERE — 2026-09-29 (late) RECOVERY CLOSEOUT — **BLOCKED**, SAFE TO RESUME

**Outcome: `BLOCKED`.** The compact operator path was not proven tonight. **No behaviour change
was committed.** Full detail: `CURRENT_STATE.md` → `INCREMENT — 2026-09-29 (late)`.

**Frozen state.** Outer **`1343fae` == `origin/master`** · clean except the untracked-and-preserved
`infra/supabase/038_loom_editor_session_tokens.sql` (`3da12826ef96dd05ce089b490ee490e66baf01c0`)
· nested OpenShorts **`7e25c60`**, pre-existing unrelated changes untouched · live adapter PID
**31120**, digest **`0d090994514a…`** == on-disk `adapter.py`.

**Last working PRE-CHOOSER commit: `c690137`** (2026-09-28 17:41). The chooser landed in
`c166bff` / `c919219` / `0405f08` and inserted a Discord offer→click round trip, with an
in-process ref as the only link, in front of the proven executor — gating every windowless
submission, URL and upload alike.

**Nothing was restored or flagged off in a commit.** The `MI_CHOOSER_ENABLED` (default OFF,
chooser code retained) recovery was implemented, compile-verified, then reverted uncommitted
because flipping a DEFAULT without its test story is not a safe close. **Do it first tomorrow,
with `test_manual_segment_choice.py` pinned to the flag ON.**

**Acceptance: none.** The compact acceptance run was killed mid-execution; no IDs exist. Do not
treat any earlier audit, resolver test, `acquire_url_media` success or local file as acceptance.

**Next action:** reproduce the durable-store write failure for the retained 30.8 MB clip at the
transport layer, with a valid token and one timed 20 MB vs 24 MB comparison. Retained bytes:
`/var/folders/zp/m0yvx5fd3x385sjm9gqqz2l40000gn/T/loom-manual-intake-tsu6jruu/clip_01M33XASMP29XTQ1QK0EV7TXDC.mp4`
· 30,782,671 B · sha256 `e8a303d41b3acbd58e017bd75d302c42761a2f60fc6a84c75724df1af8cc2360`.

**Retracted — do not cite:** the "~15 s upload cut" and the "persistent GHL 401". Both came from a
masked `Bearer ***` placeholder reaching the wire, not from the product. The token works (200s);
the log has zero GHL auth failures.
