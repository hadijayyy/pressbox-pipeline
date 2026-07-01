#!/bin/bash
STATUS_FILE="/tmp/pressbox-last-post"

# Load @Szejay_bot token
set -a; source ~/.hermes/.env 2>/dev/null; set +a
SZEJAY_CHAT="1022032312"

notify() {
    [ -z "$SZEJAY_BOT_TOKEN" ] && return
    curl -s -X POST "https://api.telegram.org/bot${SZEJAY_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=$SZEJAY_CHAT" \
        --data-urlencode "text=$1" > /dev/null 2>&1 &
}

# Check if last run was ok and recent
if [ -f "$STATUS_FILE" ]; then
    STATUS=$(cat "$STATUS_FILE" | awk '{print $1}')
    LAST_TS=$(cat "$STATUS_FILE" | awk '{print $2}')
    NOW=$(date +%s)
    LAST_EPOCH=$(date -d "$LAST_TS" +%s 2>/dev/null || echo 0)
    DIFF=$(( NOW - LAST_EPOCH ))
    
    if [ "$STATUS" = "ok" ] && [ $DIFF -lt 7200 ]; then
        exit 0  # Silent exit — last run ok and recent
    fi
fi

# Last run was bad or stale — re-run pipeline
cd ~/.hermes/pressbox-pipeline
OUTPUT=$(python3 -u pressbox-mvp.py 2>/dev/null)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "ok $(date -Iseconds)" > "$STATUS_FILE"
fi

NOW_WIB=$(TZ=Asia/Jakarta date '+%H:%M WIB')
if [ -n "$OUTPUT" ]; then
    echo "$OUTPUT"
    notify "🔄 Watchdog re-post @ $NOW_WIB
$OUTPUT"
elif [ $EXIT_CODE -ne 0 ]; then
    MSG="❌ Watchdog failed @ $NOW_WIB"
    echo "$MSG"
    notify "$MSG"
fi

exit $EXIT_CODE
