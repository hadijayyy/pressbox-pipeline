#!/usr/local/bin/python3
"""Threads OAuth Setup — one-time flow to get a long-lived access token.

Usage:
    python3 threads-oauth-setup.py

Steps:
    1. Opens browser for Threads OAuth authorization
    2. You login and authorize the app
    3. Copy the 'code' from the redirect URL
    4. Script exchanges it for a short-lived token
    5. Script exchanges it for a long-lived token (~60 days)
    6. Saves to ~/.hermes/threads_token.json
"""

import json, os, sys, time
from pathlib import Path
from urllib.parse import urlencode

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

TOKEN_FILE = Path.home() / ".hermes" / "threads_token.json"
CONFIG_FILE = Path.home() / ".hermes" / "threads_app.json"

THREADS_API = "https://graph.threads.net/v1.0"
THREADS_OAUTH = "https://threads.net/oauth/authorize"
REDIRECT_URI = "https://localhost"  # Must match your app's valid OAuth redirect URI

REQUIRED_SCOPES = [
    "threads_basic",
    "threads_content_publish",
    "threads_manage_replies",
    "threads_manage_insights",
]


def load_app_config():
    """Load app_id and app_secret from config file."""
    if not CONFIG_FILE.exists():
        print(f"❌ Config not found: {CONFIG_FILE}")
        print()
        print("Create it with:")
        print(f"  mkdir -p ~/.hermes")
        print(f"  cat > {CONFIG_FILE} << 'EOF'")
        print(f'  {{"app_id": "YOUR_APP_ID", "app_secret": "YOUR_APP_SECRET"}}')
        print(f"  EOF")
        print()
        print("Get these from: https://developers.facebook.com/apps/")
        print("  → App Settings → Basic → Threads App ID / App Secret")
        sys.exit(1)

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    if "app_id" not in config or "app_secret" not in config:
        print(f"❌ Invalid config: {CONFIG_FILE}")
        print("Must contain: app_id, app_secret")
        sys.exit(1)

    return config["app_id"], config["app_secret"]


def step1_authorize(app_id):
    """Open browser for OAuth authorization."""
    params = {
        "client_id": app_id,
        "redirect_uri": REDIRECT_URI,
        "scope": ",".join(REQUIRED_SCOPES),
        "response_type": "code",
    }
    url = f"{THREADS_OAUTH}?{urlencode(params)}"

    print("=" * 60)
    print("STEP 1: Authorize your app")
    print("=" * 60)
    print()
    print("Open this URL in your browser:")
    print()
    print(f"  {url}")
    print()
    print("After authorizing, you'll be redirected to:")
    print(f"  {REDIRECT_URI}?code=AQBx...")
    print()
    print("Copy the FULL URL from your browser address bar.")
    print()

    # Try to open browser automatically
    try:
        import webbrowser
        webbrowser.open(url)
        print("(Browser opened automatically)")
    except Exception:
        pass

    raw = input("Paste the redirect URL (or just the code): ").strip()
    if not raw:
        print("❌ No input provided")
        sys.exit(1)

    # Extract code from URL or use as-is
    if "code=" in raw:
        code = raw.split("code=")[1].split("&")[0].split("#")[0]
    else:
        code = raw.rstrip("#_")

    return code


def step2_exchange_code(app_id, app_secret, code):
    """Exchange authorization code for short-lived token."""
    print()
    print("⏳ Exchanging code for token...")

    r = requests.post(f"{THREADS_API}/oauth/access_token", data={
        "client_id": app_id,
        "client_secret": app_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    })

    if r.status_code != 200:
        print(f"❌ Token exchange failed: {r.status_code}")
        print(r.text)
        sys.exit(1)

    data = r.json()
    print(f"✅ Short-lived token obtained (expires in ~1 hour)")
    return data["access_token"]


def step3_exchange_for_long_token(app_id, app_secret, short_token):
    """Exchange short-lived token for long-lived token (~60 days)."""
    print()
    print("⏳ Exchanging for long-lived token...")

    # Threads-specific long-lived token exchange
    r = requests.get(f"{THREADS_API}/access_token", params={
        "grant_type": "th_exchange_token",
        "client_secret": app_secret,
        "access_token": short_token,
    })

    if r.status_code != 200:
        print(f"⚠️  Long-lived exchange failed ({r.status_code})")
        print(f"   Response: {r.text}")
        print()
        print("   Using short-lived token (expires in ~1 hour).")
        print("   Re-run this script to get a new one.")
        return short_token, 3600

    data = r.json()
    expires_in = data.get("expires_in", 60 * 86400)
    print(f"✅ Long-lived token obtained (expires in {expires_in // 86400} days)")
    return data["access_token"], expires_in


def step4_get_user_id(token):
    """Get the Threads user ID."""
    print()
    print("⏳ Getting user ID...")

    r = requests.get(f"{THREADS_API}/me", params={
        "fields": "id,username",
        "access_token": token,
    })

    if r.status_code != 200:
        print(f"⚠️  Could not get user ID: {r.status_code}")
        print(f"   Response: {r.text}")
        return None, None

    data = r.json()
    user_id = data.get("id")
    username = data.get("username")
    print(f"✅ User: @{username} (ID: {user_id})")
    return user_id, username


def save_token(token, user_id, username, expires_in):
    """Save token to file."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "access_token": token,
        "user_id": str(user_id),
        "username": username,
        "expires_at": int(time.time()) + expires_in,
        "created_at": int(time.time()),
    }

    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)

    # Secure the file
    os.chmod(TOKEN_FILE, 0o600)

    print()
    print(f"💾 Token saved to: {TOKEN_FILE}")


if __name__ == "__main__":
    print("🔑 Threads OAuth Setup")
    print("=" * 60)
    print()

    app_id, app_secret = load_app_config()
    print(f"App ID: {app_id}")
    print()

    code = step1_authorize(app_id)
    short_token = step2_exchange_code(app_id, app_secret, code)
    token, expires_in = step3_exchange_for_long_token(app_id, app_secret, short_token)
    user_id, username = step4_get_user_id(token)

    if user_id:
        save_token(token, user_id, username, expires_in)

    print()
    print("=" * 60)
    print("✅ Setup complete!")
    print()
    print("Test with:")
    print("  python3 threads-post.py 'Hello from Threads API!'")
    print()
    print(f"Token expires in ~{expires_in // 86400} days.")
    print("Re-run this script to refresh when needed.")
