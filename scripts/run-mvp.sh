#!/bin/bash
cd ~/.hermes/pressbox-pipeline

# Anti-bot: 2-layer randomization
# Layer 1: Random gap check (50-90 min since last POST, not last run)
# Layer 2: Random sleep 0-20 min (breaks exact :00/:30 pattern)
# Lockfile prevents concurrent runs

LOCKFILE="/tmp/pressbox-mvp.lock"
exec 200>"$LOCKFILE"
flock -n 200 || exit 0  # Another instance running, skip silently

STATUS_FILE="/tmp/pressbox-last-post"
MIN_GAP=$((3000 + RANDOM % 2401))   # 50-90 min

if [ -f "$STATUS_FILE" ]; then
    LAST_TS=$(cat "$STATUS_FILE" | grep -oP '\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}' | head -1)
    if [ -n "$LAST_TS" ]; then
        LAST_EPOCH=$(date -d "$LAST_TS" +%s 2>/dev/null)
        NOW_EPOCH=$(date +%s)
        if [ -n "$LAST_EPOCH" ]; then
            ELAPSED=$((NOW_EPOCH - LAST_EPOCH))
            if [ $ELAPSED -lt $MIN_GAP ]; then
                exit 0  # Silent skip — not enough time since last POST
            fi
        fi
    fi
fi

# Layer 2: Random sleep to break exact minute pattern
SLEEP_SEC=$((RANDOM % 1201))  # 0-20 min
sleep "$SLEEP_SEC"

OUTPUT=""
EXIT_CODE=0

# First attempt
OUTPUT=$(python3 -u pressbox-mvp.py 2>/dev/null) || {
    echo "[watchdog] First attempt failed, retrying in 60s..." >&2
    sleep 60
    OUTPUT=$(python3 -u pressbox-mvp.py 2>/dev/null) || {
        EXIT_CODE=1
    }
}

# Only mark "posted" when OUTPUT is non-empty (post actually happened)
# This allows re-try at next :30 if no good content was found
if [ $EXIT_CODE -eq 0 ] && [ -n "$OUTPUT" ]; then
    echo "ok $(date -Iseconds)" > "$STATUS_FILE"
fi

# Output → stdout (delivered to Telegram topic 20467 by Hermes cron)
if [ -n "$OUTPUT" ]; then
    echo "$OUTPUT"
elif [ $EXIT_CODE -ne 0 ]; then
    echo "❌ Pressbox MVP failed at $(TZ=Asia/Jakarta date '+%H:%M WIB')"
    echo "📁 Log: ~/.hermes/pressbox-pipeline/"
fi

exit $EXIT_CODE
