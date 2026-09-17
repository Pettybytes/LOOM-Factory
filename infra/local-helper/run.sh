#!/usr/bin/env bash
# Launch the LOOM local acquisition helper (127.0.0.1 only).
# Sources Supabase creds from ~/.openclaw/.env by NAME (never printed).
set -euo pipefail
cd "$(dirname "$0")"
set -a
for v in LOOM_SUPABASE_URL LOOM_SUPABASE_SERVICE_ROLE_KEY; do
  line=$(grep "^${v}=" ~/.openclaw/.env | tail -n1 || true)
  [ -n "$line" ] && eval "export $line"
done
set +a
exec python3 helper.py
