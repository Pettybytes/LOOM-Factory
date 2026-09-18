# LOOM Local Acquisition Helper — design

Browser-assisted Instagram video acquisition. The Chrome extension (human-proven
`chrome_network_capture` resolution) sends the captured media candidate here; the
helper validates, downloads exactly one MP4, validates the asset, and records the
Acquisition Job + Personal Reaction Library ledger.

## Endpoint

`POST http://127.0.0.1:8787/acquire`

Request headers:
- `Authorization: Bearer <token>`
- `Content-Type: application/json`
- `Origin: chrome-extension://<id>` (browser-set; must be `chrome-extension://`)

Request body (JSON, ≤ 8 KB, strict schema):
```json
{
  "canonical_url": "https://www.instagram.com/reel/DdZS1QRhrXE",
  "candidate_url": "https://scontent-det1-1.cdninstagram.com/v/...mp4?sig=...&exp=...",
  "candidate_host": "scontent-det1-1.cdninstagram.com",
  "mime_type": "video/mp4",
  "media_type": "direct_video",
  "actor": "bryan"
}
```

Response (JSON, redacted — never the signed CDN URL):
```json
{
  "ok": true,
  "status": "acquired",
  "destination": "personal_reaction_library",
  "routing": "Awaiting tenant assignment",
  "adapter": "chrome_network_capture",
  "submission_id": "...",
  "library": {
    "filename": "instagram__DdZS1QRhrXE__<hash12>.mp4",
    "size_bytes": 1234,
    "duration_seconds": 4.5,
    "width": 640, "height": 1136, "frame_rate": 30.0, "has_audio": true,
    "sha256": "...", "local_path": "/Users/.../library/....mp4"
  }
}
```

## Token storage (outside Git)

- `infra/local-helper/.token` — high-entropy token (gitignored, `chmod 600`).
- `infra/chrome-extension/config.js` — mirrors the token + helper URL (gitignored).
- Committed `config.js.example` documents the shape only.

## Output directory (Reaction Library cache)

```
~/Movies/LOOM-Reaction-Library/
  .tmp/     # partial downloads (cleaned on every failure)
  library/  # finalized, checksummed assets (atomic rename)
```

Final filename is deterministic: `<platform>__<content_id>__<sha256[:12]>.mp4`.

## Proof procedure (first bounded acquisition)

1. Start the helper: `infra/local-helper/run.sh` (sources `~/.openclaw/.env`).
2. In Chrome, open a confirmed video Reel and let it play.
3. Click **Acquire for Reaction** (extension).
4. Extension: bounded inspection → captures the first direct MP4's signed URL
   (volatile only) → POSTs it to the helper.
5. Helper: validates (https + approved host suffix + no creds/port; every
   redirect) → downloads ≤100 MiB → ffprobe (video stream, ≤5 min, container,
   dims, fps, audio presence) → SHA-256 → atomic rename → records the Acquisition
   Job (adapter=chrome_network_capture) + Reaction Library entry (non-production).
6. Extension displays "Saved to Reaction Library" + the local OBS-ready path.

## Security invariants

- 127.0.0.1 only; token + `chrome-extension://` origin required.
- HTTPS + approved Meta/IG CDN host suffix (rejects `cdninstagram.com.evil.com`).
- No credentials, no non-standard ports, no redirects off the allowlist.
- No arbitrary output paths / shell args; no cookies / auth headers / browser
  session exports.
- Timeout, single-item, 100 MiB byte ceiling, 5-minute duration ceiling.
- No retry storm. No Apify. No GHL upload. No OpenShorts. No OBS. No publish.
- The full signed CDN URL is never logged, persisted, or returned.
