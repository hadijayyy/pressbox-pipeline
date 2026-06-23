#!/usr/bin/env python3
"""Pressbox pre-flight syntax check.
Runs `python3 -m py_compile` on every pressbox script before the pipeline cron
fires. Exits 0 silently on success, exits 1 with details if any script is broken.
"""
import os
import subprocess
import sys

SCRIPTS = [
    "pressbox-pipeline-v7.py",
    "pressbox-post.py",
    "pressbox-analytics-llm.py",
    "pressbox-analytics-feedback.py",
    "pressbox-auto-adjust.py",
    "pressbox-check-staging.py",
    "pressbox_common.py",
    "pressbox-research.py",
    "pressbox-analytics-weekly.py",
    "pressbox-direct-post.py",
    "pressbox-health-check.py",
]

BASE = "/home/ubuntu/.hermes/scripts"
errors = []

for script in SCRIPTS:
    path = os.path.join(BASE, script)
    if not os.path.exists(path):
        # New/renamed scripts are fine — skip silently
        continue
    try:
        result = subprocess.run(
            ["python3", "-m", "py_compile", path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            errors.append(f"{script}: {stderr}")
    except subprocess.TimeoutExpired:
        errors.append(f"{script}: compile timeout")
    except Exception as e:
        errors.append(f"{script}: {e}")

if errors:
    print("⚠️ Pressbox preflight FAILED — script(s) have syntax errors:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    sys.exit(0)
