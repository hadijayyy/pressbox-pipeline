#!/usr/bin/env python3
"""
threads_auth.py — Threads API OAuth helper.
Generates auth URL, accepts redirect code, exchanges for long-lived token.

Usage:
  1. Set APP_ID, APP_SECRET, REDIRECT_URI below
  2. Run: python3 threads_auth.py
  3. Open printed URL → authorize → copy 'code' from redirect
  4. Paste code → get long-lived token (60 days)
  5. To refresh: python3 threads_auth.py --refresh <token>
"""
import sys
import httpx

# Reads credentials from /home/ubuntu/threads-agent/.env
from pathlib import Path as _Path
def _load_creds():
    env = {}
    p = _Path.home() / "threads-agent" / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env
_creds = _load_creds()
APP_ID = _creds.get("THREADS_APP_ID", "")
APP_SECRET = _creds.get("THREADS_APP_SECRET", "")
REDIRECT_URI = "https://developers.facebook.com/tools/explorer/"
SCOPES = "threads_basic,threads_content_publish"

GRAPH = "https://graph.threads.net"


def get_auth_url():
    return (
        f"https://threads.net/oauth/authorize"
        f"?client_id={APP_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={SCOPES}"
        f"&response_type=code"
    )


def exchange_code(code: str) -> dict:
    """code → short-lived token."""
    r = httpx.get(f"{GRAPH}/oauth/access_token", params={
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }, timeout=15)
    r.raise_for_status()
    return r.json()


def to_long_lived(short_token: str) -> dict:
    """short-lived → long-lived (60 days)."""
    r = httpx.get(f"{GRAPH}/access_token", params={
        "grant_type": "th_exchange_token",
        "client_secret": APP_SECRET,
        "access_token": short_token,
    }, timeout=15)
    r.raise_for_status()
    return r.json()


def refresh_long_lived(token: str) -> dict:
    """Refresh a long-lived token (must be ≥24h old, <60 days)."""
    r = httpx.get(f"{GRAPH}/refresh_access_token", params={
        "grant_type": "th_refresh_token",
        "access_token": token,
    }, timeout=15)
    r.raise_for_status()
    return r.json()


def get_user_id(token: str) -> dict:
    """Get Threads user ID from token."""
    r = httpx.get(f"{GRAPH}/v1.0/me", params={
        "fields": "id,username,threads_profile_picture_url",
        "access_token": token,
    }, timeout=15)
    r.raise_for_status()
    return r.json()


def test_post(token: str, user_id: str, text: str = "🔧 API test — ignore"):
    """Create a text thread post."""
    r = httpx.post(f"{GRAPH}/v1.0/{user_id}/threads", params={
        "access_token": token,
    }, data={
        "media_type": "TEXT",
        "text": text,
    }, timeout=15)
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    if not APP_ID or not APP_SECRET:
        print("ERROR: Fill APP_ID, APP_SECRET, REDIRECT_URI at top of file.")
        sys.exit(1)

    if len(sys.argv) >= 3 and sys.argv[1] == "--refresh":
        print("Refreshing long-lived token...")
        result = refresh_long_lived(sys.argv[2])
        print(f"New token: {result.get('access_token', 'FAILED')}")
        print(f"Expires in: {result.get('expires_in', '?')} seconds")
        sys.exit(0)

    print("=" * 60)
    print("THREADS API — TOKEN GENERATOR")
    print("=" * 60)
    print(f"\n1. Open this URL in browser:\n\n{get_auth_url()}\n")
    print("2. Authorize → you'll be redirected with ?code=XXXX")
    print("3. Copy the 'code' value and paste below:\n")

    code = input("Code: ").strip()
    if not code:
        print("No code provided.")
        sys.exit(1)

    print("\nExchanging code for short-lived token...")
    short = exchange_code(code)
    short_token = short.get("access_token")
    if not short_token:
        print(f"ERROR: {short}")
        sys.exit(1)
    print(f"  Short-lived token: {short_token[:20]}...")

    print("\nConverting to long-lived token...")
    long = to_long_lived(short_token)
    long_token = long.get("access_token")
    if not long_token:
        print(f"ERROR: {long}")
        sys.exit(1)
    expires = long.get("expires_in", 0)
    print(f"  Long-lived token: {long_token}")
    print(f"  Expires in: {expires // 86400} days ({expires}s)")

    print("\nGetting user info...")
    user = get_user_id(long_token)
    print(f"  User ID: {user.get('id', '?')}")
    print(f"  Username: {user.get('username', '?')}")

    print("\n" + "=" * 60)
    print("DONE! Save these:")
    print(f"  THREADS_TOKEN={long_token}")
    print(f"  THREADS_USER_ID={user.get('id', '')}")
    print("=" * 60)

    do_test = input("\nTest post? (y/n): ").strip().lower()
    if do_test == "y":
        result = test_post(long_token, user.get("id", ""))
        print(f"  Post result: {result}")
