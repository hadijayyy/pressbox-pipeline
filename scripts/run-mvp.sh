#!/bin/bash
cd ~/.hermes/pressbox-pipeline
POST_MARKER="/tmp/pressbox-posted-this-run"

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
flock -n 200 || exit 75
rm -f "$POST_MARKER" /tmp/pressbox-last-report

case "${1:-}" in
    ""|--watchdog) ;;
    *) echo "Usage: $0 [--watchdog]" >&2; exit 2 ;;
esac

PIPE_ARGS=(--with-jitter)
[ "${1:-}" = "--watchdog" ] && PIPE_ARGS+=(--watchdog)

retryable_failure() {
    grep -Eiq 'HTTP 429|HTTP 5[0-9][0-9]|timeout|timed out|connection reset|temporarily unavailable' /tmp/pressbox-mvp.log
}

# Retry once only for transient upstream failures. Editorial rejects are deterministic.
for RETRY in 1 2; do
    python3 -u pressbox-mvp.py "${PIPE_ARGS[@]}" > /tmp/pressbox-mvp.log 2>&1
    EXIT_CODE=$?
    [ $EXIT_CODE -eq 0 ] && break
    [ "$RETRY" -eq 1 ] && retryable_failure || break
    sleep 120
    echo "Retry $RETRY..." >> /tmp/pressbox-mvp.log
done

NOW_WIB=$(TZ=Asia/Jakarta date '+%H:%M WIB')

# Success with posted content
if [ $EXIT_CODE -eq 0 ] && [ -f "$POST_MARKER" ] && [ -f /tmp/pressbox-last-report ]; then
    TS=$(date -Iseconds)
    echo "ok $TS" > /tmp/pressbox-last-post
    echo "ok $TS" > /tmp/pressbox-last-status
    REPORT=$(cat /tmp/pressbox-last-report)
    echo "$REPORT"
    notify "$REPORT"
# Lock held — another instance already running. Not an error.
elif [ $EXIT_CODE -eq 75 ]; then
    echo "MVP: lock held (another instance running)"
    TS=$(date -Iseconds)
    echo "LOCKED $TS" > /tmp/pressbox-last-status
    exit 0
# Normal skip — exit 0, write SKIP status so watchdog doesn't retry
elif [ $EXIT_CODE -eq 0 ]; then
    SKIP_REASON=$(grep -E '^(Pipeline:.*Skip|Pipeline:.*no eligible|Skip —)' /tmp/pressbox-mvp.log | tail -1 | sed 's/^Pipeline: *//' | sed 's/^Skip — *//')
    [ -z "$SKIP_REASON" ] && SKIP_REASON=$(grep -E 'Pipeline:' /tmp/pressbox-mvp.log | tail -1 | sed 's/^Pipeline: *//')
    [ -z "$SKIP_REASON" ] && SKIP_REASON="normal_skip"
    TS=$(date -Iseconds)
    echo "SKIP $TS $SKIP_REASON" > /tmp/pressbox-last-status
    echo "Pressbox MVP: SKIP — $SKIP_REASON"
    exit 0
# Actual error — notify and propagate exit code
else
    STAGE_REASON=$(grep -E 'Pipeline:|Skip —|generated slides failed checks|CRASH:|Post failed:' /tmp/pressbox-mvp.log | tail -1)
    [ -n "$STAGE_REASON" ] || STAGE_REASON="no stage reason captured"
    MSG="❌ Pressbox MVP failed @ $NOW_WIB — $STAGE_REASON"
    echo "$MSG"
    tail -20 /tmp/pressbox-mvp.log >&2
    notify "$MSG"
    TS=$(date -Iseconds)
    echo "FAILED $TS $STAGE_REASON" > /tmp/pressbox-last-status
fi

exit $EXIT_CODE
