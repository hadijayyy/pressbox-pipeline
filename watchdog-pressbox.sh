#!/bin/bash
STATUS_FILE="/tmp/pressbox-last-status"
MAX_AGE=7200

if [ ! -f "$STATUS_FILE" ]; then
    echo "No status file — running pipeline"
    cd ~/.hermes/pressbox-pipeline && bash run-mvp.sh
    exit $?
fi

STATUS=$(cat "$STATUS_FILE")
LABEL="${STATUS%% *}"
TS="${STATUS#* }"

NOW=$(date +%s)
THEN=$(date -d "$TS" +%s 2>/dev/null || echo 0)
AGE=$(( NOW - THEN ))

if [[ "$LABEL" == "ok"* ]] && [ "$AGE" -lt "$MAX_AGE" ]; then
    exit 0
fi

echo "Watchdog: status=$LABEL age=${AGE}s — retrying pipeline"
cd ~/.hermes/pressbox-pipeline && bash run-mvp.sh
