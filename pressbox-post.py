#!/usr/bin/env python3
"""
PRESS BOX POST — Phase 2.
Read staging → post to Threads → verify → update tracking.
"""
import json, os, subprocess, sys, time, requests
import shlex
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~")
STAGING_FILE = f"{HOME}/.hermes/pressbox/staging.json"
STAGING_V3 = f"{HOME}/.hermes/pressbox/staging-v3.json"
POST_SCRIPT = f"{HOME}/.hermes/scripts/pressbox-direct-post.py"
VERIFY_SCRIPT = f"{HOME}/.hermes/scripts/verify-last-slide.py"
POSTED_JSON = f"{HOME}/.hermes/pressbox/posted_topics.json"
LATEST_MD = f"{HOME}/.hermes/content-pipeline/drafts/football/latest.md"
os.makedirs(f"{HOME}/.hermes/pressbox", exist_ok=True)
WIB = timezone(timedelta(hours=7))

ALERT_CHAT = "1022032312"
FEEDBACK_JSON = f"{HOME}/.hermes/pressbox/analytics_feedback.json"
BOT_TOKEN = None
try:
    for line in open(f"{HOME}/.hermes/.env"):
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            BOT_TOKEN = line.strip().split("=", 1)[1].strip('"').strip("'")
            break
except: pass

def send_alert(msg):
    if not BOT_TOKEN:
        return
    try:
        text = f"⚠️ PRESS BOX ERROR — {msg}"
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ALERT_CHAT, "text": text},
            timeout=10)
    except: pass

def _cleanup(remove_pending=True, current_topic=None):
    """Clear staging + optionally remove [PENDING] tracking safely"""
    if remove_pending and current_topic:
        try:
            with open(POSTED_JSON) as f:
                data = json.load(f)
            data["topics"] = [
                t for t in data.get("topics", []) 
                if not (t.get("post_id") == "[PENDING]" and t.get("title") == current_topic.get("title"))
            ]
            with open(POSTED_JSON, 'w') as f:
                json.dump(data, f, indent=2)
        except FileNotFoundError:
            pass # Safe to ignore if tracking file doesn't exist yet
            
    # Clear both staging files
    for sf in [STAGING_FILE, STAGING_V3]:
        try:
            with open(sf, 'w') as f:
                json.dump({"topic": None, "written_at": None}, f)
        except Exception:
            pass

def log(msg):
    ts = datetime.now(WIB).strftime("%H:%M WIB")
    print(f"[{ts}] [POST] {msg}", flush=True, file=sys.stderr)

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

def is_bad_hour():
    """Check if current hour is in worst_hours from analytics feedback."""
    try:
        with open(FEEDBACK_JSON) as f:
            fb = json.load(f)
        worst = fb.get("worst_hours", [])
        if not worst:
            return False
        now_hour = datetime.now(WIB).hour
        return now_hour in worst
    except:
        return False

def is_posting_too_frequent():
    """Check if we posted too recently (Quality > Quantity)."""
    try:
        with open(POSTED_JSON) as f:
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
                except:
                    pass
        return False
    except:
        return False

# ===== MAIN =====
log("=== PRESS BOX POST (Phase 2) ===")

# 0. TIME CHECK — skip if current hour is analytics worst hour
if is_bad_hour():
    log("⏰ Bad hour detected — skipping post. [SILENT]")
    print("⏸️ Post skip — jam ini termasuk worst hour. Next jam.")
    sys.exit(0)

# 0b. FREQUENCY CHECK — Quality > Quantity
if is_posting_too_frequent():
    log("⏰ Posting too frequent — skipping for quality. [SILENT]")
    print("⏸️ Post skip — baru posting < 1 jam lalu. Quality > Quantity.")
    sys.exit(0)

# 1. Read staging (check v3 first, then v2)
staging_file = STAGING_FILE
if os.path.exists(STAGING_V3):
    with open(STAGING_V3) as f:
        staging = json.load(f)
    if staging.get("topic") and staging.get("content"):
        staging_file = STAGING_V3
    else:
        staging_file = STAGING_FILE
elif os.path.exists(STAGING_FILE):
    staging_file = STAGING_FILE
else:
    log("No staging file — nothing to post. [SILENT]")
    print(f"⏸️ Post skip — belum ada konten di staging.")
    sys.exit(0)

with open(staging_file) as f:
    staging = json.load(f)

topic = staging.get("topic")
content = staging.get("content")
written_at = staging.get("written_at")

if not topic or not content:
    log("Staging empty — nothing to post. [SILENT]")
    print(f"⏸️ Post skip — staging kosong.")
    sys.exit(0)

log(f"Staging loaded: {topic['title']} (written at {written_at})")

# 2. Write content to latest.md
os.makedirs(os.path.dirname(LATEST_MD), exist_ok=True)
with open(LATEST_MD, 'w') as f:
    f.write(content)

# 3. Post to Threads (single attempt — no retry to fit 120s cron)
log("Posting to Threads...")
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
    log(f"❌ Failed — no root post ID (output: {post_out[:300]})")
    title = topic.get("title", "?")
    print(f"❌ Post error: {title[:60]}")
    send_alert(f"POST failed (no root ID)\nTopic: {title[:60]}")
    _cleanup(remove_pending=True, current_topic=topic)
    sys.exit(1)

# 5. SAFETY: if partial post (< 4 slides), auto-delete (unless single paragraph mode)
mode = staging.get("mode", "thread")
if mode != "single_paragraph" and len(post_ids) < 4:
    log(f"⚠️ Partial post ({len(post_ids)} slides), deleting...")
    shell(f"python3 {POST_SCRIPT} --delete {root_id}", timeout=15)
    print(f"❌ Post delete — partial post ({len(post_ids)} slides), dihapus.")
    _cleanup(remove_pending=True, current_topic=topic)
    sys.exit(0)

# 6. Verify
verify_out, _ = shell(f"python3 {VERIFY_SCRIPT} {root_id}", timeout=15)

# 7. Update tracking
with open(POSTED_JSON) as f:
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
    log(f"📝 New tracking entry: {topic.get('title','?')[:50]}")
with open(POSTED_JSON, 'w') as f:
    json.dump(data, f, indent=2)

# 8. Cleanup
_cleanup(remove_pending=False, current_topic=topic)
open(LATEST_MD, 'w').close()

# 9. Done — report
src_url = topic.get("url", "")
threads_link = permalink if permalink else f"https://www.threads.com/@parkthebus.football/post/{root_id}"
print(f"✅ Posted: {topic['title'][:80]} ({len(post_ids)} slides)")
print(f"   {threads_link}")