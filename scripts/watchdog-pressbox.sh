#!/bin/bash
STATUS_FILE="/tmp/pressbox-last-status"

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

# Send to Telegram topic directly
if [ -n "$OUTPUT" ]; then
    set -a
    source ~/.hermes/.env
    set +a
    CHAT_ID="${TELEGRAM_HOME_CHANNEL:-1022032312}"
    THREAD_ID="${TELEGRAM_HOME_CHANNEL_THREAD_ID:-57540}"
    MSG=$(echo "$OUTPUT" | tail -c 4000)
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$CHAT_ID" \
        -d message_thread_id="$THREAD_ID" \
        --data-urlencode text="$MSG" > /dev/null 2>&1
fi

exit $EXIT_CODE
