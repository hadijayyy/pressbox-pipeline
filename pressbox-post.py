#!/usr/bin/env python3
"""
PRESS BOX POST — Phase 2.
Read staging → post to Threads → verify → update tracking.
"""
import json, os, subprocess, sys, time, requests, re
import shlex
from datetime import datetime
from pressbox_common import log, send_alert, load_env, WIB, STAGING, POSTED, HOME

POST_SCRIPT = f"{os.path.dirname(os.path.abspath(__file__))}/post_pressbox_thread.py"
VERIFY_SCRIPT = f"{os.path.dirname(os.path.abspath(__file__))}/verify-last-slide.py"
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
        except (FileNotFoundError, json.JSONDecodeError) as e:
            log(f"   ⚠️ Cleanup failed (remove_pending): {e}")
            
    # Clear staging files (delete entirely — pipeline guard uses os.path.exists)
    for sf in [STAGING["v2"], STAGING["v3"]]:
        try:
            if os.path.exists(sf):
                os.remove(sf)
        except Exception as e:
            log(f"   ⚠️ Cleanup failed (staging): {e}")

def shell(cmd, timeout=120):
    """Run shell command with stderr capture and partial output on timeout."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = r.stdout.strip()
        if r.stderr.strip():
            out += f"\nERR: {r.stderr.strip()[:200]}"
        return out, r.returncode
    except subprocess.TimeoutExpired as e:
        # Return partial stdout even on timeout for ID extraction
        partial = ""
        if e.stdout:
            partial = e.stdout.decode("utf-8", errors="replace").strip() if isinstance(e.stdout, bytes) else e.stdout.strip()
        if e.stderr:
            err_text = e.stderr.decode("utf-8", errors="replace").strip() if isinstance(e.stderr, bytes) else e.stderr.strip()
            if err_text:
                partial += f"\nERR: {err_text[:200]}"
        return partial if partial else "(timeout)", -1
    except Exception as e:
        return str(e), -1

def extract_post_ids(output):
    """Extract root ID, permalink, and all chain post IDs from output.

    Chain driver output format:
      [1] {post_id}: {text}...
      Root permalink: https://www.threads.com/.../post/{root_id}
    """
    import re as _re
    post_ids = []
    seen = set()
    root_id = None
    permalink = None
    for line in output.split('\n'):
        line_stripped = line.strip()
        # Root permalink line — preferred (most reliable)
        if 'Root permalink:' in line_stripped and not permalink:
            m = _re.search(r'/post/([A-Za-z0-9_-]+)', line_stripped)
            if m:
                permalink = line_stripped.split('Root permalink:', 1)[1].strip()
                # Extract numeric ID — chain driver prints shortcode in URL, but
                # numeric IDs are in the [N] lines above. If we already saw it, use it.
                sc = m.group(1)
                if sc.isdigit() and len(sc) > 15 and not root_id:
                    root_id = sc
        # [N] {post_id}: ... chain indexing lines (most reliable for numeric IDs)
        elif line_stripped.startswith('[') and ']' in line_stripped:
            m = _re.match(r'\[(\d+)\]\s+(\d{15,20})', line_stripped)
            if m:
                idx = int(m.group(1))
                pid = m.group(2)
                if pid not in seen:
                    seen.add(pid)
                    post_ids.append(pid)
                    # Always set root_id from [1] (or update if first occurrence)
                    if idx == 1 or (root_id is None and idx == 1):
                        root_id = pid
        # Legacy "Root:" line (defensive — in case chain driver output changes)
        elif line_stripped.startswith('Root:') and not root_id:
            rid = line_stripped.split('Root:', 1)[1].strip()
            if rid.isdigit() and len(rid) > 15:
                root_id = rid
        # Legacy "Post:" line (defensive)
        elif line_stripped.startswith('Post:') and not permalink:
            permalink = line_stripped.split('Post:', 1)[1].strip()
        # Bare numeric IDs (legacy)
        elif line_stripped.isdigit() and len(line_stripped) > 15:
            if line_stripped not in seen:
                seen.add(line_stripped)
                post_ids.append(line_stripped)
        # Arrow notation (legacy)
        elif '→' in line_stripped:
            pid_part = line_stripped.split('→', 1)[1].strip()
            if pid_part.isdigit() and len(pid_part) > 15:
                if pid_part not in seen:
                    seen.add(pid_part)
                    post_ids.append(pid_part)
    return root_id, permalink, post_ids

def verify_chain_structure(root_id, expected_slides, access_token, max_attempts=2):
    """Query Threads API to verify slides posted as CHAIN (reply_to_id), not fan-out (siblings).

    For a chain: root has exactly 1 top-level reply (slide 2). All other slides
    are nested under that reply, not visible at the root level.

    Catches two failure modes:
    - Fan-out regression (siblings instead of chain): root has N-1 top-level replies
    - Chain broken (slide 2 failed to reply to root): root has 0 replies

    Returns:
        (ok, actual_replies, expected_replies)
        ok: True if chain structure correct, False if broken, None if API check failed
        actual_replies: count of top-level replies from API (should be exactly 1)
        expected_replies: 1 (chain head)
    """
    if expected_slides <= 1:
        # Single post, no chain expected
        return True, 0, 0

    expected_replies = 1  # chain: root should have exactly 1 reply (slide 2)

    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.get(
                f"https://graph.threads.net/v1.0/{root_id}",
                params={
                    "access_token": access_token,
                    "fields": "replies{id,text,timestamp}"
                },
                timeout=15
            )
            if r.status_code != 200:
                # Transient API error — retry only if we have attempts left
                if attempt < max_attempts:
                    log('POST', f"   ⚠️ Verify API HTTP {r.status_code} — retry {attempt}/{max_attempts}")
                    time.sleep(3 + attempt)
                    continue
                log('POST', f"⚠️ Verify API HTTP {r.status_code} after {max_attempts} attempts — skipping")
                return None, 0, expected_replies

            data = r.json()
            replies = data.get("replies", {}).get("data", [])
            actual = len(replies)

            if actual != expected_replies:
                # STRUCTURAL failure (chain broken / fan-out regression).
                # User comments only ACCUMULATE over time — retrying won't help and
                # wastes 5-15s while audience sees a broken post. Fail fast.
                if actual == 0:
                    log('POST', f"🚨 CHAIN BROKEN: 0 top-level replies on root (slide 2 didn't reply). FAIL FAST.")
                    return False, 0, expected_replies
                else:
                    log('POST', f"🚨 CHAIN BROKEN — FAN-OUT REGRESSION: {actual} top-level replies (expected {expected_replies}). FAIL FAST.")
                    return False, actual, expected_replies

            return True, actual, expected_replies
        except Exception as e:
            if attempt < max_attempts:
                log('POST', f"   ⚠️ Verify exception: {e} — retry {attempt}/{max_attempts}")
                time.sleep(3 + attempt)
                continue
            log('POST', f"⚠️ API verification failed after {max_attempts} attempts: {e}")
            return None, 0, expected_replies

    return None, 0, expected_replies


def _load_threads_token():
    """Load Threads API access token. Returns (token, user_id) or (None, None)."""
    try:
        token_path = f"{HOME}/.hermes/threads_token.json"
        with open(token_path) as f:
            data = json.load(f)
        return data.get("access_token"), str(data.get("user_id", ""))
    except Exception as e:
        log('POST', f"⚠️ Failed to load Threads token: {e}")
        return None, None

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
def main():
    log('POST', "=== PRESS BOX POST ===")

    # 0b. FREQUENCY CHECK — Quality > Quantity (skip with --force)
    if "--force" not in sys.argv and is_posting_too_frequent():
        print("⏸️ Skip — baru posting < 30 menit lalu. Use --force to bypass.")
        sys.exit(0)

    # 1. Read staging (check v3 first, then v2)
    staging_file = STAGING["v2"]
    if os.path.exists(STAGING["v3"]):
        try:
            with open(STAGING["v3"]) as f:
                staging = json.load(f)
            if staging.get("topic") and staging.get("content"):
                staging_file = STAGING["v3"]
            else:
                staging_file = STAGING["v2"]
        except Exception as e:
            log(f"   ⚠️ Staging v3 read failed: {e}")
            staging_file = STAGING["v2"]
    elif os.path.exists(STAGING["v2"]):
        staging_file = STAGING["v2"]
    else:
        print("⏸️ Skip — staging kosong.")
        sys.exit(0)

    try:
        with open(staging_file) as f:
            staging = json.load(f)
    except Exception as e:
        log(f"   ⚠️ Staging read failed: {e}")
        print("⏸️ Skip — staging corrupt.")
        sys.exit(0)

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

    # 3. Post to Threads (with timeout partial output handling)
    log('POST', "Posting to Threads as chain via reply_to_id...")
    # Chain driver reads from staging file directly — no need to write latest.md
    post_cmd = f"python3 {POST_SCRIPT} --staging {shlex.quote(staging_file)} 2>&1"
    post_out, code = shell(post_cmd, timeout=200)

    # 4. Extract root ID and permalink
    root_id, permalink, post_ids = extract_post_ids(post_out)

    if not root_id:
        log('POST', f"❌ Failed — no root post ID (output: {post_out[:300]})")
        title = topic.get("title", "?")
        print("❌ Post error — gagal posting.")
        send_alert("POST failed", f"No root ID. Topic: {title[:60]}")
        _cleanup(remove_pending=True, current_topic=topic)
        sys.exit(1)

    # 5. SAFETY: detect partial post (< 4 slides posted, or fewer than expected)
    mode = staging.get("mode", "thread")

    # Count expected slides from content (separated by ---)
    expected_slides = 0
    if content:
        raw_slides = [s for s in re.split(r'(?:\n|^)===\s*\n', content) if s.strip()]
        expected_slides = max(1, len(raw_slides))

    # 5a. CHAIN STRUCTURE VERIFICATION — query Threads API to confirm chain (reply_to_id),
    # not fan-out siblings. Catches the regression where slides 3-N get posted as siblings.
    if mode != "single_paragraph" and expected_slides > 1 and root_id:
        log('POST', f"🔍 Verifying chain structure via API (expecting exactly 1 top-level reply — the chain head)...")
        access_token, _ = _load_threads_token()
        if access_token:
            ok, actual, expected_replies = verify_chain_structure(root_id, expected_slides, access_token)
            if ok is False:
                if actual == 0:
                    log('POST', f"🚨 CHAIN BROKEN: 0 replies on root — slide 2 failed to reply. Auto-deleting...")
                    msg = "Chain broken (slide 2 didn't reply to root)"
                else:
                    log('POST', f"🚨 CHAIN BROKEN — FAN-OUT REGRESSION: {actual} top-level replies (expected {expected_replies}). Auto-deleting...")
                    msg = f"Fan-out regression: {actual} top-level replies instead of 1 (chain head)"
                # Delete chain — use post_pressbox_thread's delete or fall back to direct-post's delete
                # The chain driver doesn't have --delete; fall back to direct-post for safety
                del_cmd = f"python3 {os.path.dirname(POST_SCRIPT)}/pressbox-direct-post.py --delete {root_id} --partial"
                del_out, del_code = shell(del_cmd, timeout=15)
                if del_code != 0:
                    log('POST', f"❌ Delete failed (exit {del_code}): {del_out[:200]}")
                    send_alert("CHAIN BROKEN + DELETE FAILED", f"{msg}. Manual delete needed for root {root_id}.")
                else:
                    send_alert("CHAIN BROKEN", f"{msg}. Auto-deleted '{topic.get('title','?')[:50]}'.")
                _cleanup(remove_pending=True, current_topic=topic)
                sys.exit(1)
            elif ok is True:
                log('POST', f"✅ Chain structure OK: {actual} top-level reply (chain head visible)")
            else:
                log('POST', f"⚠️ Could not verify chain via API (skipped)")
        else:
            log('POST', f"⚠️ No Threads token — skipped API verification")

    # Partial post detection
    # < 3 slides = truly broken → delete via pressbox-direct-post.py (chain driver has no --delete)
    # 3+ slides but < expected = acceptable → warn only
    if mode != "single_paragraph" and len(post_ids) < 3:
        log('POST', f"⚠️ Critical partial post ({len(post_ids)} of {expected_slides} slides), deleting...")
        del_cmd = f"python3 {os.path.dirname(POST_SCRIPT)}/pressbox-direct-post.py --delete {root_id} --partial"
        del_out, del_code = shell(del_cmd, timeout=15)
        if del_code != 0:
            log('POST', f"❌ Delete failed (exit {del_code}): {del_out[:200]}")
            send_alert("DELETE FAILED", f"Partial post delete failed for '{topic.get('title','?')[:50]}' — manual cleanup needed.")
        else:
            partial_msg = f"❌ Critical partial post ({len(post_ids)}/{expected_slides} slides) — dihapus."
            print(partial_msg)
            send_alert("PARTIAL POST", f"Only {len(post_ids)}/{expected_slides} slides posted for '{topic.get('title','?')[:50]}' — deleted.")
        _cleanup(remove_pending=True, current_topic=topic)
        sys.exit(0)
    elif mode != "single_paragraph" and len(post_ids) < expected_slides:
        log('POST', f"⚠️ Partial post ({len(post_ids)} of {expected_slides} slides) — keeping thread (warn only)")
        warn_msg = f"⚠️ Partial post ({len(post_ids)}/{expected_slides} slides) — thread kept."
        print(warn_msg)
        # Don't delete, don't exit — continue to tracking and success path

    # 6. Verify (skip if script missing)
    if os.path.exists(VERIFY_SCRIPT):
        verify_cmd = f"python3 {VERIFY_SCRIPT} --post-id {root_id}"
        if topic_url:
            verify_cmd += f" --url {shlex.quote(topic_url)}"
        verify_out, verify_code = shell(verify_cmd, timeout=15)
        if verify_code == 0:
            log('POST', f"✅ Last slide verified OK")
        else:
            log('POST', f"⚠️ Last slide verification failed (exit {verify_code})")
    else:
        log('POST', f"⚠️ Verify script not found, skipping")

    # 7. Update tracking
    try:
        with open(POSTED) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"topics": []}
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
            "url": topic.get("url", ""),
            "posted_at": datetime.now(WIB).isoformat()
        })
        log('POST', f"📝 New tracking entry: {topic.get('title','?')[:50]}")
    with open(POSTED, 'w') as f:
        json.dump(data, f, indent=2)

    # 8. Cleanup
    _cleanup(remove_pending=False, current_topic=topic)
    with open(LATEST_MD, 'w') as f:
        pass

    # 9. Done — simple report
    threads_link = permalink if permalink else f"https://www.threads.com/@parkthebus.football/post/{root_id}"
    title = topic.get("title", "?")
    print(f"✅ {title[:70]}")
    print(f"   {threads_link}")

if __name__ == "__main__":
    main()
