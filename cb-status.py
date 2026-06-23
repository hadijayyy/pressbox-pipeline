#!/usr/bin/env python3
"""Circuit Breaker Status Reporter — runs every 2h, reports any OPEN circuits.

Silent on success (all CLOSED). Alerts only when a circuit is tripped.
"""
import json
import os
import subprocess
import sys
import time

STATE_DIR = "/tmp/hermes-cb"
TRACKED_JOBS = {
    "947200b793a7": "Pipeline",
    "783c6bf97144": "Post",
    "b341c2a287b9": "Feedback",
    "78e1c1ee4660": "Auto-Adjust",
    "3a8e8174e9b6": "Analytics LLM",
}

CB_PATH = "/home/ubuntu/.hermes/scripts/hermes_circuit_breaker.py"


def get_status(job_id: str) -> dict:
    r = subprocess.run(
        ["python3", CB_PATH, "status", job_id],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return {"job_id": job_id, "state": "UNKNOWN", "error": r.stderr.strip()[:120]}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"job_id": job_id, "state": "UNKNOWN", "error": "parse fail"}


def main() -> int:
    if not os.path.isdir(STATE_DIR):
        print("✅ All circuits CLOSED (no state files yet)")
        return 0

    rows = []
    open_circuits = []
    for job_id, name in TRACKED_JOBS.items():
        s = get_status(job_id)
        state = s.get("state", "UNKNOWN")
        row = {"name": name, "job_id": job_id[:8], "state": state}
        if state == "OPEN":
            row["cooldown_remaining_sec"] = s.get("cooldown_remaining_sec", 0)
            row["failures_in_window"] = s.get("failures_in_window", 0)
            open_circuits.append(row)
        elif state == "HALF_OPEN":
            open_circuits.append(row)
        rows.append(row)

    if not open_circuits:
        # Silent — all good
        return 0

    # Build Vercel-style report
    ts = int(time.time())
    print(f"⚠️ Circuit Breaker Alert — {len(open_circuits)} circuit(s) tripped")
    print(f"unix: {ts}")
    print()
    for row in rows:
        marker = "●" if row["state"] == "CLOSED" else "●"
        if row["state"] == "CLOSED":
            print(f"  {marker} {row['name']:20} · CLOSED")
        elif row["state"] == "OPEN":
            mins = row.get("cooldown_remaining_sec", 0) // 60
            fails = row.get("failures_in_window", 0)
            print(f"  {marker} {row['name']:20} · OPEN · cooldown {mins}m · {fails} fails")
        elif row["state"] == "HALF_OPEN":
            print(f"  {marker} {row['name']:20} · HALF_OPEN (probing)")
        else:
            print(f"  {marker} {row['name']:20} · {row['state']}")

    print()
    print("▾ Manual reset:")
    print("  python3 ~/.hermes/scripts/hermes_circuit_breaker.py reset <job_id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
