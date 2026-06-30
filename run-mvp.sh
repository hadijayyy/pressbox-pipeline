#!/bin/bash
cd ~/.hermes/pressbox-pipeline
STATUS_FILE="/tmp/pressbox-last-status"
LOG_FILE="/tmp/pressbox-mvp.log"

run_pipeline() {
    python3 -u pressbox-mvp.py 2>&1 | tee -a "$LOG_FILE"
}

# First attempt
if run_pipeline; then
    echo "ok $(date -Iseconds)" > "$STATUS_FILE"
    # Summary footer for Telegram
    LAST=$(tail -1 "$LOG_FILE" 2>/dev/null)
    echo ""
    echo "━━━━━━━━━━━━━━━━"
    echo "📊 Pressbox MVP ✅"
    echo "⏰ $(date '+%H:%M WIB, %d %b %Y')"
    echo "📁 Sources: skysports, goal, bbc, fourfourtwo"
    exit 0
fi

# Retry once after 60s
echo "[watchdog] First attempt failed, retrying in 60s..." | tee -a "$LOG_FILE" >&2
sleep 60

if run_pipeline; then
    echo "ok-retry $(date -Iseconds)" > "$STATUS_FILE"
    echo ""
    echo "━━━━━━━━━━━━━━━━"
    echo "📊 Pressbox MVP ✅ (retry)"
    echo "⏰ $(date '+%H:%M WIB, %d %b %Y')"
    exit 0
fi

echo "fail $(date -Iseconds)" > "$STATUS_FILE"
echo ""
echo "━━━━━━━━━━━━━━━━"
echo "📊 Pressbox MVP ❌ FAILED"
echo "⏰ $(date '+%H:%M WIB, %d %b %Y')"
echo "🔍 Check: tail -50 $LOG_FILE"
exit 1
