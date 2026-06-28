#!/usr/bin/env bash
# Direct pipeline runner — no circuit breaker.
# Pipeline handles its own errors (retries, exit codes).
cd /home/ubuntu/pressbox-pipeline

# Auto-install deps
python3 -c "import httpx, bs4, requests, dotenv" 2>/dev/null || \
  python3 -m pip install --quiet httpx beautifulsoup4 requests python-dotenv 2>/dev/null || true

# Run pipeline, capture output + exit code
OUTPUT=$(python3 -u pressbox-pipeline-v7.py 2>&1)
EXIT_CODE=$?

# Forward output to topic 20467
if [[ -n "$OUTPUT" ]]; then
    HEADER="pressbox-pipeline-v7.py ($(date +%H:%M))"
    printf "%s\n%s" "$HEADER" "$OUTPUT" | python3 /home/ubuntu/.hermes/scripts/telegram-topic-send.py --summary 2>/dev/null || true
fi

exit $EXIT_CODE
