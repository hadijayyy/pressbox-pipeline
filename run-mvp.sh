#!/bin/bash
cd ~/.hermes/pressbox-pipeline
STATUS_FILE="/tmp/pressbox-last-status"
LOG_FILE="/tmp/pressbox-mvp.log"

run_pipeline() {
    python3 -u pressbox-mvp.py 2>&1 | tee -a "$LOG_FILE"
}

summary() {
    local outcome="$1"
    local label="$2"
    echo ""
    echo "━━━━━━━━━━━━━━━━"
    echo "📊 Pressbox MVP $label"
    echo "⏰ $(date '+%H:%M WIB, %d %b %Y')"
}

# First attempt
if run_pipeline; then
    echo "ok $(date -Iseconds)" > "$STATUS_FILE"
    summary "✅"
    exit 0
fi

# Retry 1: after 60s
echo "[watchdog] Try 1 failed, retrying in 60s..." | tee -a "$LOG_FILE" >&2
sleep 60

if run_pipeline; then
    echo "ok-retry1 $(date -Iseconds)" > "$STATUS_FILE"
    summary "✅ (retry 1)"
    exit 0
fi

# Retry 2: after 120s (exponential backoff)
echo "[watchdog] Try 2 failed, retrying in 120s..." | tee -a "$LOG_FILE" >&2
sleep 120

if run_pipeline; then
    echo "ok-retry2 $(date -Iseconds)" > "$STATUS_FILE"
    summary "✅ (retry 2)"
    exit 0
fi

echo "fail $(date -Iseconds)" > "$STATUS_FILE"
summary "❌ FAILED"
echo "🔍 Check: tail -50 $LOG_FILE"
exit 1
