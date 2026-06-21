#!/usr/bin/env python3
"""
post_pressbox_thread.py — Post a 2-post chained thread to Threads.

Reads staging JSON (slide_1 = hook, slide_2 = detail), chains S2 to S1 via
reply_to_id, and prints the permalink of the root post.

Usage:
    python3 post_pressbox_thread.py [--staging PATH] [--dry-run]

Environment:
    THREADS_ACCESS_TOKEN, THREADS_USER_ID  (read from ~/.hermes/threads_token.json if not set)
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "scripts"))
from threads_poster import ThreadsPoster, ThreadsAPIError


def load_token():
    """Load Threads access token + user_id from ~/.hermes/threads_token.json."""
    token_path = Path.home() / ".hermes" / "threads_token.json"
    if not token_path.exists():
        # Fallback to env
        tok = os.environ.get("THREADS_ACCESS_TOKEN", "")
        uid = os.environ.get("THREADS_USER_ID", "")
        if not tok or not uid:
            print(f"❌ No token file at {token_path} and no env vars set")
            sys.exit(1)
        return tok, uid
    with open(token_path) as f:
        data = json.load(f)
    return data.get("access_token", ""), str(data.get("user_id", ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", default="/home/ubuntu/.hermes/pressbox/staging-v3.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(args.staging) as f:
        staging = json.load(f)

    slides = staging.get("slides")
    if not slides or len(slides) < 2:
        print(f"❌ Need 2 slides in staging, got {len(slides) if slides else 0}")
        sys.exit(1)

    parts = [slides["slide_1"]["content"], slides["slide_2"]["content"]]
    image_url = staging.get("image") or staging.get("image_url")
    image_urls = [image_url, None]  # image on root only

    print(f"📝 2-post thread")
    print(f"   S1 (root): {len(parts[0])} chars")
    print(f"   S2 (reply): {len(parts[1])} chars")
    print(f"   Image: {image_url[:80] if image_url else 'none'}...")

    if args.dry_run:
        print("\n🔍 DRY RUN — not posting")
        print(f"\n--- S1 (root) ---")
        print(parts[0])
        print(f"\n--- S2 (chained reply) ---")
        print(parts[1])
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

    print(f"\n✅ Posted {len(results)} posts as chain")
    for i, r in enumerate(results, 1):
        print(f"   [{i}] {r.post_id}: {r.text[:60]}...")

    # Print root permalink
    root_id = results[0].post_id
    print(f"\nRoot permalink: https://www.threads.com/@parkthebus.football/post/{root_id}")


if __name__ == "__main__":
    main()
