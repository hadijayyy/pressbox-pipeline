#!/bin/bash
# Watchdog: checks if main pressbox run succeeded recently.
# Runs at :15 past each hour (cron: 15 * * * *)
# Silent (exit 0, empty stdout) if everything OK.

cd "$(dirname "$0")"
STATUS_FILE="/tmp/pressbox-last-status"
MAX_AGE=7200  # 2 hours in seconds

# No status file = never ran, trigger run
if [ ! -f "$STATUS_FILE" ]; then
    echo "[watchdog] No status file — running pipeline" >&2
    exec bash run-mvp.sh
fi

STATUS=$(cat "$STATUS_FILE")
TYPE=$(echo "$STATUS" | awk '{print $1}')
TS_STR=$(echo "$STATUS" | awk '{print $2}')

# Parse ISO timestamp to epoch
if [ -n "$TS_STR" ]; then
    TS_EPOCH=$(date -d "$TS_STR" +%s 2>/dev/null || echo 0)
else
    TS_EPOCH=0
fi

NOW_EPOCH=$(date +%s)
AGE=$((NOW_EPOCH - TS_EPOCH))

# Already ok and recent — silent
if [ "$TYPE" = "ok" ] || [ "$TYPE" = "ok-retry" ]; then
    if [ "$AGE" -lt "$MAX_AGE" ]; then
        exit 0  # silent, nothing to report
    fi
fi

# Last run failed or too old — retry
echo "[watchdog] Last status: $TYPE (${AGE}s ago) — retrying pipeline" >&2
exec bash run-mvp.sh
