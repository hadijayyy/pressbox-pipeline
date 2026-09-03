#!/bin/bash
# Pressbox Watchdog — only re-runs pipeline if last status is crash or stale.
# Normal skips are tagged SKIP so watchdog does NOT retry.
STATUS_FILE="/tmp/pressbox-last-status"
# Keep watchdog contract colocated with publisher wrapper.
PIPELINE_ROOT="$HOME/.hermes/pressbox-pipeline"
MAX_AGE=7200

if [ ! -f "$STATUS_FILE" ]; then
    echo "⚠️ No status file — running pipeline"
    bash "$HOME/.hermes/scripts/run-mvp.sh" --watchdog
    exit $?
fi

STATUS=$(cat "$STATUS_FILE")
LABEL="${STATUS%% *}"
TS="${STATUS#* }"

NOW=$(date +%s)
THEN=$(date -d "$TS" +%s 2>/dev/null || echo 0)
AGE=$(( NOW - THEN ))

# SKIP labels: watchdog should NOT retry. Silent — normal skip is not an alert.
if [[ "$LABEL" == "SKIP"* ]]; then
    exit 0
fi

# Auth failures are permanent until token refresh. Do not hammer Threads or
# make cron fail every hour; retry after status is manually reset.
if [[ "$LABEL" == "FAILED"* ]] && grep -Eiq 'access token|session has expired|OAuthException|Error validating access token' /tmp/pressbox-mvp.log 2>/dev/null; then
    TS=$(date -Iseconds)
    echo "BLOCKED $TS auth_token_expired" > "$STATUS_FILE"
    echo "Pressbox Watchdog: blocked — Threads access token expired; refresh token before retry"
    exit 0
fi

# BLOCKED labels: watchdog should NOT retry until operator fixes blocker.
if [[ "$LABEL" == "BLOCKED"* ]]; then
    exit 0
fi

# OK labels within MAX_AGE: watchdog should NOT retry
if [[ "$LABEL" == "ok"* ]] && [ "$AGE" -lt "$MAX_AGE" ]; then
    exit 0
fi

# RUNNING labels within MAX_AGE: watchdog should NOT retry (anti-overlap).
# Keep explicit log for operators; active pipeline remains untouched.
if [[ "$LABEL" == "RUNNING"* ]] && [ "$AGE" -lt "$MAX_AGE" ]; then
    echo "Watchdog: pipeline running (age=${AGE}s)"
    exit 0
fi

echo "⚠️ Watchdog: status=$LABEL age=${AGE}s — retrying pipeline"
bash "$HOME/.hermes/scripts/run-mvp.sh" --watchdog
