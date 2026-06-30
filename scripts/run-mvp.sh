#!/bin/bash
cd ~/.hermes/pressbox-pipeline

# Load bot token for @Szejay_bot notifications
set -a; source ~/.hermes/.env 2>/dev/null; set +a
SZEJAY_CHAT="1022032312"

notify() {
    [ -z "$SZEJAY_BOT_TOKEN" ] && return
    curl -s -X POST "https://api.telegram.org/bot${SZEJAY_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=$SZEJAY_CHAT" \
        --data-urlencode "text=$1" > /dev/null 2>&1 &
}

# Lockfile prevents concurrent runs
LOCKFILE="/tmp/pressbox-mvp.lock"
exec 200>"$LOCKFILE"
flock -n 200 || exit 0

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
                exit 0  # Silent skip
            fi
        fi
    fi
fi

# Pipeline: jitter (0-30s) + scrape + LLM all inside Python
# Budget: 30s jitter + 60s LLM × 3 retries + 30s article + 24s scrape = 264s max
OUTPUT=$(python3 -u pressbox-mvp.py --with-jitter 2>/dev/null)
EXIT_CODE=$?

# Mark posted only when OUTPUT non-empty
if [ $EXIT_CODE -eq 0 ] && [ -n "$OUTPUT" ]; then
    echo "ok $(date -Iseconds)" > "$STATUS_FILE"
fi

# Output + notify @Szejay_bot
NOW_WIB=$(TZ=Asia/Jakarta date '+%H:%M WIB')
if [ -n "$OUTPUT" ]; then
    echo "$OUTPUT"
    notify "✅ Posted @ $NOW_WIB
$OUTPUT"
elif [ $EXIT_CODE -ne 0 ]; then
    MSG="❌ Pressbox MVP failed @ $NOW_WIB"
    echo "$MSG"
    notify "$MSG"
fi

exit $EXIT_CODE
