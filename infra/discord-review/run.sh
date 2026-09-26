#!/usr/bin/env bash
# Launch the LOOM Discord review adapter with secrets sourced from
# ~/.openclaw/.env (never printed, never committed).
set -euo pipefail
set -a
for v in LOOM_DISCORD_APP_ID LOOM_DISCORD_PUBLIC_KEY LOOM_DISCORD_BOT_TOKEN \
         LOOM_SUPABASE_URL LOOM_SUPABASE_SERVICE_ROLE_KEY LOOM_N8N_API_KEY \
         LOOM_DISCORD_CLIENT_SECRET LOOM_DISCORD_OAUTH_REDIRECT_URI \
         LOOM_EDITOR_PUBLIC_URL LOOM_EDITOR_CORS_ORIGINS LOOM_EDITOR_BASE_URL LOOM_EDITOR_ORIGIN \
         LOOM_GHL_TOKEN LOOM_GHL_LOCATION_ID LOOM_APIFY_API_TOKEN \
         LOOM_ACQUISITION_LIVE LOOM_ACQUISITION_DURABLE_STORE \
         LOOM_MANUAL_INTAKE_CHANNEL LOOM_MANUAL_INTAKE_CHANNEL_ID \
         LOOM_MANUAL_REVIEW_CHANNEL_ID LOOM_MANUAL_INTAKE_TENANT \
         LOOM_GHL_INTAKE_FOLDER_ID LOOM_MANUAL_MEDIA_MAX_BYTES; do
  line=$(grep "^${v}=" ~/.openclaw/.env | tail -n1 || true)
  [ -n "$line" ] && eval "export $line"
done
set +a

# Approved acquisition lane (Gate 1 durable persistence ON, live spend OFF).
# The live switch is the ONLY thing that can enable a paid acquisition, and it
# enables nothing on its own: the runner still re-checks every gate per click and
# requires a durable reservation before it will call the provider.
export LOOM_ACQUISITION_DURABLE_STORE="${LOOM_ACQUISITION_DURABLE_STORE:-1}"
export LOOM_ACQUISITION_LIVE="${LOOM_ACQUISITION_LIVE:-0}"
export LOOM_N8N_REVIEW_WEBHOOK="https://fns2.app.n8n.cloud/webhook/loom-review-decision"
exec python3 adapter.py
