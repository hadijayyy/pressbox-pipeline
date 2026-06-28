#!/bin/bash
cd "$(dirname "$0")"
STATUS_FILE="/tmp/pressbox-last-status"

# First attempt
if python3 -u pressbox-mvp.py; then
    echo "ok $(date -Iseconds)" > "$STATUS_FILE"
    exit 0
fi

# Retry once after 60s
echo "[watchdog] First attempt failed, retrying in 60s..." >&2
sleep 60

if python3 -u pressbox-mvp.py; then
    echo "ok-retry $(date -Iseconds)" > "$STATUS_FILE"
    exit 0
fi

echo "fail $(date -Iseconds)" > "$STATUS_FILE"
exit 1
