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

# Pipeline: jitter (0-30s) + scrape + LLM all inside Python
# Log to file, stdout only has summary
python3 -u pressbox-mvp.py --with-jitter > /tmp/pressbox-mvp.log 2>&1
EXIT_CODE=$?

NOW_WIB=$(TZ=Asia/Jakarta date '+%H:%M WIB')

if [ $EXIT_CODE -eq 0 ] && [ -f /tmp/pressbox-last-report ]; then
    echo "ok $(date -Iseconds)" > /tmp/pressbox-last-post
    REPORT=$(cat /tmp/pressbox-last-report)
    echo "$REPORT"
    notify "$REPORT"

elif [ $EXIT_CODE -ne 0 ]; then
    MSG="❌ Pressbox MVP failed @ $NOW_WIB"
    echo "$MSG"
    notify "$MSG"
fi

exit $EXIT_CODE
