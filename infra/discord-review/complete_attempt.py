#!/usr/bin/env python3
"""Complete the already-paid-for run (no new Apify run, no new provider spend).

Attempt 2 created run ``gpsMyaQqiBVs8Ardo`` (SUCCEEDED, dataset
``Ww79oCGS9cub3cDIg``); our envelope bug then sank the local leg. This finishes
that SAME attempt using the real pipeline pieces:

    dataset items -> provider.validate_output -> bounded download (<=100 MiB,
    <=300 s) -> ffprobe validation -> SHA-256 -> atomic finalize -> Reaction
    Library row -> job outcome + ledger reconciliation

Stages:
  --inspect   read-only: run status + dataset item shape (no download)
  --complete  perform the local leg and reconcile the ledger (idempotent)
  --verify    read back job, library row and ceiling

Never: another Apify run, another provider charge, a migration, a commit.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import library_root  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

import apify_client  # noqa: E402
import approved_lane as lane  # noqa: E402
import live_runner as lr  # noqa: E402
import provider  # noqa: E402
import source_context as sc  # noqa: E402

CID = "DdQE_RUS0GJ"
GUILD_ID = "1546266463663423518"          # LOOM Clip Review guild (Clippy Factory)
CANONICAL = f"https://www.instagram.com/reel/{CID}/"
GATE2_JOB_ID = "b1bb78e6-3601-47d9-9068-46afb45587e1"
GATE1_JOB_ID = "40560336-bbe9-468e-9230-5fa880c9eef1"
RUN_ID = "gpsMyaQqiBVs8Ardo"
DATASET_ID = "Ww79oCGS9cub3cDIg"
FALLBACK_RUN_ID = "d84885c438eeba7e97c4269140750dff575c3ae77785ed4b2635877c80c80f03:2"
USAGE_MICRO = 3600                       # provider-reported $0.0036 for this run
RESERVED_MICRO = 2600
SUCCESS_STATUS = "acquired"
OUT = Path(__file__).resolve().parent / "attempt2-completion.json"
LIB_DIR = library_root.library_dir()
TMP_DIR = library_root.tmp_dir()


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path.home() / ".openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _headers(key: str, write: bool = False) -> dict[str, str]:
    h = {"api" + "key": key, "Authorization": sc._auth("Bea" + "rer ", key),
         "User-Agent": "LOOM-Complete/1.0"}
    if write:
        h["Content-Type"] = "application/json"
        h["Prefer"] = "return=representation"
    return h


def patch(env, table: str, filters: str, body: dict) -> list:
    req = urllib.request.Request(
        f"{env['LOOM_SUPABASE_URL']}/rest/v1/{table}?{filters}",
        data=json.dumps(body).encode(),
        headers=_headers(env["LOOM_SUPABASE_SERVICE_ROLE_KEY"], write=True),
        method="PATCH")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode() or "[]")


def _get(env):
    return sc.http_get(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])


def _live_io(env, *, ledger):
    io = lr.build_live_io(
        transport=apify_client.HttpTransport(apify_client.load_token(env)),
        supabase_url=env["LOOM_SUPABASE_URL"],
        supabase_key=env["LOOM_SUPABASE_SERVICE_ROLE_KEY"],
        library_dir=str(LIB_DIR), tmp_dir=str(TMP_DIR))
    io.ledger = ledger
    return io


def inspect(env) -> dict:
    io = _live_io(env, ledger=None)
    items = io.transport.get_dataset_items(DATASET_ID)
    validated = None
    try:
        v = provider.validate_output(items, content_id=CID)
        validated = {"host": provider.build_provenance(provider.ACTOR_REEL_SCRAPER,
                                                       v["video_url"])["source_host"],
                     "has_duration": v["result"].get("videoDuration") is not None}
    except provider.ProviderError as e:
        validated = {"error": e.code}
    return {"stage": "inspect", "run_id": RUN_ID, "dataset_id": DATASET_ID,
            "item_count": len(items) if isinstance(items, list) else None,
            "validated": validated,
            "redaction": "raw media URL never printed"}


def complete(env) -> dict:
    get = _get(env)
    library_before = get(f"loom_reaction_library?platform_content_id=eq.{CID}&select=id")
    if library_before:
        return {"stage": "complete", "already_acquired": True, "library_row": library_before[0]}

    import ledger as L
    led = L.http_ledger(env["LOOM_SUPABASE_URL"], env["LOOM_SUPABASE_SERVICE_ROLE_KEY"])
    io = _live_io(env, ledger=led)
    job = get(f"loom_acquisition_jobs?id=eq.{GATE2_JOB_ID}&select=*")[0]
    pre = {"job": job, "actor": provider.ACTOR_REEL_SCRAPER, "content_id": CID,
           "fingerprint": job.get("duplicate_fingerprint") or CID,
           "canonical_url": CANONICAL}

    items = io.transport.get_dataset_items(DATASET_ID)
    validated = provider.validate_output(items, content_id=CID)
    downloader = lr.build_bounded_downloader(
        io, max_bytes=int(job.get("max_bytes") or lane.PILOT_MAX_BYTES),
        max_duration=float(job.get("max_duration_seconds") or lane.PILOT_MAX_DURATION_SECONDS))
    meta = downloader(validated["video_url"])
    provenance = provider.build_provenance(provider.ACTOR_REEL_SCRAPER, validated["video_url"])
    library = lr.build_finalizer(io, pre=pre)(meta, provenance)

    # --- durable job outcome ------------------------------------------------
    job_patch = {
        "status": SUCCESS_STATUS,
        "status_reason": (f"completed locally from provider run {RUN_ID} after the "
                          f"response-envelope fix; provider usage ${USAGE_MICRO/1_000_000:.4f}"),
        "asset_provenance": {"adapter": provider.adapter_name(provider.ACTOR_REEL_SCRAPER),
                             "provider": provider.PROVIDER_APIFY,
                             "run_id": RUN_ID,
                             "source_host": provenance.get("source_host", "")},
        "updated_at": _now_iso(),
    }
    patch(env, "loom_acquisition_jobs", f"id=eq.{GATE2_JOB_ID}", job_patch)

    # --- ledger: correct the audit row, then reconcile exactly once ---------
    audit_patch = {"run_id": RUN_ID, "status": SUCCESS_STATUS,
                   "cost_actual_micro": USAGE_MICRO,
                   "output_ref": (f"actor run SUCCEEDED; dataset {DATASET_ID}; "
                                  f"completed locally"),
                   "actor_build": "0.0.569"}
    audit_rows = patch(env, "loom_provider_runs",
                       f"run_id=eq.{FALLBACK_RUN_ID}&status=eq.failed_cost_unknown",
                       audit_patch)
    reconciliation = {"audit_row_updated": len(audit_rows) == 1}
    if reconciliation["audit_row_updated"]:
        ceiling = (get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]
        spent = int(ceiling.get("lifetime_spent_micro") or 0)
        counts = int(ceiling.get("acquisitions_succeeded") or 0)
        delta = USAGE_MICRO - RESERVED_MICRO            # +1000 over the held reservation
        rows = patch(env, "loom_provider_ceiling",
                     f"id=eq.1&lifetime_spent_micro=eq.{spent}&acquisitions_succeeded=eq.{counts}",
                     {"lifetime_spent_micro": max(0, spent + delta),
                      "acquisitions_succeeded": counts + 1,
                      "updated_at": _now_iso()})
        reconciliation.update({"ceiling_updated": len(rows) == 1,
                               "delta_micro": delta,
                               "before": {"spent": spent, "succeeded": counts},
                               "after": ({"spent": rows[0]["lifetime_spent_micro"],
                                          "succeeded": rows[0]["acquisitions_succeeded"]}
                                         if rows else None)})
    else:
        reconciliation["note"] = "audit row already reconciled (idempotent no-op)"

    return {"stage": "complete", "run_id": RUN_ID,
            "library": {"asset_filename": library.get("asset_filename"),
                        "asset_hash": library.get("asset_hash"),
                        "size_bytes": library.get("size_bytes"),
                        "duration_seconds": library.get("duration_seconds"),
                        "path": library.get("path")},
            "media": {k: meta.get(k) for k in ("size_bytes", "duration_seconds", "width",
                                               "height", "video_codec", "has_audio")},
            "job": job_patch,
            "ledger": reconciliation}


def verify(env) -> dict:
    get = _get(env)
    job = (get(f"loom_acquisition_jobs?id=eq.{GATE2_JOB_ID}&select=*") or [{}])[0]
    gate1 = (get(f"loom_acquisition_jobs?id=eq.{GATE1_JOB_ID}&select=*") or [{}])[0]
    lib = get(f"loom_reaction_library?platform_content_id=eq.{CID}&select=*")
    runs = get("loom_provider_runs?select=run_id,status,cost_actual_micro,acquisition_job_id")
    ceiling = (get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]
    files = sorted(p.name for p in LIB_DIR.iterdir() if p.is_file()) if LIB_DIR.exists() else []
    tmp_files = sorted(p.name for p in TMP_DIR.iterdir()) if TMP_DIR.exists() else []
    return {"stage": "verify",
            "job": {k: job.get(k) for k in ("status", "status_reason", "retry_count",
                                            "asset_provenance")},
            "library_row": lib[0] if lib else None,
            "library_files": files, "tmp_files": tmp_files,
            "runs": runs, "ceiling": ceiling,
            "gate1": {k: gate1.get(k) for k in ("status", "retry_count", "canonical_url")}}


def card_acquired(env) -> dict:
    """Render the ACQUIRED card from durable rows (so no retry is needed)."""
    import acquisition_card as card
    import acquisition_ops as ops

    get = _get(env)
    state = json.loads((Path(__file__).resolve().parent
                        / ".live-proof-state.json").read_text())
    job = (get(f"loom_acquisition_jobs?id=eq.{GATE2_JOB_ID}&select=*") or [{}])[0]
    lib = (get(f"loom_reaction_library?platform_content_id=eq.{CID}&select=*") or [{}])[0]
    ceiling = (get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]
    audit = (get(f"loom_provider_runs?acquisition_job_id=eq.{GATE2_JOB_ID}"
                 "&status=eq.acquired&select=*") or [{}])[0]

    ctx = ops.LaneContext(
        ref=CID, platform="instagram", content_id=CID, canonical_url=CANONICAL,
        state=card.STATE_ACQUIRED,
        duration_seconds=lib.get("duration_seconds"), size_bytes=lib.get("size_bytes"),
        video_codec="h264", audio_codec="aac", has_audio=True,
        width=720, height=1280,
        actual_cost_micro=int(audit.get("cost_actual_micro") or 0),
        ceiling_micro=ceiling.get("ceiling_micro"),
        remaining_acquisitions=int(ceiling.get("max_acquisitions") or 0)
                              - int(ceiling.get("acquisitions_succeeded") or 0),
        library={"status": "acquired", "asset_filename": lib.get("asset_filename", ""),
                 "sha256": lib.get("asset_hash", "")},
        preview={"kind": "local_asset", "local_filename": lib.get("asset_filename", "")},
        provenance={"adapter": (job.get("asset_provenance") or {}).get("adapter", ""),
                    "actor": job.get("actor") or "",
                    "run_id": (job.get("asset_provenance") or {}).get("run_id", "")},
        tenant_id="dreemzy", profile_id="acquisition-live-proof", fixture=False,
    )
    payload = {"embeds": [card.format_acquisition_embed(ctx.view_model())],
               "components": card.format_acquisition_buttons(ctx.view_model())}
    api = "https://discord.com/api/v10"
    headers = {"Authorization": sc._auth("Bo" + "t ", env["LOOM_DISCORD_BOT_TOKEN"]),
               "Content-Type": "application/json", "User-Agent": "LOOM-Complete/1.0"}
    url = f"{api}/channels/{state['channel_id']}/messages/{state['message_id']}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers,
                                 method="PATCH")
    with urllib.request.urlopen(req, timeout=25) as r:
        after = json.loads(r.read().decode() or "{}")
    embed = (after.get("embeds") or [{}])[0]
    fields = {f["name"]: f["value"] for f in embed.get("fields", [])}
    return {"stage": "card-acquired",
            "message_id": after.get("id"),
            "same_message": after.get("id") == state["message_id"],
            "title": embed.get("title"),
            "library_field": fields.get("Reaction Library"),
            "cost_field": fields.get("Cost"),
            "preview_field": fields.get("Preview"),
            "provenance_field": fields.get("Provenance"),
            "buttons": [[b.get("label") for b in row.get("components", [])]
                        for row in after.get("components", [])],
            "edited_timestamp": after.get("edited_timestamp")}


def card_attach(env) -> dict:
    """Live repair: attach what Discord can carry and keep every action honest.

    Uses the shared oversize reason so the card's Preview field and the actual
    upload decision can never disagree. No provider call, no download: the asset
    is only read, and its SHA-256 is verified unchanged afterwards.
    """
    import acquisition_card as card
    import acquisition_ops as ops
    import discord_media as dm

    get = _get(env)
    state = json.loads((Path(__file__).resolve().parent
                        / ".live-proof-state.json").read_text())
    job = (get(f"loom_acquisition_jobs?id=eq.{GATE2_JOB_ID}&select=*") or [{}])[0]
    lib = (get(f"loom_reaction_library?platform_content_id=eq.{CID}&select=*") or [{}])[0]
    ceiling = (get("loom_provider_ceiling?select=*&id=eq.1") or [{}])[0]
    audit = (get(f"loom_provider_runs?acquisition_job_id=eq.{GATE2_JOB_ID}"
                 "&status=eq.acquired&select=*") or [{}])[0]
    asset = LIB_DIR / str(lib.get("asset_filename") or "")
    digest = str(lib.get("asset_hash") or "")
    hash_before = dm.sha256_file(asset)

    limit = dm.guild_upload_limit(env["LOOM_DISCORD_BOT_TOKEN"], GUILD_ID)
    size = asset.stat().st_size
    fits = size <= limit["limit_bytes"]
    library_url = f"http://127.0.0.1:8787/library/{digest}"

    preview = ({"kind": "video_attachment", "local_filename": asset.name}
               if fits else
               {"kind": "local_asset", "local_filename": asset.name,
                "attach_reason": dm.oversize_reason(size, limit["limit_bytes"],
                                                    limit["premium_tier"])})
    ctx = ops.LaneContext(
        ref=CID, platform="instagram", content_id=CID, canonical_url=CANONICAL,
        state=card.STATE_ACQUIRED,
        duration_seconds=lib.get("duration_seconds"), size_bytes=lib.get("size_bytes"),
        video_codec="h264", audio_codec="aac", has_audio=True, width=720, height=1280,
        actual_cost_micro=int(audit.get("cost_actual_micro") or 0),
        ceiling_micro=ceiling.get("ceiling_micro"),
        remaining_acquisitions=int(ceiling.get("max_acquisitions") or 0)
                              - int(ceiling.get("acquisitions_succeeded") or 0),
        library={"status": "acquired", "asset_filename": asset.name,
                 "sha256": digest},
        preview=preview,
        provenance={"adapter": (job.get("asset_provenance") or {}).get("adapter", ""),
                    "actor": job.get("actor") or "",
                    "run_id": (job.get("asset_provenance") or {}).get("run_id", "")},
        library_url=library_url,
        tenant_id="dreemzy", profile_id="acquisition-live-proof", fixture=False,
    )
    vm = ctx.view_model()
    embed = card.format_acquisition_embed(vm)
    components = card.format_acquisition_buttons(vm, library_url=library_url)

    result = dm.attach_acquired_media(
        token=env["LOOM_DISCORD_BOT_TOKEN"], channel_id=state["channel_id"],
        message_id=state["message_id"], asset_path=asset, embed=embed,
        components=components, limit_bytes=limit["limit_bytes"],
        tier=limit["premium_tier"], poster_dir=LIB_DIR)

    hash_after = dm.sha256_file(asset)
    return {"stage": "card-attach",
            "guild": limit, "asset_bytes": size, "fits": fits,
            "decision": {k: result.get(k) for k in ("mode", "playable", "reason",
                                                    "poster_bytes", "upload_name")},
            "attachment_urls": result.get("attachment_urls", []),
            "poster_extraction": result.get("poster_extraction"),
            "asset_sha256_unchanged": hash_before == hash_after,
            "asset_sha256": hash_after,
            "library_url": library_url,
            "buttons": [[b.get("label") for b in row.get("components", [])]
                        for row in components],
            "preview_field": card.preview_field(vm)}


def _now_iso() -> str:
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())


def main() -> int:
    env = load_env()
    stage = next((a for a in sys.argv[1:] if a.startswith("--")), "")
    if stage == "--inspect":
        out = inspect(env)
    elif stage == "--complete":
        out = complete(env)
    elif stage == "--verify":
        out = verify(env)
    elif stage == "--card-acquired":
        out = card_acquired(env)
    elif stage == "--card-attach":
        out = card_attach(env)
    else:
        print("usage: complete_attempt.py --inspect | --complete | --verify | "
              "--card-acquired | --card-attach")
        return 2
    if stage == "--complete":
        OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
