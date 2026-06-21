#!/usr/bin/env python3
"""
PRESS BOX POST — Phase 2.
Read staging → post to Threads → verify → update tracking.
"""
import json, os, subprocess, sys, time, requests, re
import shlex
from datetime import datetime
from pressbox_common import log, send_alert, load_env, WIB, STAGING, POSTED, HOME

POST_SCRIPT = f"{os.path.dirname(os.path.abspath(__file__))}/pressbox-direct-post.py"
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
            
    # Clear both staging files
    for sf in [STAGING["v2"], STAGING["v3"]]:
        try:
            with open(sf, 'w') as f:
                json.dump({"topic": None, "written_at": None}, f)
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
    """Extract root ID, permalink, and all post IDs from output."""
    post_ids = []
    seen = set()
    root_id = None
    permalink = None
    for line in output.split('\n'):
        line_stripped = line.strip()
        if line_stripped.startswith('Root:') and not root_id:
            rid = line_stripped.split('Root:', 1)[1].strip()
            if rid.isdigit() and len(rid) > 15:
                root_id = rid
        elif line_stripped.startswith('Post:') and not permalink:
            permalink = line_stripped.split('Post:', 1)[1].strip()
        elif line_stripped.isdigit() and len(line_stripped) > 15:
            if line_stripped not in seen:
                seen.add(line_stripped)
                post_ids.append(line_stripped)
        elif '→' in line_stripped:
            pid_part = line_stripped.split('→', 1)[1].strip()
            if pid_part.isdigit() and len(pid_part) > 15:
                if pid_part not in seen:
                    seen.add(pid_part)
                    post_ids.append(pid_part)
    return root_id, permalink, post_ids

def verify_carousel_structure(root_id, expected_slides, access_token, max_attempts=3):
    """Query Threads API to verify slides posted as fan-out (siblings), not chain (nested).

    Also verifies that the reply ORDER is correct for carousel display:
    - Expected posting order: S1 (root, oldest) → S6 → S5 → S4 → S3 → S2 (newest reply)
    - Threads UI shows newest-first, so S2 should be the FIRST reply in the API response
      (immediately below root in the carousel).
    - API returns replies in newest-first order. If replies are in oldest-first order,
    the carousel will be reversed in the UI.

    Returns:
        (ok, actual_replies, expected_replies)
        ok: True if fan-out + order correct, False if broken, None if API check failed
        actual_replies: count of top-level replies from API
        expected_replies: N-1 (root + N-1 siblings for an N-slide carousel)
    """
    if expected_slides <= 1:
        # Single post, no replies expected
        return True, 0, 0

    expected_replies = expected_slides - 1  # Root + N-1 siblings

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
                if attempt < max_attempts:
                    time.sleep(5)
                    continue
                return None, 0, expected_replies

            data = r.json()
            replies = data.get("replies", {}).get("data", [])
            actual = len(replies)

            if actual < expected_replies:
                # API might still be indexing — retry
                if attempt < max_attempts:
                    time.sleep(5)
                    continue
                return False, actual, expected_replies

            # Order check: Threads API returns replies newest-first. The newest reply
            # should be the FIRST one in the array (immediately below root in UI).
            # Verify by comparing timestamps — first reply must be newer than last.
            timestamps = [rep.get("timestamp", "") for rep in replies]
            if len(timestamps) >= 2:
                # Parse and compare; first should be > last (newest first)
                from datetime import datetime
                try:
                    parsed = [datetime.fromisoformat(ts.replace("Z", "+00:00")) for ts in timestamps]
                    if parsed[0] < parsed[-1]:
                        # Replies are in oldest-first order → carousel will be reversed in UI
                        log('POST', f"🚨 REVERSED ORDER: first reply ({parsed[0]}) is OLDER than last ({parsed[-1]}). Carousel will appear reversed in UI.")
                        return False, actual, expected_replies
                except Exception as e:
                    # Timestamp parse failed — don't block on this, just warn
                    log('POST', f"⚠️ Could not parse timestamps for order check: {e}")

            return True, actual, expected_replies
        except Exception as e:
            if attempt < max_attempts:
                time.sleep(5)
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

    # 2. Write content to latest.md
    os.makedirs(os.path.dirname(LATEST_MD), exist_ok=True)
    with open(LATEST_MD, 'w') as f:
        f.write(content)

    # 3. Post to Threads (with timeout partial output handling)
    log('POST', "Posting to Threads...")
    image_url = staging.get("image_url") or ""
    image_flag = f" --image {shlex.quote(image_url)}" if image_url else ""
    post_cmd = f"python3 {POST_SCRIPT} --file {LATEST_MD}{image_flag} 2>&1"
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

    # 5a. CAROUSEL STRUCTURE VERIFICATION — query Threads API to confirm fan-out (siblings, not chain).
    # Catches the parent_pid=pid bug where slides 3-N get nested under slide 2.
    if mode != "single_paragraph" and expected_slides > 1 and root_id:
        log('POST', f"🔍 Verifying carousel structure via API (expecting {expected_slides-1} top-level replies)...")
        access_token, _ = _load_threads_token()
        if access_token:
            ok, actual, expected_replies = verify_carousel_structure(root_id, expected_slides, access_token)
            if ok is False:
                # FAN-OUT BUG: slides are nested (chain), not siblings
                log('POST', f"🚨 Carousel structure BROKEN: {actual} top-level replies, expected {expected_replies}")
                log('POST', f"🚨 Slides 3-N likely hidden under slide 2 (chain, not fan-out). Auto-deleting...")
                del_out, del_code = shell(f"python3 {POST_SCRIPT} --delete {root_id} --partial", timeout=15)
                if del_code != 0:
                    log('POST', f"❌ Delete failed (exit {del_code}): {del_out[:200]}")
                    send_alert("CAROUSEL BROKEN + DELETE FAILED", f"Fan-out: {actual} replies, expected {expected_replies}. Manual delete needed for root {root_id}.")
                else:
                    send_alert("CAROUSEL BROKEN", f"Fan-out bug: {actual} replies, expected {expected_replies}. Auto-deleted '{topic.get('title','?')[:50]}'.")
                _cleanup(remove_pending=True, current_topic=topic)
                sys.exit(1)
            elif ok is True:
                log('POST', f"✅ Carousel structure OK: {actual} top-level replies (expected {expected_replies})")
            else:
                log('POST', f"⚠️ Could not verify carousel via API (skipped)")
        else:
            log('POST', f"⚠️ No Threads token — skipped API verification")

    # Partial post detection
    # < 3 slides = truly broken → delete
    # 3+ slides but < expected = acceptable → warn only
    if mode != "single_paragraph" and len(post_ids) < 3:
        log('POST', f"⚠️ Critical partial post ({len(post_ids)} of {expected_slides} slides), deleting...")
        del_out, del_code = shell(f"python3 {POST_SCRIPT} --delete {root_id} --partial", timeout=15)
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
