#!/usr/bin/env python3
"""
poster.py — Post carousel slides to Threads as thread replies.
Token: reads from /home/ubuntu/threads-agent/.env
"""
import os
import sys
import time
import httpx
from pathlib import Path

# Load token from threads-agent .env
_ENV_PATH = Path.home() / "threads-agent" / ".env"
def _load_env():
    env = {}
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    os.environ.update(env)

_load_env()

GRAPH = "https://graph.threads.net/v1.0"
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
USER_ID = os.environ.get("THREADS_USER_ID", "")


def _post_container(text: str, reply_to: str | None = None, image_url: str | None = None) -> str | None:
    """Create a single thread container. Returns container_id."""
    data = {"access_token": TOKEN}
    
    # Validate image URL before posting
    if image_url:
        try:
            h = httpx.head(image_url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=10)
            ct = h.headers.get("content-type", "")
            if h.status_code != 200 or not ct.startswith("image/"):
                print(f"  [WARN] Image invalid (status={h.status_code}, type={ct}), falling back to TEXT")
                image_url = None
        except Exception as e:
            print(f"  [WARN] Image check failed: {e}, falling back to TEXT")
            image_url = None
    
    if image_url:
        params = {
            "access_token": TOKEN,
            "media_type": "IMAGE",
            "image_url": image_url,
            "text": text,
        }
        if reply_to:
            params["reply_to_id"] = reply_to
        r = httpx.post(f"{GRAPH}/{USER_ID}/threads", data=params, timeout=30)
        if r.status_code != 200:
            print(f"  [WARN] Image post failed, falling back to TEXT")
            r = httpx.post(f"{GRAPH}/{USER_ID}/threads", data={
                "access_token": TOKEN,
                "media_type": "TEXT",
                "text": text,
                **({"reply_to_id": reply_to} if reply_to else {}),
            }, timeout=30)
    else:
        params = {
            "access_token": TOKEN,
            "media_type": "TEXT",
            "text": text,
        }
        if reply_to:
            params["reply_to_id"] = reply_to
        r = httpx.post(f"{GRAPH}/{USER_ID}/threads", data=params, timeout=30)
    
    if r.status_code != 200:
        print(f"  [ERR] Container create failed: {r.status_code} {r.text[:200]}")
        return None
    
    container_id = r.json().get("id")
    if not container_id:
        print(f"  [ERR] No container ID in response: {r.json()}")
        return None
    
    # Wait for container to be ready (Threads needs processing time)
    time.sleep(2)
    
    # Publish the container
    r2 = httpx.post(f"{GRAPH}/{USER_ID}/threads_publish", data={
        "access_token": TOKEN,
        "creation_id": container_id,
    }, timeout=30)
    
    if r2.status_code != 200:
        print(f"  [ERR] Publish failed: {r2.status_code} {r2.text[:200]}")
        return None
    
    post_id = r2.json().get("id")
    return post_id


def post_carousel(slides: list[str], image_url: str = None) -> list[str]:
    """Post a carousel as a thread chain. Returns list of post IDs.
    Injects permalink into last slide after posting first slide (needs root_id)."""
    if not TOKEN:
        print("ERROR: THREADS_ACCESS_TOKEN not set")
        return []
    if not USER_ID:
        print("ERROR: THREADS_USER_ID not set")
        return []
    
    post_ids = []
    reply_to = None
    
    for i, text in enumerate(slides):
        print(f"  Slide {i+1}/{len(slides)}: {text[:60]}...")
        
        # First slide gets image if provided
        img = image_url if (i == 0 and image_url) else None
        
        post_id = _post_container(text, reply_to=reply_to, image_url=img)
        if not post_id:
            print(f"  [ERR] Failed at slide {i+1}, stopping chain")
            break
        
        post_ids.append(post_id)
        reply_to = post_id
        print(f"  ✓ Posted: {post_id}")
        
        # Rate limit: wait between posts
        if i < len(slides) - 1:
            time.sleep(3)
    
    return post_ids


def post_from_db(limit: int = 1, dry_run: bool = False):
    """Post staged posts from pipeline.db."""
    from db import get_db, get_staged_posts, mark_posted
    
    conn = get_db()
    staged = get_staged_posts(conn, limit)
    if not staged:
        print("No staged posts.")
        return
    
    for post in staged:
        print(f"\nPosting: {post['title'][:60]}...")
        
        # Build slides from DB columns
        slides = []
        for key in ['slide_hook', 'slide_setup', 'slide_twist', 'slide_deep', 'slide_sowhat', 'slide_cta']:
            val = post.get(key, '')
            if val:
                slides.append(val)
        
        if not slides:
            print("  [SKIP] No slides")
            continue
        
        print(f"  {len(slides)} slides, image: {post.get('image', 'none')[:60]}")
        
        if dry_run:
            for i, s in enumerate(slides, 1):
                print(f"  [{i}] {s[:80]}")
            continue
        
        post_ids = post_carousel(slides, image_url=post.get('image'))
        if post_ids:
            mark_posted(conn, post['id'], post_ids[0])
            print(f"  ✓ Thread root: {post_ids[0]}")
        else:
            print("  ✗ Failed")
    
    conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    post_from_db(dry_run=dry)
