# Live Proof Preparation — DdQE_RUS0GJ (preparation only)

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none
**Provider calls:** 0 · **Downloads:** 0 · **Spend:** $0 · **Gate 1/2 rows created:** 0
**Handler invoked directly:** no · **Approve clicked:** no · **Acquire clicked:** no

---

## 1. Preflight (read-only)

| Check | Result |
|---|---|
| Migrations 034/035 reachable | ✅ both tables readable |
| Ceiling = 2,600 micro / 1 success | ✅ exactly; remaining **$0.9974**, **9** successes, not disabled |
| Target not in Reaction Library | ✅ 0 rows for `DdQE_RUS0GJ` (library total 1) |
| No existing job for this content id | ❌ **FALSE — one row exists** (see below) |
| Apify token present | ✅ present (never printed) |
| Instagram actor allowlisted + active | ✅ `apify/instagram-reel-scraper`, role `default`, active, api id `xMc5Ga1oCONPmWJIa` (recorded build 0.0.569) |

**Preflight discrepancy (material).** One acquisition job already exists for this content id:

```
id                 d3b4bba9-4e1c-49d8-b6c6-f7bf4a8fc742
authorization_type acquire_for_reaction   (a GATE 2 row)
status             failed  («acquisition failed»)
created            2026-09-17T22:37:40Z   ← yesterday, before this session
actor              bryan
canonical_url      https://www.instagram.com/p/DdQE_RUS0GJ   (note: /p/, not /reel/)
asset_provenance   adapter: chrome_network_capture            ← the local capture path
provider runs      none reference it
```

It is a historical failure from the **local chrome-extension capture** attempt (not the Apify
provider), it produced no provider run and no library asset, and its `dedup_key` differs from what a
fresh Gate 1 row for this source would produce — so it **does not block** the prepared flow. Flagged
rather than hidden: the preflight's "no existing job" confirmation did **not** pass.

---

## 2. Health after restart

`LOOM_ACQUISITION_LIVE=1` added to the supervised environment (`~/.openclaw/.env`), the variable
added to `run.sh`'s sourced list, adapter restarted through its supervisor → **pid 52412**.
`helper.py` (8787) untouched (pid 83512).

```json
{"route":"AQ","route_active":true,"store":"durable","gate1_approve":true,
 "gate2_acquire":true,"live_runner":"enabled","durable_store_flag":"1",
 "apify_token_present":true,
 "bounds":{"max_bytes":104857600,"max_seconds":300,"max_results":1,
           "max_attempts":1,"fallback":false}}
```

All nine required health assertions hold: AQ active · durable store · live runner **enabled** ·
token present · 100 MiB · 300 s · one result · one attempt · fallback false.

---

## 3. Card

**Message ID:** `1550616797865181315`
**Link:** https://discord.com/channels/1546266463663423518/1550207989569421444/1550616797865181315

Exactly one card posted, to **#source-review**. No creator, caption, duration, dimensions or size
were fabricated (all render as unknown); the canonical URL only; no local asset exists so the honest
"no preview yet" placeholder is shown. No Apify call was made to populate it.

```
title      : 🔎 ACQUISITION · REVIEW
description: Instagram source
Media      : unknown · unknown · Audio unknown
Cost       : Est. $0.0026 (not yet spent) / ceiling $1.0000 · 9 acquisitions left
Library    : Not acquired
Preview    : No preview yet — open the source link to view it.
Provenance : ID `DdQE_RUS0GJ` · apify/instagram-reel-scraper
buttons    : Approve | Deny | Hold | View Source
footer     : Instagram · Awaiting your decision — nothing has been spent or downloaded ·
             dreemzy · acquisition-live-proof · 1 URL · 1 item · ≤5 min · ≤100 MiB · breaker $1.00 / 10
```

---

## 4. Unchanged-state confirmation

| Metric | Value | Change |
|---|---|---|
| Spend | 2,600 micro ($0.0026) | **unchanged** |
| Ceiling | 1,000,000 micro / max 10, not disabled | **unchanged** |
| Successful acquisitions | 1 | **unchanged** |
| Provider runs | 1 total, **0 for this target** | **unchanged** |
| Library rows | 1 (target absent) | **unchanged** |
| Jobs total | 5 (target still has only the historical failed row) | **unchanged** |
| Downloads | 0 | **none** |
| Asset `DdZS1QRhrXE` sha256 | `9b22f887b2ff…ae096` | **unchanged** |

Tests: acquisition **242**, discord-review **120** — both green. Nothing committed or pushed.

---

## 5. Next action — Bryan

**Click Approve on the new card in Discord.** That records Gate 1 (qualification) only: no provider
call, no download, no spend. Gate 2 is a separate click on *Acquire for Reaction*, which is the only
action that can spend (~$0.0026).
