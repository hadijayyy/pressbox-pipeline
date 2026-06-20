#!/usr/bin/env python3
"""
Verify Last Slide — verify the last slide of a posted carousel contains the
expected URL. Used by pressbox-post.py to confirm the CTA (call-to-action)
slide has a working link before marking the post as complete.

Usage:
  python3 verify-last-slide.py --post-id <root_post_id> --url <expected_url>
  python3 verify-last-slide.py --post-id <root_post_id>

If --url is provided, checks that the specific URL appears in the last slide.
If omitted, checks that any http(s) URL-like pattern is present.

Exits 0 if the URL is found, 1 if missing or unreachable.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

HOME = Path.home()
TOKEN_FILE = HOME / ".hermes" / "threads_token.json"
THREADS_API = "https://graph.threads.net/v1.0"
_HTTP = httpx.Client(timeout=10)


def load_token():
    """Load Threads API token from the shared token file."""
    data = json.loads(TOKEN_FILE.read_text())
    return data["access_token"], str(data["user_id"])


def fetch_last_reply_text(token, root_id, max_depth=10):
    """Traverse nested reply chain to find the last slide's text.

    Each slide in the carousel is a reply to the previous one, so we walk
    the chain via GET /{id}/replies until we hit the leaf post.
    """
    pid = root_id
    last_text = ""
    for _ in range(max_depth):
        try:
            r = _HTTP.get(
                f"{THREADS_API}/{pid}/replies",
                params={"access_token": token, "fields": "id,text", "limit": "1"},
                timeout=10,
            )
            if r.status_code != 200:
                break
            replies = r.json().get("data", [])
            if not replies:
                break
            last_text = replies[0].get("text", "")
            pid = replies[0]["id"]
        except (httpx.HTTPError, KeyError, json.JSONDecodeError):
            break
    return last_text


def get_root_text(token, post_id):
    """Fetch the root post text (fallback if there are no replies)."""
    try:
        r = _HTTP.get(
            f"{THREADS_API}/{post_id}",
            params={"access_token": token, "fields": "id,text"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("text", "")
    except (httpx.HTTPError, KeyError, json.JSONDecodeError):
        pass
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="Verify the last carousel slide contains the expected URL"
    )
    parser.add_argument(
        "--post-id",
        required=True,
        help="Root post ID of the carousel (numeric Threads ID)",
    )
    parser.add_argument(
        "--url",
        default="",
        help="Expected URL to verify in the last slide text",
    )
    args = parser.parse_args()

    token, _ = load_token()

    # Traverse reply chain to find the last slide text
    last_text = fetch_last_reply_text(token, args.post_id)

    # If no replies were found, the post might be a single slide
    if not last_text:
        last_text = get_root_text(token, args.post_id)

    if not last_text:
        print("❌ Could not retrieve any slide text from the post", file=sys.stderr)
        sys.exit(1)

    # Determine what to check
    expected_url = args.url.strip()
    if expected_url:
        # Check for the specific URL
        if expected_url in last_text:
            print(f"✅ Last slide contains the expected URL: {expected_url}")
            sys.exit(0)
        else:
            print(f"❌ Expected URL not found in last slide", file=sys.stderr)
            print(f"   Expected: {expected_url}", file=sys.stderr)
            snippet = last_text[:300].replace("\n", " | ")
            print(f"   Last slide: {snippet}", file=sys.stderr)
            sys.exit(1)
    else:
        # No specific URL — check for any http(s) link in the text
        if re.search(r"https?://[^\s)]+", last_text):
            print(f"✅ Last slide contains a URL")
            sys.exit(0)
        else:
            print(f"❌ No URL found in last slide text", file=sys.stderr)
            snippet = last_text[:300].replace("\n", " | ")
            print(f"   Last slide: {snippet}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
