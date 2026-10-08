#!/usr/bin/env bash
# Run the app with gunicorn outside Docker (daemonized, logs in instance/).
set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
PID="$DIR/instance/gunicorn.pid"

case "${1:-}" in
  up)
    if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
      echo "Already running (PID $(cat "$PID"))"
      exit 0
    fi
    source "$DIR/.venv/bin/activate"
    gunicorn --chdir "$DIR" \
             --bind 0.0.0.0:5000 \
             --workers 2 \
             --preload \
             --daemon \
             --pid "$PID" \
             --access-logfile "$DIR/instance/access.log" \
             --error-logfile "$DIR/instance/error.log" \
             "app:create_app()"
    echo "Running on :5000"
    ;;
  down)
    if [ ! -f "$PID" ]; then
      echo "Not running"
      exit 0
    fi
    if kill "$(cat "$PID")" 2>/dev/null; then
      echo "Stopped"
    else
      echo "Not running (stale PID — cleaned up)"
    fi
    rm -f "$PID"
    ;;
  *)
    echo "Usage: scripts/serve.sh {up|down}"
    exit 1
    ;;
esac
