#!/usr/bin/env python3
"""
post_pressbox_thread.py — Post N-post chained thread to Threads.

Reads staging JSON (slide_1 ... slide_N), chains each slide to the previous
via reply_to_id (Threads native "Add to thread" pattern), and prints the
permalink of the root post.

Usage:
    python3 post_pressbox_thread.py [--staging PATH] [--dry-run]
"""
import argparse
import json
import os
import re
import sys
import time
import requests
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "scripts"))
from threads_poster import ThreadsPoster, ThreadsAPIError, GRAPH_API_BASE


def get_post_permalink(post_id: str, token: str) -> str:
    """Fetch real alphanumeric permalink for a post via Threads API.

    The constructed URL with numeric ID often 404s or redirects. The API
    returns a working URL with the alphanumeric shortcode (e.g.
    https://www.threads.com/t/ABC123xyz or /post/ABC123xyz).
    """
    try:
        r = requests.get(
            f"{GRAPH_API_BASE}/{post_id}",
            params={"fields": "permalink", "access_token": token},
            timeout=10,
        )
        if r.status_code == 200:
            permalink = r.json().get("permalink", "").strip()
            if permalink:
                return permalink
    except Exception:
        pass
    return ""


def load_token():
    """Load Threads access token + user_id from ~/.hermes/threads_token.json."""
    token_path = Path.home() / ".hermes" / "threads_token.json"
    if not token_path.exists():
        tok = os.environ.get("THREADS_ACCESS_TOKEN", "")
        uid = os.environ.get("THREADS_USER_ID", "")
        if not tok or not uid:
            print(f"❌ No token file at {token_path} and no env vars set")
            sys.exit(1)
        return tok, uid
    with open(token_path) as f:
        data = json.load(f)
    return data.get("access_token", ""), str(data.get("user_id", ""))


def get_slide_keys(slides_obj):
    """Return ordered list of slide keys (slide_1, slide_2, ...)."""
    keys = [k for k in slides_obj.keys() if k.startswith("slide_") and k[6:].isdigit()]
    return sorted(keys, key=lambda k: int(k.split("_")[1]))


def load_staging(staging_path):
    """Load staging JSON in either v3 (slides dict) or v2 (content with ===) format.

    Returns the staging dict with a guaranteed 'slides' key.
    """
    with open(staging_path) as f:
        staging = json.load(f)

    slides = staging.get("slides")
    if slides:
        return staging  # v3 format already has slides

    # Fallback: parse v2 format — content is slides joined with \n===\n
    content = staging.get("content", "")
    if not content:
        print(f"❌ No slides in staging (no 'slides' key, no 'content' field)")
        sys.exit(1)

    parts = [p.strip() for p in re.split(r'(?:\n|^)===\s*\n', content) if p.strip()]
    if len(parts) < 2:
        print(f"❌ Need at least 2 slides in content, got {len(parts)}")
        sys.exit(1)

    # Synthesize slide_N keys (chain driver only reads .content)
    staging["slides"] = {
        f"slide_{i+1}": {"title": f"SLIDE {i+1}", "content": p}
        for i, p in enumerate(parts)
    }
    return staging


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", default="/home/ubuntu/.hermes/pressbox/staging-v3.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(args.staging) as f:
        staging = json.load(f)

    slides = staging.get("slides")
    # Note: pipeline sets "slides" to an INTEGER COUNT (e.g. 6), not the actual slides dict.
    # Only treat dict/list with actual slide data as valid.
    if not isinstance(slides, (dict, list)) or not slides:
        # Try v2 fallback: parse content field with === separators
        content = staging.get("content", "")
        if content:
            parts = [p.strip() for p in re.split(r'(?:\n|^)===\s*\n', content) if p.strip()]
            if parts:
                staging["slides"] = {
                    f"slide_{i+1}": {"title": f"SLIDE {i+1}", "content": p}
                    for i, p in enumerate(parts)
                }
                slides = staging["slides"]

    if not slides:
        print(f"❌ No slides in staging")
        sys.exit(1)

    slide_keys = get_slide_keys(slides)
    if len(slide_keys) < 2:
        print(f"❌ Need at least 2 slides, got {len(slide_keys)}")
        sys.exit(1)

    parts = [slides[k]["content"] for k in slide_keys]
    image_url = staging.get("image") or staging.get("image_url")
    image_urls = [image_url] + [None] * (len(parts) - 1)  # image only on root

    print(f"📝 {len(parts)}-post chained thread")
    for i, (k, p) in enumerate(zip(slide_keys, parts), 1):
        print(f"   {k} [{len(p)} chars]: {p[:60]}...")
    print(f"   Image: {image_url[:80] if image_url else 'none'}...")

    if args.dry_run:
        print("\n🔍 DRY RUN — not posting")
        for i, (k, p) in enumerate(zip(slide_keys, parts), 1):
            print(f"\n--- {k} ---")
            print(p)
        sys.exit(0)

    tok, uid = load_token()
    poster = ThreadsPoster(access_token=tok, user_id=uid)

    try:
        results = poster.post_thread(parts, image_urls=image_urls)
    except ThreadsAPIError as e:
        print(f"❌ Post failed: {e}")
        if e.payload:
            print(f"   Payload: {e.payload}")
        sys.exit(1)

    # Explicit partial-chain check — threads_poster.post_thread catches
    # ThreadsAPIError internally and returns whatever succeeded. If we got
    # fewer results than parts, surface it loudly so the orchestrator's
    # partial-post + verify_chain_structure guards can fire.
    if len(results) < len(parts):
        missing = len(parts) - len(results)
        print(f"⚠️ PARTIAL CHAIN: posted {len(results)}/{len(parts)} slides "
              f"({missing} failed silently)", file=sys.stderr)
        # Still print successful results so orchestrator can extract root_id
        # and trigger partial-delete / alert. Do NOT exit 0 — orchestrator's
        # sys.exit(1) on partial keeps cron from marking this run as ok.

    print(f"\n✅ Posted {len(results)} posts as chain")
    for i, r in enumerate(results, 1):
        print(f"   [{i}] {r.post_id}: {r.text[:60]}...")

    # Print root permalink (fetch real alphanumeric URL from API)
    root_id = results[0].post_id
    time.sleep(1)  # let Threads API propagate the new post
    real_permalink = get_post_permalink(root_id, tok)
    if real_permalink:
        print(f"\nRoot permalink: {real_permalink}")
    else:
        print(f"\nRoot permalink: https://www.threads.com/@parkthebus.football/post/{root_id}")
        print(f"   ⚠️ Could not fetch real permalink from API — using constructed URL (may redirect)")


if __name__ == "__main__":
    main()
