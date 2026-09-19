# NEXT SESSION HANDOFF — LOOM Editor Closeout

**Supersedes everything before it for resuming work.** Read this first, then `LOOM_SOURCE_OF_TRUTH.md`, `CURRENT_STATE.md`, `HANDOFF.md`.

---

## 0. Repository state

| | |
|---|---|
| Repo path | `/Users/user/Desktop/LOOM-Factory` |
| Branch | `master` |
| FINAL_HEAD | `10ac46f74d5ca0bac80f5cfe1e703738d7783fa7` |
| REMOTE_HEAD | `10ac46f74d5ca0bac80f5cfe1e703738d7783fa7` (local == remote ✅) |
| Worktree | **clean** |
| Full tests | **974** (acquisition 578 · content_intelligence 55 · local-helper 86 · discovery 87 · discord-review 168) |
| Failures | **0** |
| Test runner | `python3 -m unittest discover -s tests` from **inside** each `infra/<suite>/`. **Not pytest** (not installed, not needed). Do NOT pass `-t .` — it forces package semantics and raises "Start directory is not importable". |

Commits this session, all pushed: `8df2054` (Twitch connector) · `88814bd` (Caddy access logging) · `9973fb7` (GHL upload retry) · `8b89ce2` (truthful card contract + idempotent retry + flushed log) · `10ac46f` (tier-aware oversize fallback).

---

## 1. Runtime topology (as of session close)

| Component | Detail |
|---|---|
| Discord adapter | `python3 adapter.py`, binds **127.0.0.1:8090**. PID changes on restart. |
| Supervisor | `./supervise.sh` (restart loop) → runs `./run.sh` which sources `~/.openclaw/.env` and `exec`s the adapter. **Killing the adapter is the sanctioned restart** — the supervisor brings it back in ~3s. |
| Local helper | `helper.py` on 127.0.0.1:8787 (`/health` → `{"ok": true, "service": "loom-local-helper"}`) |
| Caddy relay | **runs in Docker** (`loom-tunnel-proxy`, caddy:2-alpine). Host loopback `:8081` → adapter, `:8082` → n8n callback relay. Caddyfile at `infra/tunnel-proxy/Caddyfile` (bind-mounted read-only). |
| Dashboard/editor frontend | Vite app at `openshorts/dashboard`, dev server `:5175` (Caddy's default `handle` routes everything unmatched there). |
| Logs | `infra/discord-review/adapter.log` (adapter stdout+stderr), `supervisor.log`, and `docker logs loom-tunnel-proxy` (Caddy access log, enabled this session). |

### Discord ingress
* **Current endpoint:** `https://users-macbook-pro-1.taild000b2.ts.net/discord/interactions` (**Tailscale Funnel**)
* `TAILSCALE_ENDPOINT_VALIDATION = PASS` — Discord's real PING returned **200** at 17:10:21 from egress IP `35.196.132.85`.
* `CLOUDFLARE_ENDPOINT_VALIDATION = NOT EXERCISED BY DISCORD`. A $0 Cloudflare **Quick Tunnel** (`cloudflared tunnel --url http://127.0.0.1:8081`, PID was 43004, hostname was `magical-learners-known-superior.trycloudflare.com`) is **temporary — the hostname changes on restart**. Discord never used it; only manual tests did.
* The earlier Tailscale verification FAILURE is **unexplained**. A Caddy reload correlates in time but is not proven causal (manual curls reached the adapter before *and* after it).
* **The endpoint was cleared and re-saved during this session.** If verification ever fails again, the safe procedure is: clear the Interactions Endpoint URL → Save → paste the exact same URL back → Save (forces Discord's PING).
* No further ingress work is required. Do not reopen this.

### Diagnostics added (do not remove)
`infra/tunnel-proxy/Caddyfile` now has a `log { output stdout; format json }` block in the `:8081` site. This is what made the final diagnosis possible — without it, "request rejected" and "request never arrived" are indistinguishable.

---

## 2. Locked architecture decisions (do not relitigate)

1. **Caddy is retained deliberately.** `adapter.py`'s `/discord/card` handler has **no authentication at all**. Caddy's `@card` matcher is the ONLY thing gating it:
   ```
   @card { path /discord/card* ; header Authorization "Bearer {$LOOM_TUNNEL_TOKEN}" }
   ```
   Pointing a public tunnel directly at `:8090` would let anyone post arbitrary review cards. Keep Caddy in the path.
2. **Large media stays local.** Never traverse n8n Cloud or the public funnel.
3. **Callback-first orchestration.** No continuous polling.
4. **Human approval before distribution.** Decisions: Approve / Reject / Hold.
5. **Evidence discipline.** Label claims: Runtime verified · Static verified · Bryan reported · Bentley reported · Planned · Deferred. Construction is not runtime proof.
6. **Ultralytics AGPL exposure** is confined to the nested `openshorts/` chassis (`requirements.txt:3`, `main.py:13` — the person-detection fallback). It is **not** imported anywhere in LOOM-Factory proper. Do NOT casually remove it; the fix direction is replacing it with MediaPipe (Apache-2.0) inside the chassis, as its own ticket.

---

## 3. Asset-lineage contract (verified this session)

```
loom_media.asset_version            render-1.0 → render-1.1 → …
loom_media.previous_asset_version   lineage pointer; the ORIGINAL render has NULL
loom_media.canonical_url            burned final (version-specific)
loom_media.clean_source_url         clean PRE-hook/pre-subtitle master
                                    (property of the ORIGINAL render; deliberately
                                     NOT version-scoped — later versions re-render from it)
loom_edit_corrections               per target_asset_version: trim_start/trim_end,
                                    caption_changes (text, style, blocks, effect,
                                    bg_color, position, alignment, font_name),
                                    crop_reframe_changes (framing)
loom_scores.suggested_hook          ← the editor's Hook source of truth
loom_scores.suggested_caption       ← the editor's Social Caption source of truth
loom_review_tokens                  review AND editor tokens (status pending/consumed/expired)
loom_reviews                        terminal decisions
```
`resolve_clip_clean_source(tenant_id, clip_id)` filters by **tenant+clip only, not asset_version** — by design.

**Editor bootstrap contract** (`resolve_durable_bootstrap`) proven payload:
`hook` ← `loom_scores.suggested_hook` · `social_caption` ← `loom_scores.suggested_caption` · `media_url` ← `canonical_url` · `clean_source_url` · `duration_seconds` · `source_range {start,end}` (correction trim, else 0→duration) · `framing` ← `crop_reframe_changes.framing` · `captions`/`transcript` ← `caption_changes.blocks` · `scenes` = `[]` (**contract default — the `/scenes` 409 is EXPECTED/NON-BLOCKING**) · `title` ← not yet populated by any fixture.

---

## 4. Discord Edit lifecycle — status

| Stage | Classification | Evidence |
|---|---|---|
| Endpoint validation (PING) | ✅ COMPLETE | 200, `{"type":1}`, 0.0373s, real Discord IP |
| ACK | ✅ COMPLETE | unit `test_ack_first.py` PASS; live interaction returned 200 |
| Edit interaction delivery | ✅ COMPLETE | 200 from a second Discord egress IP `35.237.4.214` |
| Editor-link generation | ✅ COMPLETE | `[edit] external editor link issued for clip …` |
| Completion response | ✅ COMPLETE | **520** later corrected: `/discord/editor/complete` returned **HTTP 200** (see §7 gotcha) |
| Card retry / idempotency | ✅ COMPLETE | `LOOM_CARD_SEND_ATTEMPTS` (default 3), guarded by `check_existing_card` |
| Oversize fallback | ✅ COMPLETE | tier-aware poster fallback, commit `10ac46f` |
| Flushed logging | ✅ COMPLETE | `log_message` now `flush=True` |
| New asset version | ✅ COMPLETE | `render-1.1` created, `prev=render-1.0` |
| Original preserved | ✅ COMPLETE | `render-1.0` untouched |
| Corrections persisted | ✅ COMPLETE | trim `(0, 8.868005)` + caption/reframe |
| Re-review delivery | ✅ COMPLETE | card `1550988492845228043`, `🎬 FINAL REVIEW · EDITED CUT`, `v1.1`, lineage `v1.0 → v1.1` |

---

## 5. Editor implementation — IMPLEMENTED, HEADER ACCEPTANCE PENDING

**`EDITOR_IMPLEMENTATION = IMPLEMENTED`** (openshorts commits `416e12e`
Hook rich text + one authoritative preview, `8895050` Header parity, `eef684b`
Header defect fixes).
**`HOOK_VISUAL_ACCEPTANCE = PASS`** (Bryan reported: Hook renders in the primary
preview, including multiple colours).
**`HEADER_VISUAL_ACCEPTANCE = ROUND 4: PENDING_HUMAN`** — rounds 1-3 fixes stand
(selection works, region/padding accepted, preview geometry parity). Round 4
adds the optional dedicated black Header region.

**"What previously controlled the black region" (asked, answered): nothing.**
No field or layout semantic ever existed — `black_region`, `top_region`,
`header_region`, `region_mode`, `headerRegion` return zero hits in source, and
nothing in git history. The old black band above the video was an EMERGENT
artifact: the main preview box had no aspect ratio (`lg:aspect-auto lg:h-auto`)
and took its height from the taller sidebar, so the 9:16 video was letterboxed
inside it and the header band (a % of that taller container) landed in the
letterbox. `41c8d9c` pinned the frame to the video to fix geometry parity, which
removed the letterbox and with it the accidental band. **No prior state existed,
so nothing was restored — the mode is a new, explicit, persisted replacement.**
Contract: `title_overlay.region = 'overlay' | 'dedicated'` in
`lib/overlayLayout.js`; `'overlay'` is the default and is bit-identical to the
accepted geometry; the Hook is positioned relative to the VIDEO AREA in both
modes, which is what makes collision structurally impossible.

**NEXT (after this re-test passes): FINAL RENDER PARITY.** The Editor is NOT
complete until the actual rendered MP4 is proven to contain the same Header/Hook
text, ranges, colours, fonts, relative sizes and placement seen in the preview:
editor state -> draft/EDL -> render -> MP4 -> new asset_version -> GHL canonical
-> Discord re-review card. **`dedicated` mode additionally changes the render
composition** (the canvas becomes taller than the video), so the renderer must
honour the mode rather than assuming a 9:16 frame. Preview-only success is not
final acceptance. Do not start Segment 5B until render parity is runtime-proven.

**Round-3 root cause — preview geometry parity (measured, not guessed).** The
media is 720x1280 (exactly 9:16), but the two preview containers were different
shapes: the modal's was a real 9:16 box, while the main preview used
`aspect-[9/16] lg:aspect-auto lg:h-auto`, so on desktop its height came from the
tall sidebar (~420x1000). The 9:16 video was therefore letterboxed ~126px top
and bottom in the MAIN preview only, and the header band (0-14% of the
CONTAINER) fell mostly into that black bar ("too high") while the Hook at 20%
landed just under the video's top edge ("on the boundary"). Same normalized
values, different frames.

Fixed by `PreviewSurface.jsx` as the ONE surface used by the primary preview and
both range modals: the frame's aspect ratio is pinned to the video's own aspect
and capped by WIDTH only (a max-height would break the aspect and reintroduce the
divergence). `overlayLayout.js` exposes `FRAME_ASPECT`, `frameRect()`,
`framePoint()` and `normalizedPlacement()` so the transform is explicit and
tested. **Do not reintroduce `lg:aspect-auto`/`max-h` on a preview container.**

Round-2 root causes (both fixed, both pinned by tests):
- **Inline Header field crashed the editor.** `ManualTrimEditor` read
  `e.currentTarget.selectionStart` INSIDE a deferred state updater; React nulls
  `currentTarget` after dispatch, so the updater saw `null` ->
  "Cannot read properties of null (reading 'selectionStart')" -> error boundary.
  The modal fields were fine because they read a ref BEFORE `setState`. Fixed
  with `readSelection()` (null-safe, refuses to touch non-INPUT/TEXTAREA) and an
  eager snapshot; no blanket try/catch.
- **Header crowded the region boundary.** Added `HEADER_SAFE_INSET_PCT`,
  `headerUsableBand()`; the default anchor is now CENTRED in the usable band and
  every anchor is clamped inside it, so explicit choices cannot breach padding.
  Region clipping means a large rich-text range cannot redefine the geometry.

**NEXT (after this re-test passes): FINAL RENDER PARITY.** The Editor is NOT
complete until the actual rendered MP4 is proven to contain the same Header/Hook
text, ranges, colours, fonts, relative sizes and placement seen in the preview:
editor state -> draft/EDL -> render -> MP4 -> new asset_version -> GHL canonical
-> Discord re-review card. Preview-only success is not final acceptance.
**Do not start Segment 5B until render parity is runtime-proven.**

Bryan's first Header acceptance failed on two counts, both root-caused:
1. **Selective Header styling did not work.** `HeaderModal` re-synced the
   selection from `onBlur`; clicking a swatch blurs the textarea and browsers
   (WebKit) then report `selectionStart/End` as `0/0`, so the live range was
   wiped and the patch fell through to the BASE style — the panel said "no
   selection" while the whole header changed colour. Fixed with
   `lib/rangeSelection.js` (sticky `keepSelection`), `preventDefault` on swatch
   mousedown so controls cannot steal focus, no blur sync at all, and the inline
   header field's selection is now carried into the modal.
2. **Header geometry regressed into the video region.** There was no placement
   contract — two independent frame percentages with nothing confining the
   Header. Fixed with `lib/overlayLayout.js`: HEADER_REGION 0-14% (clipped band),
   HOOK_REGION 16-100%, separate anchors per element. The Header box takes no Hook
   input and vice versa, so neither can move the other.

**Architecture rule now enforced:** rich text model, normalization, styling
controls and per-run rendering are SHARED; placement/geometry and visibility
semantics are SEPARATE per element.

Editor scope now covers BOTH visual text elements on one model:
`lib/richText.js` (was `hookRichText.js`; the model was always generic),
`RangeStyleControls.jsx` (the ONE range-styling UI, shared by the Hook modal and
the new `HeaderModal.jsx`), and `TextOverlayPreview.jsx` rendering per-run style
for each element. A span `size` is a MULTIPLIER of the element's base size, so
the Header's px targets and the Hook's S/M/L both work off one field; legacy
`'S'/'M'/'L'` spans still normalize. `hooks.py` gained `_size_multiplier` —
without it a numeric span size hit a dict keyed by `'S'/'M'/'L'`, fell back to
1.0, and silently no-op'd (styled in preview, unstyled in the burn).

**Known gap (stated, not hidden):** the Header has **no server-side (ffmpeg)
burn path at all** — `title_overlay` appears in no Python file. The Header is a
preview/Remotion-layer concept, and its range styling is carried on the durable
record (`edl_metadata.header_rich`) so nothing is lost, but the Remotion
`TitleOverlay.tsx` / `TitleOverlayConfig` were **not** extended with runs, and no
caller feeds a header through a render today. If a burned Header is ever wanted,
that is new work, not a regression.

⚠️ **Where the editor code actually lives:** `openshorts/` is a **separate nested
git repo** (own remote `mutonby/openshorts`) and is **gitignored by LOOM-Factory**.
`ManualTrimEditor.jsx`, `HookModal.jsx` and friends are tracked THERE, not in
LOOM-Factory — `git status` in LOOM-Factory will never show them. Branch:
`loom/factory-mvp-baseline-fixes`, no upstream configured; editor commits are
local-only (14 unpushed at time of writing) — pushing is a deliberate decision,
not an oversight.

Evidence (labels per §2.5):
- **Static verified** — `vite build` inside the `openshorts-frontend` container is
  clean; `app.py` and `hooks.py` compile.
- **Test verified** — 52 `node --test` cases in `dashboard/src/lib/*.test.mjs`;
  22 unittest cases in `openshorts/tests/test_hook_rich_text.py`; the infra
  baseline is **974/974 unchanged** (my changes are all in the nested repo, so the
  LOOM-Factory total did not move — that is the correct result, not a skipped run).
- **Runtime verified** — an editor session was minted for
  `loom-edit-fidelity:0@render-1.1`; `/discord/editor/exchange` returned the
  prepared draft (hook + 4 rich-text spans), `/discord/editor/bootstrap` returned
  200 with media/clean URLs, and `/edit` served 200 from the public funnel origin.
- **NOT verified** — the human visual acceptance (Header + Hook actually rendering
  with the range colours on screen). That is Bryan's one click.

What changed (accepted plan items 1-8): one shared overlay surface
(`TextOverlayPreview.jsx`) plus one draft→preview conversion
(`lib/editorPreviewState.js`), consumed by the primary preview AND the Hook modal;
one span model (`lib/hookRichText.js`); shared looks/geometry (`lib/hookStyles.js`);
the server burn honours spans (`hooks.py` `build_style_map`, `/api/hook` accepts
`spans`/`base`, recorded in `edl_metadata`); the draft persists
`hook_rich`/`hook_entrance`. Clip Title and Social Caption remain non-visual.

### Root cause (established, exact)
```
ManualTrimEditor.jsx:728   <video ref={videoRef} src={clipUrl} controls playsInline   ← bare <video>, NO overlay layer
ManualTrimEditor.jsx       RemotionPreview usage: NONE
HookModal.jsx:105          <RemotionPreview videoUrl hook={hookConfig} subtitles={…} /> ← separate renderer
```
`/edit` → `main.jsx:82` → `LoomActivityRoot.jsx` → **`ManualTrimEditor.jsx` (1083 lines)** is the real editor. `ClipEditor.jsx` (1613 lines) contains **no hook code** and is NOT the editor — do not confuse them.

The primary preview cannot show the Hook because it has **no overlay surface**; the modal renders a separate Remotion overlay. Two independent visual implementations, hence drift (`top · M · yellow · 5s` in the control, nothing in the frame).

### Required work (accepted plan — implement, do not re-derive)
1. **One authoritative preview** — a shared overlay component driven by current edit state, consumed by the main preview, the Hook modal, and ideally the render contract. **Do NOT patch around it by copying Hook state into the bare `<video>`.**
2. **Live sync** — Hook and Header changes must appear immediately; no save/render/refresh/reopen.
3. **Four text concepts, made explicit** in code and UI:
   * **A. Clip Title** (`THE JIGGLE`) — metadata; NOT burned automatically.
   * **B. On-video Title/Header** (`THE JIGGLE THE BOUNCE THE JUICE`) — visual overlay.
   * **C. Hook** (`LOOK MA.. NO HANDS!`) — separate overlay with own text/style/position/size/timing.
   * **D. Social Caption** (`editor hydration acceptance`) — publishing metadata; must NOT render on the video.
   Preserve old persisted state; do not destroy metadata to get cleaner semantics.
4. **Selective colouring** — whole-text + selected-word now; structured spans/range representation so character-level is possible later; backward-compatible with plain-string hooks.
5. **One state contract** — one draft object / deterministic conversion owning clip title, header, hook, hook style/timing, rich-text spans, social caption, subtitles, trim, framing.
6. **Preserve the lifecycle** and the lineage contract in §3.
7. **Render parity** — semantics (content, colours, placement, style, timing, trim, framing) must match; centralize shared config rather than two interpreted formats.
8. **Oversize-image fallback** for editor previews — the card path is done (`10ac46f`); the editor/preview side is not.
9. **Tests** — shared preview state construction, Hook/Header propagation, Clip Title distinct from Header, Social Caption non-visual, hook style/timing persistence, rich-text span persistence, backward compat with plain strings, lineage, completion error behaviour, card retry/idempotency, no hydration regression. There is **no JS test runner** in this repo — decide whether to add one or assert the contract from Python; report the choice.
10. **One visual acceptance** — prepare a state (Clip Title `EDITOR PREVIEW ACCEPTANCE`, Header `HEADER PREVIEW`, Hook `LOOK MA.. NO HANDS!`, multiple colours, known trim/framing) and give Bryan exactly ONE instruction. `EDITOR_VISUAL_ACCEPTANCE = PENDING_HUMAN (Round 5 - Render Parity) (Round 5 - Render Parity)` is not a failure.

---

## 6. Test fixtures still present (do not delete blindly)

| Fixture | Where | Notes |
|---|---|---|
| `loom-edit-fidelity:0` | `loom_media` (`render-1.0`, dur 8.9), `loom_scores` (seeded hook/caption), `loom_edit_corrections` (`render-1.1`), tokens | The consistency fixture. `loom_media` row `render-1.1` has NULL canonical/clean (expected: final_publication skips media registration). |
| `loom-e2e-proof-job:0` | `loom_media` (`render-1.0` dur 6.0, `render-1.1`) | Superseded acceptance card `1550967120089718935`. |
| Staged render files | `openshorts/output/loom-edit-fidelity/` and `loom-acd934a8bb7f/` | Gitignored. **Do NOT commit generated media.** |
| Retired orphans | 7 rows, clip `6c46c3ef-5eba-40bc-9b50-8f3457d1db79:1`, tenant `loom-acceptance-test`, created 2026-09-14, `pending` with no message | Pre-existing, not mine, left untouched. Decide whether to retire. |
| Real scored candidates | `126d1217-…:0` (`loom-internal-test`), `6c46c3ef-…:1` (`loom-acceptance-test`) | Both have **0 `loom_media` rows** → neither can host an Edit-enabled card. |

---

## 7. Gotchas that cost real time this session

1. **`log_message` did not flush** (now fixed in `8b89ce2`). Response lines sat in the block-buffered stdout and only appeared when a *later* request flushed. A completed request that returned **200** looked like a request that never responded — I concluded that wrongly and had to correct it. If a request ever looks unanswered, force a flush (any request) and re-read before concluding.
2. **Source IP proves nothing.** Caddy proxies to `host.docker.internal:8090`, so externally-relayed traffic logs as `127.0.0.1`; at Caddy it is the Docker bridge (`172.22.0.1`). The reliable evidence was the *timeline gap*, not the IP.
3. **Tailscale funnel hostnames short-circuit locally.** A request to the funnel hostname from the same Mac resolves via MagicDNS and never leaves the machine. Use `curl --resolve <host>:443:<public-ingress-ip>` to test the real path.
4. **`~/.openclaw/.env` holds a 41-char non-JWT `LOOM_SUPABASE_SERVICE_ROLE_KEY`** and it **works**. Do not "fix" it. Inline shell/python that embeds key material gets masked and can corrupt a script — read credentials via `adapter`'s own helpers (`supabase_filter_get`) instead of inlining them.
5. **`http_json()` returns `(status, body)`**, not the body.
6. **Adapter restart:** kill the adapter PID; `supervise.sh` restarts it. Restart is REQUIRED to load code changes.

---

## 8. Twitch EventSub

```
TWITCH_EVENTSUB_IMPLEMENTATION   = COMPLETE   (commit 8df2054, pushed)
TWITCH_EVENTSUB_LIVE_ACCEPTANCE  = PENDING
```
Implemented: `infra/discovery/connectors/twitch.py` — Helix listing + EventSub `channel.clip.created` push (one bounded Helix hydrate, Twitch's documented pattern), HMAC-SHA256 webhook signature verification with replay window, challenge handshake, injectable transport (suite is fully offline), registered in `connectors/__init__.py`. 25 tests. **Live acceptance needs `LOOM_TWITCH_CLIENT_ID` / `LOOM_TWITCH_CLIENT_SECRET` and a public HTTPS callback.** $0.

---

## 9. Remaining Segment 5 sequence (authorized order — do not reorder)

1. **Finish Editor visual/runtime acceptance** (see §5) — `EDITOR_VISUAL_ACCEPTANCE = PENDING_HUMAN (Round 5 - Render Parity) (Round 5 - Render Parity)`.
2. **Segment 5B — Multimodal Candidate Intelligence.**
   * **Objective:** cheap LOCAL audio/visual signals so speech is not the only thing that can create a candidate moment. Target the `talking → talking → VISUAL PAYOFF → talking` failure.
   * Pipeline shape: `transcript + audio spike + visual activity + scene change → candidate evidence → Qwen`.
   * Signal layer: per-second `loudness`, `spike` (vs rolling-30s median), `burst` (onset density), `noisiness` (ZCR, audibility-gated); visual `motion`, `scene_cut`, `flash`; `speech_density` from existing word timestamps.
   * Normalise **percentile-rank WITHIN the video only** — a relative salience signal, never an absolute virality claim, never presented as content-quality evidence.
   * Signal-peak windows are **additive**: tag `source: signal_peak`; never displace an existing transcript window.
   * Signals inform the existing scoring prompt as context — they do NOT replace model judgement, and OpenShorts' own score stays excluded from Qwen-visible input.
   * Emit `signal_manifest.json` beside the clips (thresholds + per-second arrays recorded) for auditability.
   * **Storage decision:** local manifest + ledger reference initially. No raw per-window telemetry in Supabase until centralized querying is demonstrated.
   * **Exclusions:** no AGPL code copying (read `clips-studio` for ideas only — it is AGPL-3.0); no academic moment-retrieval models (Moment-DETR/UniVTG — overfit, MPS-hostile, licence-encumbered); no full-content/Level-4 pre-acquisition analysis; do not widen the pre-acquisition `content_intelligence` pipeline (§ evidence level 3 is the authorized ceiling).
   * Run the bounded A/B: existing absolute ranking vs relative rerank, and keep relative rerank **only if it changes ordering usefully** — evidence before architecture.
3. **5C.1 — WhisperX evaluation** (forced alignment vs current word-snap; measure drift on a fixed corpus).
4. **DeepFilterNet A/B** — bounded, only if warranted and non-blocking.
5. **Timestamp-primitive decision.**
6. **5C.2 — Complete-Thought Boundary Refiner.** Must return explicit `boundary_mode` / `boundary_reason` / `boundary_degraded`, preserve `original_bounds` alongside `proposed_bounds`, and have **no silent fallback**. Promotion of safe boundary adjustments from proposal → automatic is a **trust ramp** backed by acceptance evidence, NOT a permanent human gate.
7. **Bounded relative-Qwen rerank A/B.**
8. **Segment 5 runtime acceptance.**
9. **Docs / tests / commit / push.**
10. **Segment 5 closeout.**

Also still open from earlier planning (not authorized yet): the 8 ecosystem items (OBS/GSI/etc.), the gaming 4th tenant, and the Ultralytics→MediaPipe chassis ticket.

---

## 10. Next session starting point

Because `EDITOR_VISUAL_ACCEPTANCE = PENDING_HUMAN (Round 5 - Render Parity) (Round 5 - Render Parity)`:

```
START HERE NEXT SESSION: Human visual acceptance for Header render parity is ready.
Open ~/Desktop/LOOM-EDITOR-ACCEPTANCE.txt to access the single-use editor link.
Turn on the Header ("on-video title / header -> enabled"), select some text to style it, change to 'dedicated black region', hit 'auto edit', and view the Discord #re-review card output to confirm final render fidelity matches your exact selection geometry.
Then begin Segment 5B.