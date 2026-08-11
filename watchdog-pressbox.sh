#!/bin/bash
# Pressbox Watchdog — only re-runs pipeline if last status is crash or stale.
# Normal skips are tagged SKIP so watchdog does NOT retry.
STATUS_FILE="/tmp/pressbox-last-status"
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

# SKIP labels: watchdog should NOT retry
if [[ "$LABEL" == "SKIP"* ]]; then
    echo "✅ Watchdog: status=$LABEL (normal skip) — not retrying"
    exit 0
fi

# OK labels within MAX_AGE: watchdog should NOT retry
if [[ "$LABEL" == "ok"* ]] && [ "$AGE" -lt "$MAX_AGE" ]; then
    exit 0
fi

echo "⚠️ Watchdog: status=$LABEL age=${AGE}s — retrying pipeline"
bash "$HOME/.hermes/scripts/run-mvp.sh" --watchdog
