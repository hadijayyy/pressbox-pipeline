#!/usr/local/bin/python3
"""Circuit Breaker Daily Digest — runs 08:00 WIB daily.

Summarizes last 24h of circuit breaker activity across all tracked jobs.
Silent on no-incident days. Alerts only when there's something worth seeing:
  - any circuit tripped in last 24h
  - any HALF_OPEN probe (recovery)
  - cumulative failure count trending up
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

STATE_DIR = "/tmp/hermes-cb"
WIB = timezone(timedelta(hours=7))

TRACKED = {
    "947200b793a7": "Pipeline",
    "783c6bf97144": "Post",
    "b341c2a287b9": "Feedback",
    "78e1c1ee4660": "Auto-Adjust",
    "3a8e8174e9b6": "Analytics LLM (Daily)",
    "ec5cab5397b9": "Analytics LLM (Weekly)",
    "5405e500b720": "Weekly Backup",
    "e7213683c6b8": "Job Search (2-segment)",
}


def load_state(job_id: str) -> dict | None:
    path = f"{STATE_DIR}/{job_id}.json"
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def main() -> int:
    now = datetime.now(WIB)
    yesterday = now - timedelta(hours=24)
    yesterday_ts = yesterday.timestamp()

    rows = []
    incidents = []   # any tripping in last 24h
    probes = []      # HALF_OPEN recoveries

    for job_id, name in TRACKED.items():
        st = load_state(job_id)
        if not st:
            rows.append({"name": name, "job_id": job_id[:8], "state": "—",
                         "last_24h_fails": 0, "last_24h_trips": 0, "last_24h_probes": 0})
            continue

        cur_state = st.get("state", "UNKNOWN")
        failures = st.get("failures", [])
        history = st.get("history", [])

        # Filter to last 24h
        fails_24h = [t for t in failures if t >= yesterday_ts]
        trips_24h = [h for h in history
                     if h.get("ts", 0) >= yesterday_ts and h.get("to") == "OPEN"]
        probes_24h = [h for h in history
                      if h.get("ts", 0) >= yesterday_ts and h.get("to") == "HALF_OPEN"]

        # Find recoveries (OPEN → CLOSED in last 24h)
        recoveries_24h = [h for h in history
                          if h.get("ts", 0) >= yesterday_ts
                          and h.get("from") in ("OPEN", "HALF_OPEN")
                          and h.get("to") == "CLOSED"]

        if trips_24h:
            incidents.append({"name": name, "job_id": job_id[:8],
                              "trip_count": len(trips_24h),
                              "last_trip_ts": max(h["ts"] for h in trips_24h)})
        if probes_24h:
            probes.append({"name": name, "job_id": job_id[:8],
                           "probe_count": len(probes_24h)})

        rows.append({
            "name": name, "job_id": job_id[:8], "state": cur_state,
            "last_24h_fails": len(fails_24h),
            "last_24h_trips": len(trips_24h),
            "last_24h_probes": len(probes_24h),
            "last_24h_recoveries": len(recoveries_24h),
        })

    if not incidents and not probes:
        # Silent — boring day = good day
        return 0

    ts = int(time.time())
    print(f"▶ Circuit Breaker Digest · {now.strftime('%Y-%m-%d %H:%M WIB')}")
    print(f"unix: {ts}")
    print(f"window: last 24h · since {yesterday.strftime('%Y-%m-%d %H:%M WIB')}")
    print()

    if incidents:
        print(f"▾ Trips in last 24h ({len(incidents)}):")
        for inc in sorted(incidents, key=lambda x: -x["last_trip_ts"]):
            ago_min = int((ts - inc["last_trip_ts"]) // 60)
            print(f"  ● {inc['name']:<28} · {inc['job_id']} · "
                  f"{inc['trip_count']}× trip · last {ago_min}m ago")
        print()

    if probes:
        print(f"▾ HALF_OPEN probes (recovery attempts):")
        for p in probes:
            print(f"  ▶ {p['name']:<28} · {p['job_id']} · {p['probe_count']}× probe")
        print()

    print("▾ State snapshot:")
    print(f"  {'Job':<28} {'State':<11} {'24h Fails':<10} {'Trips':<6} {'Probes':<7}")
    print("  " + "-" * 70)
    for r in rows:
        marker = "●" if r["state"] in ("OPEN", "HALF_OPEN") else " "
        print(f"  {marker} {r['name']:<26} {r['state']:<11} "
              f"{r['last_24h_fails']:<10} {r['last_24h_trips']:<6} {r['last_24h_probes']:<7}")

    # Action recommendations
    if any(r["last_24h_trips"] >= 3 for r in rows):
        print()
        print("⚠ Pattern detected: 3+ trips in 24h → investigate upstream dep")
    if any(r["last_24h_probes"] >= 3 for r in rows):
        print()
        print("⚠ Pattern detected: 3+ probes in 24h → flapping, consider longer cooldown")

    return 0


if __name__ == "__main__":
    sys.exit(main())
