#!/usr/bin/env bash
# Supervise the LOOM Discord review adapter: restart it on any exit, and tee
# the adapter's stdout/stderr to adapter.log so a crash is always diagnosable.
# Detach with: nohup ./supervise.sh > supervisor.log 2>&1 &
# (launchd was attempted but macOS TCC blocks it from reading ~/Desktop, so
# this restart loop is the supervision mechanism.)
set -u
cd "$(dirname "$0")"
while true; do
  echo "[supervise] starting adapter at $(date -u +%FT%TZ)"
  ./run.sh >> adapter.log 2>&1
  rc=$?
  echo "[supervise] adapter exited rc=$rc; restarting in 3s"
  sleep 3
done
