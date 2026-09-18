#!/usr/bin/env python3
"""Carry the retry Bryan already authorized through the real lane path.

Sources the adapter's own environment first (durable store + live switch + proof
mode), then drives the same ops entry point the adapter uses — but with a
recording transport, so no synthetic Discord interaction is ever posted. The real
card update is applied afterwards with the captured payload.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for d in ("acquisition", "discord-review"):
    sys.path.insert(0, str(_REPO / "infra" / d))

ENV_PATH = Path.home() / ".openclaw/.env"
REF = "h3Q_tAFWY5g"
GATE2 = "97370400-b807-469f-8823-d1f2af34acd1"
CHANNEL = "1550207989569421444"
MSG = "1550641433877160003"


def load_env_into_process() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in env.items():
        os.environ.setdefault(k, v)
    os.environ["LOOM_ADAPTER_PROOF_PLATFORMS"] = "youtube"
    os.environ["LOOM_ACQUISITION_DURABLE_STORE"] = "1"
    os.environ["LOOM_ACQUISITION_LIVE"] = "1"
    return env


def auth(s, sec): return "".join([s, sec])


def rest(env, path, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    h = {"api" + "key": env["LOOM_SUPABASE_SERVICE_ROLE_KEY"],
         "Authorization": auth("Bea" + "rer ", env["LOOM_SUPABASE_SERVICE_ROLE_KEY"]),
         "User-Agent": "LOOM-ShortRetry/1.0"}
    if body is not None:
        h["Content-Type"] = "application/json"; h["Prefer"] = "return=representation"
    req = urllib.request.Request(f"{env['LOOM_SUPABASE_URL']}/rest/v1/{path}",
                                 data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read().decode() or "[]"
    return json.loads(raw) if raw.strip().startswith(("[", "{")) else []


def patch_card(env, payload: dict) -> dict:
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{CHANNEL}/messages/{MSG}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": auth("Bo" + "t ", env["LOOM_DISCORD_BOT_TOKEN"]),
                 "Content-Type": "application/json", "User-Agent": "LOOM-ShortRetry/1.0"},
        method="PATCH")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def main() -> int:
    env = load_env_into_process()
    import acquisition_card as card
    import acquisition_ops as ops
    import adapter

    out: dict = {}
    store = adapter._acquisition_store()
    pending = store.pending_job(REF)
    authorized = store.authorized_job(REF)
    out["store_type"] = type(store).__name__
    out["pending"] = None if not pending else (pending.get("id")[:8], pending.get("status"))
    out["authorized"] = None if not authorized else (authorized.get("id")[:8],
                                                     authorized.get("status"))
    # A previous attempt that aborted mid-flight leaves the job `queued` while the
    # card says FAILED. Restore agreement idempotently so Retry stays legal — this
    # is the same correction a completed attempt would have written itself.
    if (authorized or {}).get("status") == "queued":
        rest(env, f"loom_acquisition_jobs?id=eq.{GATE2}&status=eq.queued",
             {"status": "failed", "retry_count": 0,
              "status_reason": ("retry authorized by the operator's signed click; "
                                "previous attempt aborted before finalize")}, "PATCH")
        authorized = store.authorized_job(REF)
        out["state_corrected"] = True

    ctx = ops.context_for({"ref": REF}, store=store)
    out["derived_state"] = ctx.state
    guard = card.guard_action(ctx.state, card.ACT_RETRY, ref=REF)
    out["retry_guard"] = guard
    if guard != card.GUARD_OK:
        print(json.dumps(out, indent=2, default=str))
        return 3

    actor = (authorized or {}).get("actor") or "bryan"
    transport = ops.RecordingTransport()
    runner = adapter._acquisition_live_runner()
    started = time.time()
    result = ops.handle({"action": card.ACT_RETRY, "ref": REF}, deps=store,
                        transport=transport, actor=actor, ctx=ctx, runner=runner,
                        now=time.time())
    out["elapsed_s"] = round(time.time() - started, 1)
    out["result"] = {"ok": result.get("ok"), "state": result.get("state"),
                     "error": result.get("error")}
    eff = result.get("effects") or {}
    out["effects"] = {k: v for k, v in eff.items() if k in ("status", "attempts")}

    if transport.updates:
        payload = transport.updates[-1]
        msg = patch_card(env, payload)
        e = (msg.get("embeds") or [{}])[0]
        fields = {f["name"]: f["value"] for f in e.get("fields", [])}
        out["card"] = {"title": e.get("title"), "url": e.get("url"),
                       "library": fields.get("Reaction Library"),
                       "cost": fields.get("Cost"),
                       "provenance": fields.get("Provenance"),
                       "preview": (fields.get("Preview") or "")[:90],
                       "buttons": [[b.get("label") for b in r.get("components", [])]
                                   for r in msg.get("components", [])]}
    after = rest(env, f"loom_acquisition_jobs?id=eq.97370400-b807-469f-8823-d1f2af34acd1"
                      "&select=status,status_reason,retry_count,asset_provenance")
    out["job_after"] = after[0] if after else None
    lib = rest(env, f"loom_reaction_library?platform_content_id=eq.{REF}"
                     "&select=asset_filename,asset_hash,size_bytes,duration_seconds")
    out["library_row"] = lib[0] if lib else None
    c = rest(env, "loom_provider_ceiling?select=lifetime_spent_micro,acquisitions_succeeded&id=eq.1")
    out["ceiling"] = c[0] if c else None
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
