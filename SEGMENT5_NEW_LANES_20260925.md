# SEGMENT5 NEW LANES 2026-09-25 — rollback + six-platform human-review run

**Rollback first (Bryan's directive):** commit `6028fa3` (structured reject
reasons) was reverted by `b68ef1a`; the reject work is preserved under tag
`segment5-reject-reasons`. No schema changes had been made by that work
(`rejection_category`/`reviewer_instructions` pre-exist in migration 024).
Zero DB rows were written during the reverted work (timestamp-verified).
All four suites re-run on reverted HEAD `b68ef1a` at exact baseline counts:
content **469** / discovery **148** / Discord **255** / acquisition **578**.
Migration 038 remains untracked/unmodified, SHA1 `3da12826ef96dd05ce089b490ee490e66baf01c0`.

## New-lane human-review cards (advisory, no code changes)

Tenant `dreemzy` / profile `segment5-newlanes-20260925`. Lanes: gym baddie
fitness moments (YouTube/TikTok/Instagram/Reddit), TPain funny moments
(Twitch), Blac Chyna funny moments (Kick). Creator identities verified
natively: Twitch `tpain` id 117083340 (partner); Kick `blacchyna` id
121279976 (verified=true). Media-first advisory review; strict gates remain
(adults_only, direct event, timed payoff, intent match).

| Platform | Card | Window | Market/Content/Selection/Confidence | Verdict |
|---|---|---|---|---|
| kick | [1553193671950598175](https://discord.com/channels/1546266463663423518/1550207989569421444/1553193671950598175) | 0–59s | 41/88/84/100 | review option (strong) |
| twitch | [1553193680012058638](https://discord.com/channels/1546266463663423518/1550207989569421444/1553193680012058638) | 0–23s | 29/62/65/92 | review option |
| instagram | [1553193683854164012](https://discord.com/channels/1546266463663423518/1550207989569421444/1553193683854164012) | 0–5s | 99/65/100/78 | review option |
| youtube | [1553193689483051099](https://discord.com/channels/1546266463663423518/1550207989569421444/1553193689483051099) | 0–15s | 44/60/75/100 | review option |
| reddit | [1553193693458997334](https://discord.com/channels/1546266463663423518/1550207989569421444/1553193693458997334) | 0–12s | 0/65/14/20 | review option (weak, honest) |
| tiktok | [1553193699104661515](https://discord.com/channels/1546266463663423518/1550207989569421444/1553193699104661515) | 0–58s | 58/38/69/85 | review option (weak, honest) |

All six read back live with Approve/Reject/Hold + Open Source controls; zero
reviews recorded at readback; acquisition `live_runner=disabled`. No
approvals impersonated. No FIRE passes claimed (advisory human review).
Honest rejections recorded: Twitch gameplay clips `adult_status_unproven` x3
(no people visible — gate working as designed), Reddit EgyGym post
intent_mismatch+adult_status_unproven, TikTok gymgirlthings clip
`no_directly_observed_event`, Instagram reel adult_status_unproven.

## Lane-config notes (tenant terms only, no code changes)

Creator-pinned lanes (Twitch/Kick) and Instagram use `required_concepts: []`
— theme enforced by media-first inspection against lane intent (cryptic
titles carry no theme words). TikTok gym lane uses lane vocabulary tokens
(gym/baddie/gymgirl/gymtok/glutes/legday) because word-boundary matching
does not split compound hashtags. YouTube shorts: only `jtVwshyzEh4`
verified as a portrait native Short (yt-dlp; desktop HTML client redirects
to /watch, HTTP 200 — same evidence standard as the Reddit card).

## Costs

- Apify actual (ledger-reconciled): TikTok `$0.0565` + Instagram `$0.0218` = **$0.0783**
- Gemini usage-based estimate (cached-metered ledger): **$0.0430** (16 media inspections + 0 metadata calls)
- Combined ≈ **$0.121** (excludes agent inference). Reservations remain
  standing until billing verified, per ledger discipline.

## Verification artifacts

Readback: `/tmp/loom-newlanes-20260925/verified.json`. Per-platform
inspection JSON, discovery records, and spend receipts in the same
directory. No new provider/subscription/publication spend.
