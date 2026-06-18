#!/usr/bin/env python3
"""
PRESS BOX POST — Phase 2.
Read staging → post to Threads → verify → update tracking.
"""
import json, os, subprocess, sys, time, requests
import shlex
from datetime import datetime
from pressbox_common import log, send_alert, load_env, WIB, STAGING, POSTED, HOME

POST_SCRIPT = f"{HOME}/.hermes/scripts/pressbox-direct-post.py"
VERIFY_SCRIPT = f"{HOME}/.hermes/scripts/verify-last-slide.py"
LATEST_MD = f"{HOME}/.hermes/content-pipeline/drafts/football/latest.md"
os.makedirs(f"{HOME}/.hermes/pressbox", exist_ok=True)

def _cleanup(remove_pending=True, current_topic=None):
    """Clear staging + optionally remove [PENDING] tracking safely"""
    if remove_pending and current_topic:
        try:
            with open(POSTED) as f:
                data = json.load(f)
            data["topics"] = [
                t for t in data.get("topics", []) 
                if not (t.get("post_id") == "[PENDING]" and t.get("title") == current_topic.get("title"))
            ]
            with open(POSTED, 'w') as f:
                json.dump(data, f, indent=2)
        except FileNotFoundError:
            pass # Safe to ignore if tracking file doesn't exist yet
            
    # Clear both staging files
    for sf in [STAGING["v2"], STAGING["v3"]]:
        try:
            with open(sf, 'w') as f:
                json.dump({"topic": None, "written_at": None}, f)
        except Exception:
            pass

def shell(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = r.stdout.strip()
        if r.stderr.strip():
            out += f"\nERR: {r.stderr.strip()[:200]}"
        return out, r.returncode
    except subprocess.TimeoutExpired:
        return "(timeout)", -1
    except Exception as e:
        return str(e), -1

def is_posting_too_frequent():
    """Check if we posted too recently (Quality > Quantity)."""
    try:
        with open(POSTED) as f:
            data = json.load(f)
        topics = data.get("topics", [])
        if not topics:
            return False
        # Check last 3 posts timing
        recent = sorted(topics, key=lambda x: x.get("posted_at", ""), reverse=True)[:3]
        now = datetime.now(WIB)
        for t in recent:
            posted = t.get("posted_at", "")
            if posted:
                try:
                    dt = datetime.fromisoformat(posted)
                    diff = (now - dt).total_seconds() / 60  # minutes
                    if diff < 30:  # Less than 30 min since last post
                        return True
                except (ValueError, TypeError) as e:
                    log(f"   ⚠️ Date parsing failed: {e}")
        return False
    except (OSError, IOError, json.JSONDecodeError) as e:
        log(f"   ⚠️ Post frequency check failed: {e}")
        return False

# ===== MAIN =====
log('POST', "=== PRESS BOX POST ===")

# 0b. FREQUENCY CHECK — Quality > Quantity
if is_posting_too_frequent():
    print("⏸️ Skip — baru posting < 30 menit lalu.")
    sys.exit(0)

# 1. Read staging (check v3 first, then v2)
staging_file = STAGING["v2"]
if os.path.exists(STAGING["v3"]):
    with open(STAGING["v3"]) as f:
        staging = json.load(f)
    if staging.get("topic") and staging.get("content"):
        staging_file = STAGING["v3"]
    else:
        staging_file = STAGING["v2"]
elif os.path.exists(STAGING["v2"]):
    staging_file = STAGING["v2"]
else:
    print("⏸️ Skip — staging kosong.")
    sys.exit(0)

with open(staging_file) as f:
    staging = json.load(f)

topic = staging.get("topic")
content = staging.get("content")
written_at = staging.get("written_at")

if not topic or not content:
    log('POST', "Staging empty — nothing to post.")
    print("⏸️ Skip — staging kosong.")
    sys.exit(0)

# 1b. DUPLICATE CHECK — skip if URL already posted
topic_url = topic.get("url", "")
if topic_url:
    try:
        with open(POSTED) as f:
            posted_data = json.load(f)
        for t in posted_data.get("topics", []):
            if t.get("url") == topic_url:
                log('POST', f"🔁 Duplicate detected — already posted: {topic['title'][:50]}")
                print(f"⏭️ Skip — sudah pernah dipost.")
                _cleanup(remove_pending=True, current_topic=topic)
                sys.exit(0)
    except FileNotFoundError:
        pass

log('POST', f"Staging loaded: {topic['title']} (written at {written_at})")

# 2. Write content to latest.md
os.makedirs(os.path.dirname(LATEST_MD), exist_ok=True)
with open(LATEST_MD, 'w') as f:
    f.write(content)

# 3. Post to Threads (single attempt — no retry to fit 120s cron)
log('POST', "Posting to Threads...")
image_url = staging.get("image_url") or ""
image_flag = f" --image {shlex.quote(image_url)}" if image_url else ""
post_cmd = f"python3 {POST_SCRIPT} --file {LATEST_MD}{image_flag} 2>&1"
post_out, code = shell(post_cmd, timeout=100)

# 4. Extract root ID and permalink
root_id = None
permalink = None
post_ids = []
post_out2 = ""  # Initialize to prevent NameError
for line in post_out.split('\n'):
    if line.startswith('Root:'):
        root_id = line.split('Root:', 1)[1].strip()
    elif line.startswith('Post:'):
        permalink = line.split('Post:', 1)[1].strip()
    line_stripped = line.strip()
    if line_stripped and line_stripped.isdigit() and len(line_stripped) > 15:
        post_ids.append(line_stripped)

# Skip retry — single attempt to fit 120s cron limit
if not root_id:
    log('POST', f"❌ Failed — no root post ID (output: {post_out[:300]})")
    title = topic.get("title", "?")
    print("❌ Post error — gagal posting.")
    send_alert(f"POST failed (no root ID)\nTopic: {title[:60]}")
    _cleanup(remove_pending=True, current_topic=topic)
    sys.exit(1)

# 5. SAFETY: if partial post (< 4 slides), auto-delete (unless single paragraph mode)
mode = staging.get("mode", "thread")
if mode != "single_paragraph" and len(post_ids) < 4:
    log('POST', f"⚠️ Partial post ({len(post_ids)} slides), deleting...")
    shell(f"python3 {POST_SCRIPT} --delete {root_id}", timeout=15)
    print(f"❌ Partial post ({len(post_ids)} slides) — dihapus.")
    _cleanup(remove_pending=True, current_topic=topic)
    sys.exit(0)

# 6. Verify
verify_out, _ = shell(f"python3 {VERIFY_SCRIPT} {root_id}", timeout=15)

# 7. Update tracking
with open(POSTED) as f:
    data = json.load(f)
# Try to update [PENDING] entry first
found = False
for t in data.get("topics", []):
    if t.get("post_id") == "[PENDING]" and t.get("title") == topic.get("title"):
        t["post_id"] = root_id
        found = True
        break
# If no [PENDING] entry, create new tracking entry
if not found:
    if "topics" not in data:
        data["topics"] = []
    data["topics"].append({
        "title": topic.get("title", ""),
        "post_id": root_id,
        "timestamp": datetime.now(WIB).isoformat(),
        "source": topic.get("source", ""),
        "description": (topic.get("description") or "")[:300],
        "url": "",
        "posted_at": datetime.now(WIB).isoformat()
    })
    log('POST', f"📝 New tracking entry: {topic.get('title','?')[:50]}")
with open(POSTED, 'w') as f:
    json.dump(data, f, indent=2)

# 8. Cleanup
_cleanup(remove_pending=False, current_topic=topic)
open(LATEST_MD, 'w').close()

# 9. Done — simple report
threads_link = permalink if permalink else f"https://www.threads.com/@parkthebus.football/post/{root_id}"
title = topic.get("title", "?")
source = topic.get("source", "?")
img = "🖼️" if image_url else ""
print(f"✅ {title[:70]}")
print(f"   {threads_link}")