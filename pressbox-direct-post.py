#!/usr/local/bin/python3
"""
Press Box Direct Post — Post text threads to Threads API with multi-slide support.
Each slide (separated by ===) becomes its own post chained via reply_to_id.

STRATEGY: Publish root first, wait for it to fully index, then create + publish
each reply sequentially using the PUBLISHED post ID as reply_to_id.
5 second delay between each slide to ensure the parent is indexed.

Usage:
  python3 pressbox-direct-post.py --file path.md
  python3 pressbox-direct-post.py --verify
  python3 pressbox-direct-post.py --delete POST_ID
"""

import json, sys, httpx, time, re
import atexit
from pathlib import Path

HOME = Path.home()
TOKEN_FILE = HOME / ".hermes" / "threads_token.json"
THREADS_API = "https://graph.threads.net/v1.0"
_HTTP = httpx.Client(timeout=8)
atexit.register(_HTTP.close)

def load_token():
    data = json.loads(TOKEN_FILE.read_text())
    return data["access_token"], str(data["user_id"])

def create_container(uid, token, text, reply_to=None, image_url=None, max_retries=1):
    """Create a media container with retry on transient errors.
    If image_url provided (root slide only), tries IMAGE then falls back to TEXT."""
    if image_url and not reply_to:
        # Try IMAGE container first
        data = {"media_type": "IMAGE", "image_url": image_url, "text": text.strip(), "access_token": token}
        for attempt in range(max_retries + 1):
            try:
                r = _HTTP.post(f"{THREADS_API}/{uid}/threads", data=data)
                if r.status_code >= 500:
                    if attempt < max_retries:
                        wait_time = 2 + attempt
                        print(f"   ⚠️ Image HTTP {r.status_code} — retry {attempt+1}/{max_retries}", file=sys.stderr)
                        time.sleep(wait_time)
                        continue
                    print(f"   ⚠️ Image failed (HTTP {r.status_code}), fallback to TEXT", file=sys.stderr)
                    break
                result = r.json()
                if r.status_code == 200:
                    print(f"   📷 Image attached to root slide", file=sys.stderr)
                    return result["id"]
                if "transient" in str(result).lower() and attempt < max_retries:
                    wait_time = 2 + attempt
                    print(f"   ⚠️ Image transient — retry {attempt+1}/{max_retries}", file=sys.stderr)
                    time.sleep(wait_time)
                    continue
                print(f"   ⚠️ Image failed ({result.get('error',{}).get('message','?')}), fallback to TEXT", file=sys.stderr)
                break
            except httpx.TimeoutException:
                if attempt < max_retries:
                    print(f"   ⚠️ Image timeout — retry {attempt+1}/{max_retries}", file=sys.stderr)
                    time.sleep(2)
                    continue
                print(f"   ⚠️ Image timeout after retries, fallback to TEXT", file=sys.stderr)
                break
            except Exception as e:
                print(f"   ⚠️ Image error: {e}, fallback to TEXT", file=sys.stderr)
                break
        # Fall through to TEXT fallback
        print(f"   Using TEXT fallback for root slide", file=sys.stderr)

    # TEXT container (default or IMAGE fallback)
    data = {"media_type": "TEXT", "text": text.strip(), "access_token": token}
    if reply_to:
        data["reply_to_id"] = reply_to

    for attempt in range(max_retries + 1):
        try:
            r = _HTTP.post(f"{THREADS_API}/{uid}/threads", data=data)

            # Handle HTTP 500 Transient Server Errors safely
            if r.status_code >= 500:
                if attempt < max_retries:
                    wait_time = 2 + attempt
                    print(f"   ⚠️ Container HTTP {r.status_code} — retry {attempt+1}/{max_retries}", file=sys.stderr)
                    time.sleep(wait_time)
                    continue
                raise Exception(f"Container create failed with HTTP {r.status_code}: {r.text}")

            result = r.json()
            if r.status_code == 200:
                return result["id"]

            # Retry on explicit transient errors in JSON payload
            if "transient" in str(result).lower() and attempt < max_retries:
                wait_time = 2 + attempt
                print(f"   ⚠️ Container transient — retry {attempt+1}/{max_retries}", file=sys.stderr)
                time.sleep(wait_time)
                continue

            raise Exception(f"Container create failed: {result}")
        except httpx.TimeoutException:
            if attempt < max_retries:
                print(f"   ⚠️ Container timeout — retry {attempt+1}/{max_retries}", file=sys.stderr)
                time.sleep(2)
                continue
            raise
    raise Exception(f"Container create failed after {max_retries + 1} attempts")

def publish(uid, token, container_id, max_retries=1):
    """Publish a container. Returns published post ID."""
    for attempt in range(max_retries + 1):
        try:
            r = _HTTP.post(f"{THREADS_API}/{uid}/threads_publish",
                data={"creation_id": container_id, "access_token": token})

            if r.status_code >= 500:
                if attempt < max_retries:
                    wait_time = 2 + attempt
                    print(f"   ⚠️ Publish HTTP {r.status_code} — retry {attempt+1}/{max_retries}", file=sys.stderr)
                    time.sleep(wait_time)
                    continue
                raise Exception(f"Publish failed with HTTP {r.status_code}: {r.text}")

            result = r.json()
            if r.status_code == 200:
                return result.get("id")

            error_msg = result.get("error", {}).get("message", "")
            if "transient" in str(result).lower() and attempt < max_retries:
                wait_time = 2 + attempt
                print(f"   ⚠️ Publish transient: {error_msg[:60]} — retry {attempt+1}/{max_retries}", file=sys.stderr)
                time.sleep(wait_time)
                continue

            raise Exception(f"Publish failed: {result}")
        except httpx.TimeoutException:
            if attempt < max_retries:
                print(f"   ⚠️ Publish timeout — retry {attempt+1}/{max_retries}", file=sys.stderr)
                time.sleep(2)
                continue
            raise
    raise Exception(f"Publish failed after {max_retries + 1} attempts")

def get_latest_permalink(uid, token):
    """Get the actual post permalink (alphanumeric format) for the most recent post."""
    try:
        r = _HTTP.get(f"{THREADS_API}/{uid}/threads",
            params={"fields": "id,permalink,text", "limit": "3", "access_token": token},
            timeout=10)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                return data[0].get("permalink", "")
    except Exception:
        pass
    return ""

def post_thread(uid, token, slides, image_url=None):
    """
    Post slides as a Threads CAROUSEL (fan-out, not chain).

    Slide 1 is the root post. Slides 2-N are all direct replies to slide 1
    (fan-out), so they appear as siblings in the Threads UI.

    Post order: slide 1 (root) first, then slides N, N-1, ..., 2 (reverse).
    Threads UI shows replies newest-first, so this reverse posting produces
    the correct carousel order top→bottom: S1, S2, S3, S4, S5, S6.

    image_url: attach to root slide (slide 1) if provided.
    """
    filtered = [s for s in slides if s.strip()]
    if not filtered:
        return []

    post_ids = []
    root_pid = None  # Track the root post ID; all slides reply to this (fan-out)

    # Post slide 1 (root) first
    # Then post remaining slides in REVERSE order (N, N-1, ..., 2)
    # so the newest reply ends up being slide 2, which Threads UI displays
    # directly below the root — preserving carousel order top→bottom.
    slide_indices = [0] + list(range(len(filtered) - 1, 0, -1))

    for i, slide_idx in enumerate(slide_indices):
        slide = filtered[slide_idx]
        text = slide.strip()
        if not text:
            continue

        # Char-cap safety net: Threads API rejects > 500 chars. Pipeline should already trim,
        # but this is the final guard in case staging was written before the fix.
        if len(text) > 500:
            trimmed = text[:500]
            last_period = max(trimmed.rfind(". "), trimmed.rfind("! "), trimmed.rfind("? "))
            if last_period > 50:
                text = trimmed[:last_period + 1]
            else:
                text = trimmed.rstrip() + "…"
            print(f"   ✂️ Slide {slide_idx+1} char-trimmed to {len(text)} chars (final guard)", file=sys.stderr)

        # Carousel parent: root for all slides (fan-out, not chain)
        reply_to = root_pid if i > 0 else None

        try:
            if reply_to:
                print(f"   Slide {slide_idx+1}/{len(filtered)}: creating reply to root {root_pid}...", file=sys.stderr)
            else:
                print(f"   Slide {slide_idx+1}/{len(filtered)}: creating root container...", file=sys.stderr)

            cid = create_container(uid, token, text, reply_to, image_url if slide_idx == 0 else None)
            print(f"   Slide {slide_idx+1}/{len(filtered)}: publishing...", file=sys.stderr)
            pid = publish(uid, token, cid)
            post_ids.append((slide_idx, pid))
            print(f"   Slide {slide_idx+1}/{len(filtered)}: → {pid}", file=sys.stderr)

            if i == 0:
                # Save root ID for all subsequent slides
                root_pid = pid
                print(f"Root: {pid}")
                # Get actual permalink (alphanumeric format like DZvnqdoE7-k)
                time.sleep(1)
                permalink = get_latest_permalink(uid, token)
                if permalink:
                    print(f"Post: {permalink}")
                else:
                    print(f"Post: https://www.threads.com/@parkthebus.football/post/{pid}")
        except Exception as e:
            print(f"   ⚠️ Slide {slide_idx+1}/{len(filtered)} failed: {e}", file=sys.stderr)
            # RETRY: wait 5s and try once more before giving up
            try:
                time.sleep(5)
                print(f"   🔄 Retrying slide {i+1}/{len(filtered)}...", file=sys.stderr)
                cid = create_container(uid, token, text, reply_to, image_url if i == 0 else None)
                pid = publish(uid, token, cid)
                post_ids.append((slide_idx, pid))
                print(f"   ✅ Slide {i+1}/{len(filtered)} retry succeeded: → {pid}", file=sys.stderr)
                if i == 0:
                    root_pid = pid
                    print(f"Root: {pid}")
                    time.sleep(1)
                    permalink = get_latest_permalink(uid, token)
                    if permalink:
                        print(f"Post: {permalink}")
                    else:
                        print(f"Post: https://www.threads.com/@parkthebus.football/post/{pid}")
            except Exception as retry_err:
                print(f"   ❌ Slide {i+1}/{len(filtered)} retry also failed: {retry_err}", file=sys.stderr)
                if root_pid is None and i > 0:
                    print(f"   🛑 Cannot continue — no root post to reply to.", file=sys.stderr)
                    break
                print(f"   Continuing with remaining slides...", file=sys.stderr)
                # Skip this slide but keep root_pid for the rest
            continue

        if i < len(filtered) - 1:
            # Rate limit avoidance: 10s pauses to stay under API limit
            if i == 1:  # After slide 2
                time.sleep(10)
            elif i == 3:  # After slide 4
                time.sleep(10)
            else:
                time.sleep(3)  # Wait for Threads API to index parent post

    return post_ids

def parse_slides(text):
    """Split text into slides by === separator."""
    slides = re.split(r'(?:\n|^)===\s*\n', text)
    return [s.strip() for s in slides if s.strip()]

def verify_posts(uid, token, limit=15):
    """Check recent posts."""
    r = _HTTP.get(f"{THREADS_API}/{uid}/threads",
        params={"access_token": token, "fields": "id,text,timestamp", "limit": limit})
    results = []
    for post in r.json().get("data", []):
        text = post.get("text", "")
        if text.strip():
            has_bare = bool(re.search(r'https?://[^\s\[\]]+', text))
            has_bracket = "[Source" in text or "[http" in text
            results.append((post["id"], has_bare, has_bracket, text[:80]))
    return results

def delete_post(uid, token, post_id):
    """Delete a post by ID."""
    r = _HTTP.delete(f"{THREADS_API}/{post_id}",
        params={"access_token": token})
    return r.status_code == 200

def main():
    token, uid = load_token()
    image_url = None

    if "--image" in sys.argv:
        idx = sys.argv.index("--image")
        if idx + 1 < len(sys.argv):
            image_url = sys.argv[idx + 1]
            print(f"📷 Image URL provided: {image_url[:60]}...", file=sys.stderr)

    if "--verify" in sys.argv:
        results = verify_posts(uid, token, 10)
        all_ok = True
        for pid, has_bare, has_bracket, preview in results:
            if has_bare:
                status = "✅ CLICKABLE"
            elif has_bracket:
                status = "⚠️ BRACKETED"
                all_ok = False
            else:
                status = "❌ NO URL"
                all_ok = False
            print(f"{status} | {pid} | {preview}")
        sys.exit(0 if all_ok else 1)

    if "--delete" in sys.argv:
        idx = sys.argv.index("--delete")
        if idx + 1 >= len(sys.argv):
            print("❌ Error: Missing POST_ID after --delete")
            sys.exit(1)
        pid = sys.argv[idx + 1]
        is_partial = "--partial" in sys.argv
        if delete_post(uid, token, pid):
            reason = " (partial cleanup)" if is_partial else ""
            print(f"✅ Deleted: {pid}{reason}")
        else:
            print(f"❌ Delete failed: {pid}")
        sys.exit(0)

    text = ""
    if "--file" in sys.argv:
        idx = sys.argv.index("--file")
        if idx + 1 >= len(sys.argv):
            print("❌ Error: Missing file path after --file")
            sys.exit(1)
        text = Path(sys.argv[idx + 1]).read_text().strip()
    elif not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        text = " ".join(sys.argv[1:])
    else:
        print("Usage: --file path.md, pipe stdin, or provide text")
        sys.exit(1)

    if not text:
        print("❌ Empty text")
        sys.exit(1)

    plain_text = re.sub(r'[*_~`#>\[\]|]', '', text).strip()
    if len(plain_text) < 50:
        print(f"❌ Text too short ({len(plain_text)} chars, min 50) — skipping to avoid empty post")
        sys.exit(1)

    slides = parse_slides(text)
    print(f"📝 {len(slides)} slides detected", file=sys.stderr)

    post_ids = post_thread(uid, token, slides, image_url)
    if not post_ids:
        print("❌ No slides posted")
        sys.exit(1)
    # post_ids is list of (slide_idx, pid) tuples; sort by slide_idx for stable output
    sorted_ids = sorted(post_ids, key=lambda x: x[0])
    pids_only = [pid for _, pid in sorted_ids]
    print(f"✅ Thread posted: {len(post_ids)} slides")
    print(f"   Root: {sorted_ids[0][1]}")
    for slide_idx, pid in sorted_ids:
        print(pid)

if __name__ == "__main__":
    main()