#!/usr/local/bin/python3
"""Threads Token Refresh — auto-refresh long-lived token before expiry.

Usage:
    python3 threads-token-refresh.py          # Check and refresh if needed
    python3 threads-token-refresh.py --force  # Force refresh

This script:
    - Reads token from ~/.hermes/threads_token.json
    - Checks if it's expired or expiring soon (<7 days)
    - Refreshes using the Threads API refresh endpoint
    - Saves the new token back

Set up a weekly cron job to keep the token fresh:
    0 9 * * 1 python3 /home/ubuntu/pressbox-pipeline/threads-token-refresh.py
"""

import json, sys, time
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

TOKEN_FILE = Path.home() / ".hermes" / "threads_token.json"
CONFIG_FILE = Path.home() / ".hermes" / "threads_app.json"
THREADS_API = "https://graph.threads.net/v1.0"

REFRESH_THRESHOLD_DAYS = 7  # Refresh if expiring within 7 days


def load_token():
    if not TOKEN_FILE.exists():
        print(f"❌ Token file not found: {TOKEN_FILE}")
        print("   Run: python3 threads-oauth-setup.py")
        sys.exit(1)

    with open(TOKEN_FILE) as f:
        return json.load(f)


def load_app_secret():
    if not CONFIG_FILE.exists():
        print(f"❌ Config not found: {CONFIG_FILE}")
        sys.exit(1)

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    return config.get("app_secret")


def save_token(data):
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"💾 Token saved to: {TOKEN_FILE}")


def refresh_token(current_token, app_secret):
    """Refresh long-lived token using the Threads refresh endpoint."""
    print("⏳ Refreshing token...")

    r = requests.get(f"{THREADS_API}/refresh_access_token", params={
        "grant_type": "th_refresh_token",
        "access_token": current_token,
    })

    if r.status_code != 200:
        print(f"❌ Refresh failed: {r.status_code}")
        print(f"   Response: {r.text}")
        print()
        print("   You may need to re-authorize:")
        print("   python3 threads-oauth-setup.py")
        return None

    data = r.json()
    return data


if __name__ == "__main__":
    force = "--force" in sys.argv

    data = load_token()
    app_secret = load_app_secret()

    if not app_secret:
        print("❌ No app_secret in config")
        sys.exit(1)

    expires_at = data.get("expires_at", 0)
    now = int(time.time())
    days_left = (expires_at - now) / 86400

    print(f"Token status:")
    print(f"  User:      @{data.get('username', 'unknown')}")
    print(f"  Expires:   {time.ctime(expires_at)}")
    print(f"  Days left: {days_left:.1f}")

    if days_left < 0:
        print()
        print("❌ Token EXPIRED. Re-authorization needed.")
        print("   python3 threads-oauth-setup.py")
        sys.exit(1)

    if not force and days_left > REFRESH_THRESHOLD_DAYS:
        print()
        print(f"✅ Token still valid ({days_left:.1f} days left). No refresh needed.")
        print(f"   Use --force to refresh anyway.")
        sys.exit(0)

    print()
    result = refresh_token(data["access_token"], app_secret)

    if result:
        data["access_token"] = result["access_token"]
        data["expires_at"] = int(time.time()) + result.get("expires_in", 60 * 86400)
        data["refreshed_at"] = int(time.time())
        save_token(data)
        new_days = result.get("expires_in", 60 * 86400) / 86400
        print(f"✅ Token refreshed! New expiry: {new_days:.0f} days")
    else:
        sys.exit(1)
