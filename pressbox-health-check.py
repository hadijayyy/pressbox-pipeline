#!/usr/bin/env python3
"""Pressbox health check — runs after each pipeline/post cron tick.
Exits 0 silently when everything is fine; exits 1 with a list of failures
when any of the tracked crons errored in their latest run.
"""
import os
import glob
import sys

# Crons we care about
CRONS = {
    "Pipeline":          "947200b793a7",
    "Post":              "783c6bf97144",
    "Feedback":          "b341c2a287b9",
    "Auto-Adjust":       "78e1c1ee4660",
    "Analytics LLM":     "3a8e8174e9b6",
}

OUTPUT_BASE = "/home/ubuntu/.hermes/cron/output"
errors = []

for name, jid in CRONS.items():
    out_dir = f"{OUTPUT_BASE}/{jid}"
    if not os.path.isdir(out_dir):
        continue
    files = sorted(glob.glob(f"{out_dir}/*.md"), key=os.path.getmtime, reverse=True)
    if not files:
        continue
    latest = files[0]
    try:
        with open(latest, "r") as f:
            content = f.read()
    except Exception as e:
        errors.append(f"{name} ({jid}) - cannot read {os.path.basename(latest)}: {e}")
        continue
    # Failure markers
    if "script failed" in content or "❌" in content or "Status: error" in content:
        errors.append(f"{name} ({jid}) - {os.path.basename(latest)}")

if errors:
    print("⚠️ Pressbox health check FAILED:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    # Silent on success — cron will not deliver anything
    sys.exit(0)
