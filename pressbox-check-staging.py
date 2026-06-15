#!/usr/bin/env python3
"""
PRESS BOX — Staging Check (Aggressive).
Cek staging, kalau kosong → run pipeline with retries.
Goal: ensure :35 always has content to post.
"""
import json, os, sys, subprocess, time
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
STAGING_FILE = f"{HOME}/.hermes/pressbox/staging.json"
STAGING_V3 = f"{HOME}/.hermes/pressbox/staging-v3.json"
PIPELINE_SCRIPT = f"{HOME}/.hermes/scripts/pressbox-pipeline-v2.py"
WIB = timezone(timedelta(hours=7))
MAX_RETRIES = 1  # Aggressive: 3 attempts to ensure content ready

def log(msg):
    ts = datetime.now(WIB).strftime("%H:%M WIB")
    print(f"[{ts}] [CHECK] {msg}", flush=True, file=sys.stderr)

def is_staging_ready():
    """Check if either staging file has content."""
    for sf in [STAGING_FILE, STAGING_V3]:
        if os.path.exists(sf):
            try:
                with open(sf) as f:
                    data = json.load(f)
                if data.get("topic") and data.get("content"):
                    return True
            except: pass
    return False

# Check if staging already has content
if is_staging_ready():
    log("Staging ada konten — skip. [SILENT]")
    sys.exit(0)

# Staging kosong — run pipeline with retries
log(f"Staging kosong — running pipeline (max {MAX_RETRIES} attempts)...")

for attempt in range(MAX_RETRIES):
    log(f"Attempt {attempt+1}/{MAX_RETRIES}...")
    try:
        result = subprocess.run(
            [sys.executable, PIPELINE_SCRIPT],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode == 0:
            log(f"✅ Pipeline sukses on attempt {attempt+1}")
            if result.stdout.strip():
                print(result.stdout.strip())
            sys.exit(0)
        else:
            log(f"❌ Attempt {attempt+1} failed (exit {result.resultcode})")
            if attempt < MAX_RETRIES - 1:
                log(f"  Retrying in 5s...")
                time.sleep(5)
    except subprocess.TimeoutExpired:
        log(f"❌ Attempt {attempt+1} timeout (180s)")
        if attempt < MAX_RETRIES - 1:
            log(f"  Retrying in 5s...")
            time.sleep(5)
    except Exception as e:
        log(f"❌ Attempt {attempt+1} error: {e}")
        if attempt < MAX_RETRIES - 1:
            log(f"  Retrying in 5s...")
            time.sleep(5)

# All attempts failed
log(f"❌ All {MAX_RETRIES} attempts failed — :35 will have no content")
sys.exit(1)
