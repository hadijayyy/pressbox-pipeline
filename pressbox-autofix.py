#!/usr/bin/env python3
"""Pressbox auto-fix — runs after a pipeline failure to handle safe cases.
Safe to auto-fix:
  - network_timeout  (curl/LLM read timed out)
  - stale_staging    (old staging.json never posted)
  - empty_llm        (LLM returned 0 chars after retries)
Unsafe (skipped, left for alert):
  - SyntaxError, NameError, KeyError, AuthenticationError, etc.

Behavior:
  - Acquires a lock to prevent concurrent runs
  - Only acts on failures from the last 15 minutes
  - Applies one targeted fix, re-runs the pipeline once
  - Silent on success; alerts if the re-run also fails
"""
import os
import re
import subprocess
import sys
import time
from datetime import datetime

PIPELINE_JID = "947200b793a7"
PIPELINE_SCRIPT = "/home/ubuntu/.hermes/scripts/pressbox-pipeline-v7.py"
STAGING_PATH = "/home/ubuntu/.hermes/pressbox/staging.json"
OUTPUT_DIR = f"/home/ubuntu/.hermes/cron/output/{PIPELINE_JID}"
WORKDIR = "/home/ubuntu/pressbox-pipeline"
LOCK = "/tmp/pressbox-autofix.lock"
MAX_AGE_SEC = 900  # 15 minutes

# --- lock ---
if os.path.exists(LOCK):
    try:
        if time.time() - os.path.getmtime(LOCK) > 600:  # stale lock (>10 min)
            os.remove(LOCK)
        else:
            sys.exit(0)  # another instance running
    except Exception:
        pass

with open(LOCK, "w") as f:
    f.write(str(os.getpid()))

try:
    # --- analytics-llm timeout fix (runs independently) ---
    ANALYTICS_JID = "3a8e8174e9b6"
    ANALYTICS_SCRIPT = "/home/ubuntu/.hermes/scripts/pressbox-analytics-llm.py"
    ANALYTICS_OUTPUT_DIR = f"/home/ubuntu/.hermes/cron/output/{ANALYTICS_JID}"
    if os.path.isdir(ANALYTICS_OUTPUT_DIR):
        a_files = sorted(
            [f for f in os.listdir(ANALYTICS_OUTPUT_DIR) if f.endswith(".md")],
            reverse=True,
        )
        if a_files:
            a_latest = os.path.join(ANALYTICS_OUTPUT_DIR, a_files[0])
            try:
                a_age = time.time() - os.path.getmtime(a_latest)
            except Exception:
                a_age = 999999
            if a_age < 86400:  # within 24h
                try:
                    with open(a_latest, "r") as f:
                        a_content = f.read()
                except Exception:
                    a_content = ""
                if "script failed" in a_content and ("timeout" in a_content.lower() or "120s" in a_content.lower()):
                    print("🔧 Auto-fix: analytics-llm timeout detected — re-running with 300s timeout")
                    try:
                        a_result = subprocess.run(
                            ["python3", ANALYTICS_SCRIPT],
                            capture_output=True,
                            text=True,
                            timeout=300,
                        )
                        if a_result.returncode == 0:
                            print("✅ Auto-fix: analytics-llm re-run succeeded")
                            # Write success marker so health check sees a recent good run
                            try:
                                marker = os.path.join(
                                    ANALYTICS_OUTPUT_DIR,
                                    f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}-autofix.md",
                                )
                                with open(marker, "w") as f:
                                    f.write(f"# Auto-fix success\n\nAnalytics-llm re-ran successfully at {datetime.now().isoformat()}\n")
                            except Exception as e:
                                print(f"   (could not write marker: {e})")
                        else:
                            print(f"❌ Auto-fix: analytics-llm re-run failed (exit {a_result.returncode})")
                            a_stderr = (a_result.stderr or "").strip().splitlines()[-5:]
                            for line in a_stderr:
                                print(f"   {line}")
                    except subprocess.TimeoutExpired:
                        print("❌ Auto-fix: analytics-llm re-run timed out (300s)")

    # --- find latest pipeline output ---
    if not os.path.isdir(OUTPUT_DIR):
        sys.exit(0)
    files = sorted(
        [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".md")],
        reverse=True,
    )
    if not files:
        sys.exit(0)
    latest = os.path.join(OUTPUT_DIR, files[0])

    # --- only act on recent failures ---
    try:
        if time.time() - os.path.getmtime(latest) > MAX_AGE_SEC:
            sys.exit(0)
    except Exception:
        sys.exit(0)

    with open(latest, "r") as f:
        content = f.read()

    if "script failed" not in content and "❌" not in content:
        sys.exit(0)  # latest run was success

    # --- unsafe patterns: bail out, let alert handle ---
    unsafe_patterns = [
        r"SyntaxError",
        r"NameError",
        r"KeyError",
        r"ImportError",
        r"IndentationError",
        r"AttributeError",
        r"Authentication",
        r"invalid_grant",
        r"invalid_api_key",
        r"api[._ ]key.*invalid",
    ]
    for pat in unsafe_patterns:
        if re.search(pat, content, re.IGNORECASE):
            sys.exit(0)  # unsafe — let alert ping

    # --- safe patterns ---
    safe_fix = None
    if re.search(r"timed?\s*out|timeout", content, re.IGNORECASE):
        safe_fix = "network_timeout"
    elif re.search(r"stale|staging.*not posted|unposted", content, re.IGNORECASE):
        safe_fix = "stale_staging"
    elif re.search(r"content=0 chars|empty response|response.*empty", content, re.IGNORECASE):
        safe_fix = "empty_llm"

    if not safe_fix:
        sys.exit(0)  # unknown failure — let alert ping

    # --- apply fix ---
    print(f"🔧 Auto-fix triggered: {safe_fix}")

    if safe_fix == "stale_staging":
        try:
            if os.path.exists(STAGING_PATH):
                os.remove(STAGING_PATH)
                print(f"   - removed stale staging: {STAGING_PATH}")
        except Exception as e:
            print(f"   - could not remove staging: {e}")
    elif safe_fix == "network_timeout":
        print("   - waiting 30s for network to settle")
        time.sleep(30)
    elif safe_fix == "empty_llm":
        print("   - waiting 10s before retry")
        time.sleep(10)

    # --- re-run pipeline ---
    print(f"   - re-running {PIPELINE_SCRIPT}")
    try:
        result = subprocess.run(
            ["python3", PIPELINE_SCRIPT],
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("❌ Auto-fix: re-run timed out (120s)")
        sys.exit(1)

    if result.returncode == 0:
        # extract success line
        for line in result.stdout.splitlines()[-15:]:
            if "Pipeline done" in line or "✅" in line:
                print(f"   {line.strip()}")
        if os.path.exists(STAGING_PATH):
            size = os.path.getsize(STAGING_PATH)
            print(f"✅ Auto-fix: pipeline re-run succeeded — staging {size} bytes")
        else:
            print("✅ Auto-fix: pipeline re-run succeeded")
        # Write success marker so health check sees a recent good run
        try:
            marker = os.path.join(
                OUTPUT_DIR,
                f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}-autofix.md",
            )
            with open(marker, "w") as f:
                f.write(f"# Auto-fix success\n\nPipeline re-ran successfully at {datetime.now().isoformat()}\n")
        except Exception as e:
            print(f"   (could not write marker: {e})")
        sys.exit(0)
    else:
        print(f"❌ Auto-fix: re-run failed (exit {result.returncode})")
        # surface the last few stderr lines
        stderr_tail = (result.stderr or "").strip().splitlines()[-10:]
        for line in stderr_tail:
            print(f"   {line}")
        sys.exit(1)

finally:
    try:
        os.remove(LOCK)
    except Exception:
        pass
