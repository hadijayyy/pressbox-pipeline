#!/bin/bash
cd ~/.hermes/pressbox-pipeline

# Load bot token for @Szejay_bot notifications
set -a; source ~/.hermes/.env 2>/dev/null; set +a
SZEJAY_CHAT="1022032312"

notify() {
    # Send status to @Szejay_bot. $1=message text
    [ -z "$SZEJAY_BOT_TOKEN" ] && return
    curl -s -X POST "https://api.telegram.org/bot${SZEJAY_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=$SZEJAY_CHAT" \
        --data-urlencode "text=$1" > /dev/null 2>&1 &
}

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
SLEEP_SEC=$((RANDOM % 121))  # 0-2 min (was 0-20 min, caused 300s timeout)
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
# + send to @Szejay_bot
NOW_WIB=$(TZ=Asia/Jakarta date '+%H:%M WIB')
if [ -n "$OUTPUT" ]; then
    echo "$OUTPUT"
    notify "✅ Posted @ $NOW_WIB
$OUTPUT"
elif [ $EXIT_CODE -ne 0 ]; then
    MSG="❌ Pressbox MVP failed @ $NOW_WIB"
    echo "$MSG"
    echo "📁 Log: ~/.hermes/pressbox-pipeline/"
    notify "$MSG"
fi

exit $EXIT_CODE
