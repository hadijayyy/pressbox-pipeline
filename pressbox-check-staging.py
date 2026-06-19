#!/usr/bin/env python3
"""
PRESS BOX — Staging Check (Aggressive).
Cek staging, kalau kosong → run pipeline with retries.
Goal: ensure :35 always has content to post.
"""
import json, os, sys, subprocess, time
from pressbox_common import log, WIB, STAGING, HOME

PIPELINE_SCRIPT = f"{os.path.dirname(os.path.abspath(__file__))}/pressbox-pipeline-v7.py"
MAX_RETRIES = 1  # Aggressive attempts to ensure content ready

def is_staging_ready():
    """Check if either staging file has content."""
    for sf in [STAGING["v2"], STAGING["v3"]]:
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
    log('CHECK', "Staging ada konten — skip. [SILENT]")
    print("✅ Staging ready — pipeline skip")
    sys.exit(0)

# Staging kosong — run pipeline with retries
log('CHECK', f"Staging kosong — running pipeline (max {MAX_RETRIES} attempts)...")

for attempt in range(MAX_RETRIES):
    log('CHECK', f"Attempt {attempt+1}/{MAX_RETRIES}...")
    try:
        result = subprocess.run(
            [sys.executable, PIPELINE_SCRIPT],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode == 0:
            log('CHECK', f"✅ Pipeline sukses on attempt {attempt+1}")
            if result.stdout.strip():
                print(result.stdout.strip())
            sys.exit(0)
        else:
            log('CHECK', f"❌ Attempt {attempt+1} failed (exit {result.returncode})")
            if attempt < MAX_RETRIES - 1:
                log('CHECK', f"  Retrying in 5s...")
                time.sleep(5)
    except subprocess.TimeoutExpired:
        log('CHECK', f"❌ Attempt {attempt+1} timeout (180s)")
        if attempt < MAX_RETRIES - 1:
            log('CHECK', f"  Retrying in 5s...")
            time.sleep(5)
    except Exception as e:
        log('CHECK', f"❌ Attempt {attempt+1} error: {e}")
        if attempt < MAX_RETRIES - 1:
            log('CHECK', f"  Retrying in 5s...")
            time.sleep(5)

# All attempts failed
log('CHECK', f"❌ All {MAX_RETRIES} attempts failed — :35 will have no content")
print(f"❌ Check staging: all {MAX_RETRIES} pipeline attempts failed")
sys.exit(1)
