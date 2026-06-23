#!/usr/bin/env bash
# pressbox-cb-run.sh — circuit-breaker-gated cron runner
# Usage: pressbox-cb-run.sh <job_id> <script_path> [args...]
#
# Behavior:
#   1. Check circuit breaker. If OPEN → exit 77 silently (cron won't deliver).
#   2. If CLOSED/HALF_OPEN → run the script.
#   3. Capture exit code, record success/failure to breaker.
#   4. Emit telemetry (latency, exit code, cb_state) to JSONL log.
#   5. Pass-through stdout/stderr to caller (so cron delivery still works).
#
# Exit codes:
#   77 → circuit OPEN (skip silently)
#   0  → script succeeded
#   !=0 && !=77 → script failed (recorded to breaker)
set -u

JOB_ID="${1:-}"
SCRIPT="${2:-}"
shift 2 || true
ARGS=("$@")

if [[ -z "$JOB_ID" || -z "$SCRIPT" ]]; then
  echo "Usage: pressbox-cb-run.sh <job_id> <script_path> [args...]" >&2
  exit 2
fi

CB="/home/ubuntu/.hermes/scripts/hermes_circuit_breaker.py"
WORKDIR="/home/ubuntu/pressbox-pipeline"
TELEMETRY_DIR="/home/ubuntu/.hermes/cb-logs/telemetry"
TELEMETRY_FILE="${TELEMETRY_DIR}/${JOB_ID}.jsonl"

# --- 0. Pre-flight telemetry (state BEFORE run) ---
mkdir -p "$TELEMETRY_DIR"
PRE_CB_STATE=$(python3 "$CB" status "$JOB_ID" 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('state', 'UNKNOWN'))
except Exception:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

# --- 1. Gate check ---
ALLOW_OUT=$(python3 "$CB" allow "$JOB_ID" 2>&1)
ALLOW_EXIT=$?

if [[ $ALLOW_EXIT -eq 77 ]]; then
  # Circuit OPEN — silent skip (cron will not deliver)
  echo "⛔ Circuit breaker OPEN for $JOB_ID — skipping"
  echo "   $ALLOW_OUT"

  # Telemetry: record skip
  SKIP_TS=$(date +%s)
  cat >> "$TELEMETRY_FILE" <<EOF
{"ts":${SKIP_TS},"job_id":"${JOB_ID}","script":"${SCRIPT}","event":"skip","cb_state":"${PRE_CB_STATE}","exit_code":77,"duration_s":0}
EOF
  exit 77
fi

# Log state for visibility (won't trigger cron delivery since stdout is forwarded only on success)
echo "▶ Circuit $ALLOW_OUT — running $SCRIPT"

# --- 2. Run script ---
START_TS=$(date +%s)
cd "$WORKDIR" || { echo "❌ cannot cd to $WORKDIR"; exit 1; }

# Capture stdout + exit code
set +e
SCRIPT_STDOUT=$(python3 -u "$SCRIPT" "${ARGS[@]}" 2>&1)
RUN_EXIT=$?
set -e

# Forward summary to topic 20467 (if non-empty)
FORWARDER="/home/ubuntu/.hermes/scripts/telegram-topic-send.py"
if [[ -n "$SCRIPT_STDOUT" && -f "$FORWARDER" ]]; then
    HEADER="${SCRIPT} ($(date +%H:%M))"
    printf "%s\n%s" "$HEADER" "$SCRIPT_STDOUT" | python3 "$FORWARDER" --summary 2>/dev/null || true
fi

END_TS=$(date +%s)
DURATION=$((END_TS - START_TS))

# --- 3. Record result ---
if [[ $RUN_EXIT -eq 0 ]]; then
  python3 "$CB" record success "$JOB_ID" >/dev/null 2>&1
  echo "✅ $JOB_ID succeeded in ${DURATION}s"
  POST_CB_STATE="CLOSED"
else
  # Truncate error to ~120 chars to keep breaker state small
  ERR_MSG="exit $RUN_EXIT after ${DURATION}s"
  python3 "$CB" record failure "$JOB_ID" "$ERR_MSG" >/dev/null 2>&1
  echo "❌ $JOB_ID failed: $ERR_MSG"
  POST_CB_STATE=$(python3 "$CB" status "$JOB_ID" 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('state', 'UNKNOWN'))
except Exception:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")
fi

# --- 4. Emit telemetry ---
# Get stderr line count (rough signal of error verbosity)
STDERR_LINES=$(wc -l < /tmp/last-cb-stderr.log 2>/dev/null | tr -d ' ' || echo "0")

cat >> "$TELEMETRY_FILE" <<EOF
{"ts":${END_TS},"job_id":"${JOB_ID}","script":"${SCRIPT}","event":"run","cb_state_pre":"${PRE_CB_STATE}","cb_state_post":"${POST_CB_STATE}","exit_code":${RUN_EXIT},"duration_s":${DURATION},"stderr_lines":${STDERR_LINES}}
EOF

exit $RUN_EXIT
