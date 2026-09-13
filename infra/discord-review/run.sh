#!/usr/bin/env bash
# Launch the LOOM Discord review adapter with secrets sourced from
# ~/.openclaw/.env (never printed, never committed).
set -euo pipefail
set -a
for v in LOOM_DISCORD_APP_ID LOOM_DISCORD_PUBLIC_KEY LOOM_DISCORD_BOT_TOKEN \
         LOOM_SUPABASE_URL LOOM_SUPABASE_SERVICE_ROLE_KEY LOOM_N8N_API_KEY; do
  line=$(grep "^${v}=" ~/.openclaw/.env | tail -n1)
  [ -n "$line" ] && eval "export $line"
done
set +a
export LOOM_N8N_REVIEW_WEBHOOK="https://fns2.app.n8n.cloud/webhook/loom-review-decision"
exec python3 adapter.py
