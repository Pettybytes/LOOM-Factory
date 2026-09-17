# Apify Instagram Actor Evaluation — LOOM paid-acquisition fallback

> Phase 1 artifact. **$0 spent, no Actor call made, no token used.** This is an
> evaluation + plan only; STOP for Bryan's approval before any spend.

## Summary

Local Instagram acquisition is **closed out** (`blocked_local_fmp4_incomplete`).
This evaluates the paid-provider fallback, Instagram first, with a
**platform-neutral** boundary so the same contracts extend to TikTok / Reddit /
X / YouTube / Twitch / Kick later (each requires separate evaluation +
authorization before activation).

**Primary candidate:** `agentx/instagram-reels-downloader`
**Documented alternative:** `thenetaji/instagram-video-downloader`

---

## The 14-point evaluation — primary candidate `agentx/instagram-reels-downloader`

1. **Actor ID / developer / version / history**
   - Actor ID `59aU9vig8wglWOBe1` (stable); name `agentx/instagram-reels-downloader`.
   - Developer: **AgentX** (community, not Apify-owned).
   - Version/build: not published on the listing page; last verified **2026-08-09**
     (metadata read returned in 4.0s). ⚠️ Capture exact build from the `versions`
     tab before Phase 3, and record it in every run.

2. **Pricing model + meaning of one billable result** (pay-per-event, FREE tier)
   - `Actor Start` **$0.01** — once per run.
   - `Metadata` **$0.08** — one record read (no file).
   - `Download` **$0.20** — one record + stored video file.
   - One billable "result" = one Metadata event (or one Download event when a file
     is stored). Free tier pays **$0.29** for a single Reel with file
     (`0.01 + 0.08 + 0.20`); **$0.09** for metadata-only.
   - Store discounts: Bronze 10%, Silver 20%, Gold+ 25% (record events only).

3. **Single direct Reel/post URL supported?** Yes — required input `video_url`
   accepts "an Instagram reel or post URL". One link in, one row out.

4. **Metadata-only separate from media download?** Yes — `video_quality:
   "metadata"` reads the 43-field record only; `low|medium|high` additionally
   stores the file. The `video` output field is empty on a metadata run.

5. **Enforce one URL / one result / no crawl / no search / no pagination /**
   **no related-post / no carousel expansion**
   - The Actor exposes exactly three inputs (`video_url`, `video_quality`,
     `max_comments`) and **no** profile/hashtag/search/pagination/related inputs.
     One run → one dataset row. One-URL/one-result is enforced **by the Actor's
     own surface**, and our boundary re-validates canonical URL + one-item before
     dispatch (defense in depth).
   - Carousel: not a first-class concept here; behavior for `media_type=8` is
     **unverified** — pilot target is a single Reel, so carousel is out of scope.
     Verify before any carousel use.

6. **Carousel → multiple billable results?** Unverified. Docs say "one row per
   run". Pilot is Reel-only; treat any carousel as `blocked` until separately
   evaluated.

7. **Minimum run charge** — Actor Start **$0.01** is the floor. A metadata-only
   run is $0.09; a file run is $0.29. ⚠️ The Start event is likely billed even
   on a failed run (verify in Phase 3 logs).

8. **Additional compute/proxy/storage/transfer costs** — **None** for this
   Actor: "You are not charged for the Apify platform usage." No per-MB transfer,
   no proxy surcharge. (Contrast: `thenetaji` charges a per-MB transfer fee.)

9. **Output schema / file URL / expiration**
   - 43 fixed fields (identity, creator, engagement, media properties,
     classification, extras, provenance). Nulls are honest (not zero-filled).
   - `video` field = stored file URL (Apify key-value store) when a download was
     requested. We must fetch it promptly and then **delete** the remote record
     (store retention is Apify's default, typically 7 days).

10. **Remote storage retention + deletion**
    - File lives in an Apify key-value store record. Deletion via
      `DELETE /v2/key-value-stores/{storeId}/records/{key}` (our policy: fetch →
      local ffprobe + SHA-256 → finalize → delete remote, in `finally`).

11. **Credential requirements** — **None for Instagram.** No Instagram login, no
    cookies, no browser-session export. "Reads only what a public visitor can
    already see." Only the Apify platform token (`LOOM_APIFY_API_TOKEN`) is
    needed to call the Actor.

12. **Reliability / reviews / usage / maintenance / supply-chain**
    - 9 total users · 5 monthly · **100.0% runs succeeded** · 4.00/5 · 1 bookmark.
    - ⚠️ **Very low adoption** (9 users) + community developer = **supply-chain /
      maintenance risk**. Mitigate: pin Actor ID + record build, prefer the
      alternative if this Actor is ever de-listed or changes pricing.
    - Alternative `thenetaji/instagram-video-downloader`: 386 users · 39 monthly ·
      87.8% success · 3.40/5 (higher adoption, lower rating, per-MB transfer fee,
      `codes` is an array so it must be forced to a single item).

13. **Estimated total cost (single-Reel, with file, FREE tier)**
    | Acquisitions | Metadata-only ($0.09) | With file ($0.29) |
    |---|---|---|
    | 1 | $0.09 | $0.29 |
    | 10 | $0.90 | **$2.90** |
    | 100 | $9.00 | $29.00 |
    | 1,000 | $90.00 | $290.00 |

    ⚠️ **Pilot-ceiling conflict to resolve:** "$1 lifetime ceiling" AND "max 10
    acquisitions" cannot both hold with the file path — $0.29 × 10 = $2.90 > $1.
    At $0.29/acquisition the $1 ceiling allows **~3** file acquisitions
    (3 × $0.29 = $0.87). Recommendation: keep the **$1 hard ceiling** as the
    binding constraint and set **max 3** successful pilot acquisitions (or raise
    the ceiling to ~$3 if 10 is non-negotiable). Circuit breaker enforces
    whichever is reached first.

14. **Failed/empty runs charges** — A file that never completes "is not billed";
    the Metadata/Download events fire only on a produced record. ⚠️ Actor Start
    $0.01 is likely still charged on a failed run. A run with **zero** eligible
    results fails rather than emitting an empty row. Verify exact failed-run
    billing in the Phase 3 run logs before trusting the ceiling math.

---

## Alternative `thenetaji/instagram-video-downloader` (comparison)

- Input `codes` (**array** — shortcodes / numeric IDs / full URLs), `saveMedia`
  (bool), `quality` (`480|720|1080|best`).
- Permanent stored MP4; `directUrl` expires ~34h when `saveMedia:false`.
- Pricing **from $1.00/1,000 results + a per-megabyte transfer fee** (billed MB
  rounds up). Failed/skipped inputs are **not** charged; no login/cookies.
- 386 users · 39 monthly · 87.8% success · 3.40/5.
- Verdict: higher adoption, lower rating, **harder to pre-price** (per-MB fee) and
  `codes` is bulk-capable so single-item must be enforced by our boundary.

**Recommendation:** adopt `agentx/instagram-reels-downloader` for the Instagram
pilot (flat pre-priced $0.29, single-URL-by-design, metadata/download separated,
no usage charge). Keep `thenetaji` as a documented fallback if the primary is
de-listed or its pricing changes.
