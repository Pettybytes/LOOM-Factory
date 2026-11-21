# ASTRA — LOOM start file (2026-09-29)

Self-contained handoff. Read this and nothing else to begin. Everything below is
evidence-linked; anything not established is labelled **UNKNOWN** rather than filled in.
Credentials, tokens, signed URLs and private headers are deliberately absent.

## 1. Objective

Diagnose and repair the **GHL durable write** for the retained Kick clip
(`clip_01M33XASMP29XTQ1QK0EV7TXDC`) **without reducing quality, without changing canonical
storage, and without inventing a second durable store**. Then run compact operator
acceptance (one small Whole, one reachable bounded Segment, one supplied upload, one
editor reopen). **The upload failure's mechanism is UNKNOWN.** No hypothesis is
established tonight; the last two diagnoses (a 15-second egress cutoff, a GHL 401) were
retracted as test artifacts.

## 2. Verified state

| Fact | Value |
|---|---|
| Outer HEAD == `origin/master` | `ef1743d81c73da5c1ab18a466fa20176db3e78de` |
| Worktree | clean except `?? infra/supabase/038_loom_editor_session_tokens.sql` |
| Protected `038` | SHA1 `3da12826ef96dd05ce089b490ee490e66baf01c0`, **untracked, unstaged, unapplied** |
| Nested OpenShorts HEAD | `7e25c6050b16877bc3e670871a3f7b4e0740a716` (`loom/factory-mvp-baseline-fixes`), pre-existing unrelated changes preserved |
| Runtime | adapter PID **31120**, started 01:19:30; `/health` digest `0d090994514a5d96f5b21627f49a7c6fb730d8b933e145b8e98cf8b88b6278a6` == on-disk `infra/discord-review/adapter.py` |
| Backup tags | `loom-backup-2026-09-29-manual-intake`, `loom-backup-2026-09-29-working-intake` (**recovery anchors for the code HEAD only — not intake proof**) |

**Code committed ≠ live accepted.** Committed and unit-tested: format-selector fallback,
correlation tracing, Whole-resume route repair, edited-version resolver, gone-source
refusal. **Live accepted: nothing** (see §8).

## 3. Failure trace — latest Kick Whole attempt (02:14 Detroit)

Source `https://kick.com/deenthegreat/clips/clip_01M33XASMP29XTQ1QK0EV7TXDC`, correlation
ref `DAWYa4pxkI`, source id `clip_01M33XASMP29XTQ1QK0EV7TXDC`, `mode=url`.
Sanitized `infra/discord-review/adapter.log`:

```
intake-trace  stage=intake_received           mode=url
intake-trace  stage=executor_start            platform=kick source_id=clip_01M33... resumed=false
manual-intake operator choice offered         ref=DAWYa4pxkI duration=30.0 whole_allowed=True
intake-trace  stage=choice_offered            kind=clip duration_s=30.0 whole_allowed=True ref=DAWYa4pxkI
intake-trace  stage=choice_click_received     action=whole ref=DAWYa4pxkI resume_state=present
intake-trace  stage=resume_resolved           action=whole ref=DAWYa4pxkI
intake-trace  stage=executor_start            resumed=true ref=DAWYa4pxkI
manual-intake whole source chosen by the operator; acquiring the source as provided
intake-trace  stage=acquisition_start         mechanism=local:yt-dlp window_mode=whole_item
intake        platform=kick adapter=local:yt-dlp state=implemented_not_proven proven_modes=['clip']
intake        pre-flight start_height=1080 estimate=None reason=estimate_unknown
intake        whole-source acquisition allowed (source=30.0s)
manual-intake source metadata available=['likes','views'] unavailable=['comments','saves','shares']
media         GHL upload attempt 1/5 failed for kick__clip_01M33....mp4: <urlopen error [Errno 32] Broken pipe> - retrying in 1.0s
media         GHL upload attempt 2/5 failed ... retrying in 1.5s
media         GHL upload attempt 3/5 failed ... retrying in 2.2s
media         GHL upload attempt 4/5 failed ... retrying in 3.4s
media         GHL upload gave up for kick__clip_01M33....mp4 after 5 attempts: <urlopen error [Errno 32] Broken pipe>
intake-trace  stage=route_failed              failed_stage=durable_store_ghl code=DURABLE_STORE_FAILED ref=DAWYa4pxkI
manual-intake failed stage=durable_store_ghl code=DURABLE_STORE_FAILED
              reason='ghl_upload_failed reason=<urlopen error [Errno 32] Broken pipe>'
              frames=adapter.py:3758:_manual_intake_execute;manual_media_intake.py:963:store_durable_media
```

**Read this precisely.** Whole-click routing reached acquisition and storage:
`choice_click_received` → `resume_resolved` → executor → acquisition succeeded and the
artifact passed the post-download gates (a 30,782,671-byte working source exists). It then
failed **only** at `durable_store_ghl`. **No GHL HTTP response was ever received** — every
attempt died as a socket write error (`[Errno 32] Broken pipe`) with no status code.

**Persisted:** nothing durable, no submission row, no candidate row, no `#clip-review`
card. **Not persisted:** the durable copy. The working source was retained locally
(retry-safe by design).

The same failure recurred earlier at 00:47 with ref `HmRYrQZCM3` (identical 5/5 pattern),
so this is reproducible, not a one-off.

## 4. Minimal code map (12 files max)

| File | What matters |
|---|---|
| `infra/discord-review/adapter.py` | `_handle_button` (MI branch → `MI_ACT_WHOLE`/`MI_ACT_SEGMENT`), `_mi_choice_worker`, `_manual_intake_execute` (the executor; raises at ~:3758), `ghl_upload_file` (~:2222), `_mi_trace`, `_post_source_card`, `register_edited_asset_rpc`, `resolve_durable_bootstrap_diag`, `resolve_source_lineage_bootstrap` |
| `infra/discord-review/manual_media_intake.py` | `acquire_url_media` (ladder → finalize → validate), `store_durable_media` (~:963, ownership of `DURABLE_STORE_FAILED`), `build_media_submission`, `probe_source_duration_diag`, `verify_artifact_integrity`, `find_existing_media` |
| `infra/acquisition/ytdlp_adapter.py` | `build_download_command`, `build_format_selector` (+ `UNTAGGED_FALLBACK_SELECTOR`), `run_ytdlp_command` (timeout 600 s), `is_source_unavailable` |
| `infra/acquisition/source_window.py` | `validate_acquisition_mode` (the ONE whole-vs-window authority), `validate_window` |
| `infra/acquisition/media_validation.py` | `validate_finalized_media` / tail-decode integrity gate |
| `infra/discord-review/source_review.py` | `fetch_candidate_by_fingerprint`, `source_lineage_bootstrap`, `edited_version_bootstrap`, `fetch_edited_version_rows` |
| `infra/discord-review/manual_intake_intelligence.py` | `run_manual_intelligence` (candidate + boundary plan) |
| `infra/supabase/043_register_edited_asset_clean_source.sql` | version registration (render row + immutable trim) |
| `infra/supabase/037_loom_edited_asset_storage_status.sql` | why an edited version records `storage_status=not_applicable` |
| `infra/discord-review/tests/test_week…` → `test_whole_choice_resume_route.py`, `test_edited_version_and_gone_source.py`, `test_intake_route_correlation.py` | the focused seams |

**Uploader request shape** (`ghl_upload_file`): `POST
https://services.leadconnectorhq.com/medias/upload-file`; `multipart/form-data` with
`file` (part `Content-Type: video/mp4`), `name`, `hosted=false`, `parentId=<LOOM folder>`;
headers `Authorization: Bearer <PIT>`, `Version: v3`, `User-Agent:
LOOMReviewAdapter/1.0`; `urlopen(req, timeout=60)`; **file read once into memory** and
re-framed per attempt with a fresh boundary. **Retries:** `GHL_UPLOAD_ATTEMPTS`
(default 5), backoff `min(8, 1.5**attempt)` → 1.0/1.5/2.2/3.4 s. **Failure ownership:**
`store_durable_media` (`manual_media_intake.py:963`) raises `MediaIntakeError
DURABLE_STORE_FAILED` carrying the transport reason; the exception surfaces at
`adapter.py:3758`. Placement is verified by read-back (`parentId`), and HTTP success is
never treated as placement proof.

## 5. Relevant history

- **Last working pre-chooser route:** `c690137` (2026-09-28 17:41); Manual Intake
  acceptance commit `925a834`. Pre-chooser, a windowless submission went **straight** into
  `acquire_url_media`; the shared gate decided; Segment existed only as `START:`/`END:`
  text; **no cross-message state existed**.
- **Chooser:** `c166bff` (offer/Whole/Segment modal), `c919219`, `0405f08` (choice for
  ALL media). It added the Segment **modal** (new capability) and made the executor
  reachable only from a later click, coupled through in-process `_MI_PENDING`.
- **Format-selector regression + repair:** `eeff60c` introduced a height-bounded selector
  that excluded a manifest with no resolution metadata; `d60edee` added
  `UNTAGGED_FALLBACK_SELECTOR`. **Proven by real probe and repaired** (Kick clip
  then acquired, 30,782,671 B).
- **Whole loop repair:** `4124252` — a resumed Whole choice re-offered forever instead of
  acquiring. **Proven live and repaired.**
- **Edited-version resolver repair:** `1343fae` — `render-1.0` was refused as
  `version_not_found` although its media and trim were durable.
- **Correlation logging** `e2e130b`; upload-route tests `8aaccd5`; docs `080067c`,
  `ef1743d`.
- **None of these touch the GHL write path.** The format-selector defect is upstream of it
  (it determines *what* is downloaded); the loop and resolver defects are unrelated.

## 6. False leads to avoid

1. **The GHL 401 was never real.** It came from a literal masked placeholder (`Bearer ***`)
   reaching the wire. With the credential read safely, valid PIT calls returned **200**;
   `adapter.log` contains **zero** GHL auth failures (all 212 `401`s are LOOM's own
   `/api/config`, `/discord/interactions`, editor surfaces).
2. **The "fixed 15-second egress cutoff" is retracted.** The test producing it sent the same
   corrupted header; its numbers are void.
3. **`hosted=true + fileUrl` is disallowed** — it retains an external reference, not
   GHL-owned bytes.
4. **the legacy VPN tunnel was intentionally removed from LOOM.** Do not reintroduce it, and do not
   blame leftover macOS VPN entries without evidence.
5. **Do not treat the earlier 33 MB success or the 24–31 MB failures as proof of a fixed
   file-size cap.** A 33 MB upload succeeded on 2026-09-28 18:47 through the same path; the
   boundary is **UNKNOWN**.

## 7. Astra's first investigation (narrowly scoped)

Using the **retained file** and **existing logs only**, compare the **last successful GHL
upload** against the **failing production uploader** on: request construction, headers,
multipart streaming behaviour, client timeouts, proxy/env path, and the exact socket/HTTP
outcome. **Identify the first differing condition** before proposing any fix.
**Do not repeat large uploads** until that comparison yields a specific hypothesis.
Preserve **GHL as canonical**. Verify any eventual write by **independent retrieval +
byte count + SHA-256**.

## 8. Acceptance ledger

| Path | Status | Evidence |
|---|---|---|
| Whole (URL) | **FAIL** | 02:14 Kick clip: acquisition succeeded, `durable_store_ghl` 5/5 `Broken pipe`, no durable copy |
| Segment (modal) | **NOT TESTED** | the only segment attempt (`ref=IcADjlEIIA`) hit a **dead** VOD (`[kick:vod] … HTTP 404`), so it proves nothing about segment |
| Upload (supplied media) | **NOT TESTED** | — |
| YouTube `render-1.0` reopen | **PARTIAL** | resolver returns media + trim `0.0→20.108481`; **browser reopen UNVERIFIED** |
| Kick large-file storage | **FAIL** | same as Whole; retained 30,782,671 B |
| CapCut AUTO | **NOT TESTED** | parked |

A review card alone is **not** durable acceptance.

## 9. Tomorrow's exact order

1. Resolve or isolate the **GHL write** (§7). 2. One **small** Whole. 3. One reachable
bounded **Segment**. 4. One **supplied upload**. 5. Reopen the existing YouTube
**`render-1.0`**. 6. Only then resume **CapCut AUTO**. No full-platform audit.

## 10. Open operational issue — probe URL / Cloudflare cache

Contradictory claims exist about the exact temporary probe URL being Cloudflare-cached.
**The record naming that exact URL was not found tonight** in the repository, the workspace
notes, or `adapter.log` (searched `assets.cdn.filesafe.space/...` with probe/tiny markers,
plus probe-named artifacts). Therefore:
- exact URL: **UNKNOWN** (not established from existing records — do not create a new probe)
- last verified status: **UNKNOWN**
- purge still necessary: **UNKNOWN**

Evidence held about the edge, none of which is a `HIT`: `https://app.loomz.stream` → `/`
and `/edit` = `cf-cache-status: DYNAMIC`; `/media/*`, `/outputs/*`, `/recut_*.mp4` = 404
`MISS`; `/api/media/*` = 401 `BYPASS`. Live ingress (verified from the running
configuration): Cloudflare → Caddy `:8081` → `8090` (interactions/editor/`api/*`) and
`:5175` (SPA); `:8082` → n8n relay; `cloudflared` = quick tunnel + named tunnel;
`legacy vpn status` = stopped. The Caddy config read inside the running container matches the
repo file and has **no file/media handler**.

## Appendix — retained artefact

```
/var/folders/zp/m0yvx5fd3x385sjm9gqqz2l40000gn/T/loom-manual-intake-tsu6jruu/clip_01M33XASMP29XTQ1QK0EV7TXDC.mp4
30,782,671 bytes · mtime 2026-09-29 00:47:44
sha256 e8a303d41b3acbd58e017bd75d302c42761a2f60fc6a84c75724df1af8cc2360   (matches the reported hash)
```
The 02:14 attempt retained its **own** working copy in a separate `loom-manual-intake-*`
temporary directory; **that path was not enumerated tonight — UNKNOWN.** No large uploads
were retried during this handoff.
