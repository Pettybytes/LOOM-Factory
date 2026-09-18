# Acquisition Card — Implementation & Review Report

**Scope:** premium operator card for the approved Instagram acquisition lane (Phase 1).
**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` (no commit, no push, as instructed)
**Provider calls:** 0 · **Spend:** $0 · **Downloads:** 0 · **Migrations:** 0 · **Asset modified:** no

---

## 1. What was built

| File | Purpose |
|---|---|
| `infra/acquisition/acquisition_card.py` | Presentation module: view model, state machine for operator intents, Discord embed + button builders, guards, provenance hygiene, retained-Reel fixture. No I/O, no provider, no spend. |
| `infra/acquisition/tests/test_acquisition_card.py` | 35 deterministic tests (state/button matrix, spend guards, duplicate clicks, hygiene, formatting, fixture/library integration). |
| `infra/acquisition/render_card_previews.py` | Renders the **live payload** to HTML and screenshots every state at desktop + narrow width; writes `manifest.json`. |
| `infra/acquisition/card-previews/*.png` | Rendered evidence (7 renders + contact sheet). |

Test totals: **acquisition 145 passed** (110 pre-existing + 35 new), **discord-review 78 passed** (no regressions).

The card reuses the existing operator visual language (`infra/discord-review/source_review.py`):
same embed shape, inline field pairs, outcome-colour palette, button `custom_id` convention,
and `decided_embed` for the Deny/Hold outcome. **No new visual language was invented.**

Boundary discipline: the card renders state and maps intents. Money and media stay behind the
existing boundary — `provider.py` (allowlist/validation/provenance), `cost.py` (ceiling,
reservation, breaker, reconciliation), `orchestrate.py` (the only dispatch path),
`reaction_library.py` (asset record).

---

## 2. Existing-asset proof (no re-download, no conversion)

```
sha256 before: 9b22f887b2ff6a3b78d91529c1d9c3105497af2a4298ba9241394686239ae096
sha256 after : 9b22f887b2ff6a3b78d91529c1d9c3105497af2a4298ba9241394686239ae096  (unchanged)
```
File: `~/.openclaw/loom-reaction-library/library/instagram__DdZS1QRhrXE__9b22f887b2ff.mp4`
Observed facts (read-only `ffprobe`): h264 + aac, 720×1280, 30 fps, 55.26 s, 23,721,338 bytes, audio present.

The retained Reel is the runtime fixture for the ACQUIRED state. It was opened read-only by the
browser and never copied, re-downloaded, transcoded, or modified. No Apify call was made.

**Honest limitation:** this Chromium build cannot decode H.264, so the video element reports
`videoWidth = 0` and the renderer degrades to a clearly-labelled 9:16 placeholder frame
(`manifest.json → video_element_decoded: false`). **No frame was extracted or converted.** If a
real frame is wanted in future evidence, a single-frame `ffmpeg` extraction would be required —
that is a conversion step and was deliberately not taken without asking.

---

## 3. Rendered evidence

`infra/acquisition/card-previews/`

| File | Shows |
|---|---|
| `card-review.png` | REVIEW — Approve / Deny / Hold / View Source |
| `card-approved.png` | APPROVED — Acquire for Reaction / Hold / Deny / View Source |
| `card-acquiring.png` | ACQUIRING — all interactive buttons disabled, progress field |
| `card-acquired.png` | ACQUIRED — vertical preview surface, library record, sha256 |
| `card-failed.png` | FAILED — ⚠️ Retry Manually / Hold / View Error / View Source |
| `card-review-narrow.png`, `card-acquired-narrow.png` | narrow/mobile width |
| `card-states-contact-sheet.png` | all five states stacked |
| `manifest.json` | per-render viewport, button labels, disabled flags, decode result |

Renders come from `acquisition_card.format_acquisition_card()` — the same payload the adapter
would send — so payload changes change the screenshots. The preview HTML renders Discord
markdown (`**bold**`, `` `code` ``) so the images match Discord's actual appearance.

---

## 4. Exact action/button behaviour by state

| State | Buttons (label → action) | Style | Enabled | Effect |
|---|---|---|---|---|
| **REVIEW** | Approve, Deny, Hold, View Source | success / danger / secondary / link | yes | `approve` records authorization only — **never spends, never downloads**; `deny`/`hold` record a decision, card lane ends |
| **APPROVED** | Acquire for Reaction, Hold, Deny, View Source | success / secondary / danger / link | yes | `acquire` is the **single spend + download trigger** (requires authorization) |
| **ACQUIRING** | Acquiring…, Hold | secondary | **all disabled** | no interactive action possible; progress field shows the run is in flight; card updates in place |
| **ACQUIRED** | Preview / Play, Open in Reaction Library, Hold | success / secondary / secondary | yes | `play` opens preview, `library` opens the asset record; `acquire` is permanently refused (`already_acquired`) |
| **FAILED** | ⚠️ Retry Manually, Hold, View Error, View Source | primary / secondary / secondary / link | yes | `retry` is manual, single-shot, explicitly authorized, **never automatic, never a loop** |

Intent differentiation: approve/acquire = green, hold = neutral grey, deny = red,
retry = blue with a ⚠️ label prefix (Discord exposes no warning style; blue is used so retry
never reads as approve-green or deny-red), view-source = link style (navigation only — it
cannot mutate state).

Programmatic guarantees (enforced in code and asserted by tests):
`requires_spend()` is true only for `acquire` and `retry`; `is_safe_without_spend()` is true for
approve/deny/hold/play/library/view-error/view-source. Duplicate clicks inside 20 s return
`duplicate_click`; a second job for an acquired asset returns `already_acquired`; a click during
a run returns `in_flight`.

---

## 5. Mobile / narrow-width behaviour

Verified at 340 px viewport (`card-review-narrow.png`, `card-acquired-narrow.png`):

- Inline field pairs wrap to a single column; values stay left-aligned and never clip.
- Buttons wrap onto a second row instead of shrinking below touch-target size.
- The header + platform/state pills wrap; long provenance wraps mid-token without overflow.
- Long values are clipped at Discord's own limits (1024 chars/field, 25 fields, 2048 footer,
  80-char button label, 100-char `custom_id`) — enforced in `format_acquisition_embed`.

---

## 6. Accessibility basics

- **Contrast:** body text `#e3e5ea` on `#131519` (≈14:1); field labels `#7d8592` (≈4.7:1);
  footers `#6b7280` (≈4.1:1) — above WCAG AA for normal text.
- **Labels:** every button has a plain-language label; the destructive action is literally
  "Deny", the spend action "Acquire for Reaction" — never icon-only, never ambiguous.
- **Disabled state:** both visual (dimmed) and semantic (`disabled: true` in the payload, so the
  Discord client renders it non-interactive and conveys it to assistive tech).
- **State is text, not colour alone:** each card carries a state title, a state pill, and a
  status sentence in the footer, so state is legible without colour perception.
- **Keyboard:** within Discord, buttons are reachable through the client's normal component
  navigation (tab/arrow per platform); the card adds no custom focus traps. Rail-level keyboard
  guarantees are therefore Discord-client-dependent, not card-dependent.

---

## 7. Genuinely implemented vs fixture/mock

**Implemented (real, working code):**
- View model, state machine, guards, embed/button builders, custom-id encode/parse,
  provenance sanitizer, media/cost/library formatting — all pure and unit-tested.
- Duplicate-click, in-flight, already-acquired, and illegal-transition protection.
- Failed-state cost honesty: a failed run shows "actual not reconciled" and never claims
  "not yet spent".
- URL hygiene: canonical URLs are stripped of query/fragment; any URL carrying `sig=`, `oh=`,
  `token=`, `signature=`, `x-amz-` or `key=` is dropped entirely; a test asserts no signed URL
  marker can reach the payload.
- The preview renderer (real Playwright screenshots of the real payload).

**Fixture / not yet wired (stated plainly):**
- The card is **not yet wired to the adapter** — no Discord message was posted. Posting is the
  next step and is explicitly out of this phase.
- The ACQUIRED card is rendered from the retained Reel record (real hash, real media facts),
  but as a fixture view model — the live path would populate it from the job/ceiling/library
  rows after a real run.
- Creator handle, caption, and source thumbnail are **omitted rather than invented**: the
  Instagram proof record did not store a handle or caption, so the card renders the honest
  "no preview yet — open the source link" affordance. (TikTok/YouTube records do carry a
  creator, and the populated path is covered by tests.)
- Cost figures ("Est. $0.0026 · ceiling $1.00 · 9 left") mirror the recorded ledger, but the
  card reads them from a view-model field — it does not query `cost.py` yet.

---

## 8. Discord limitations that constrain the intended experience

1. **No local-file embeds.** Discord cannot play a file from disk. In-client preview requires
   either a message attachment (`attachment://…`) or a hosted URL. The asset is 22.6 MiB, which
   sits under Discord's 25 MiB upload ceiling but has little headroom — worth noting before
   relying on attachment delivery.
2. **Link buttons cannot be disabled**, so during ACQUIRING the card omits View Source entirely
   rather than showing a "disabled" link that isn't.
3. **No warning button style.** Retry is mapped to primary + ⚠️ prefix; documented in §4.
4. **Interaction ACK deadline.** Discord requires an interaction response within ~3 s; a real
   `acquire` must be deferred (acknowledge, then update the card in place). That adapter-side
   deferral is **not implemented in this phase** and is required before live wiring.
5. **No rich layout.** Discord embeds are title/description/fields/footer. The premium look is
   achieved through hierarchy and spacing, not free-form layout; anything richer needs a hosted
   web view, which is out of scope here.
6. **Ephemeral vs persistent** card lifetime is an adapter decision, not a card decision.

---

## 9. Verification run

```
python3 -m unittest discover -s infra/acquisition/tests   → 145 passed
python3 -m unittest discover -s infra/discord-review/tests → 78 passed
python3 infra/acquisition/render_card_previews.py          → 7 renders, no decode (documented)
shasum -a 256 ~/.openclaw/loom-reaction-library/library/*.mp4 → unchanged
git status                                                 → new untracked files only, no commit
```

## 10. Next (not started, needs authorization)

1. Adapter wiring: post the card, handle interaction ACKs + deferred `acquire` (3 s constraint).
2. Approved-lane runner: approval → job row → `orchestrate.dispatch()` with real transport,
   hard-setting the pilot bound (≤100 MiB / ≤5 min — the job default is 2 GiB and must not be
   inherited).
3. Library record write-back into the ACQUIRED card, and card update-in-place on state change.
