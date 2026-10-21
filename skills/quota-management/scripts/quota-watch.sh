#!/usr/bin/env bash
# Plain process loop. No model calls; exit 2 stops configuration-error loops.
set -u

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
PY=${QUOTA_WATCH_PYTHON:-python3}
WATCHER=$SELF_DIR/quota_watch.py
INTERVAL=${QUOTA_WATCH_INTERVAL:-900}
BASE=${QUOTA_WATCH_BASE:-}
LOGDIR=$BASE/logs
LOGFILE=$LOGDIR/quota-watch.log

if [ ! -f "$WATCHER" ]; then
  echo "quota-watch.sh: missing $WATCHER" >&2
  exit 1
fi

case "${1:-}" in
  --once)      exec "$PY" "$WATCHER" --once ;;
  -h|--help)   exec "$PY" "$WATCHER" --help ;;
  --detect)    exec "$PY" "$WATCHER" --detect ;;
  '')          : ;;
  *)           echo "quota-watch.sh: unknown argument: $1" >&2; exit 2 ;;
esac

if [ -z "$BASE" ]; then
  echo "set QUOTA_WATCH_BASE to a private run directory" >&2
  exit 2
fi
case "$INTERVAL" in
  ''|*[!0-9]*|0) echo "QUOTA_WATCH_INTERVAL must be a positive integer" >&2; exit 2 ;;
esac
command mkdir -p "$LOGDIR" "$BASE/alerts" || exit 1

stamp() { command date '+%Y-%m-%dT%H:%M:%S%z'; }

printf '%s  quota-watch started (pid %s, interval %ss)\n' "$(stamp)" "$$" "$INTERVAL" \
  | command tee -a "$LOGFILE"

trap 'printf "%s  quota-watch stopping (signal)\n" "$(stamp)" | command tee -a "$LOGFILE"; exit 0' \
  INT TERM

while :; do
  # A crashing cycle must not kill the watcher; it is logged and retried.
  "$PY" "$WATCHER" --once
  rc=$?
  if [ "$rc" -eq 2 ]; then exit 2; fi
  if [ "$rc" -ne 0 ]; then
    printf '%s  cycle returned non-zero\n' "$(stamp)" | command tee -a "$LOGFILE"
  fi
  command sleep "$INTERVAL"
done
