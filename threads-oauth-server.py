#!/usr/bin/env python3
"""Threads OAuth Server — Buffer-like auto-flow.

Usage:
    python3 threads-oauth-server.py              # Start server + tunnel
    python3 threads-oauth-server.py --no-tunnel  # Start server only (localhost)

Flow:
    1. Server starts on localhost:5123
    2. cloudflared tunnel provides public URL
    3. User opens tunnel URL → sees "Connect Threads" button
    4. Click → redirect to Meta OAuth → authorize
    5. Meta redirects back to /callback with code
    6. Server auto-exchanges code → short token → long token
    7. Token saved to ~/.hermes/threads_token.json
    8. Done! Pipeline can use the token automatically.
"""

import json
import os
import sys
import time
import threading
import subprocess
import signal
from pathlib import Path
from urllib.parse import urlencode

import requests
from flask import Flask, request, redirect, jsonify, render_template_string

# ── Config ──────────────────────────────────────────────────────────
TOKEN_FILE = Path.home() / ".hermes" / "threads_token.json"
CONFIG_FILE = Path.home() / ".hermes" / "threads_app.json"
PORT = 5123
THREADS_API = "https://graph.threads.net/v1.0"
THREADS_OAUTH = "https://threads.net/oauth/authorize"

REQUIRED_SCOPES = [
    "threads_basic",
    "threads_content_publish",
    "threads_manage_replies",
    "threads_manage_insights",
]

# ── HTML Template ───────────────────────────────────────────────────
INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Threads OAuth — Connect Account</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #101010;
            color: #f5f5f5;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .card {
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 16px;
            padding: 48px;
            max-width: 440px;
            width: 90%;
            text-align: center;
        }
        .logo { font-size: 48px; margin-bottom: 16px; }
        h1 { font-size: 24px; margin-bottom: 8px; }
        .subtitle { color: #999; margin-bottom: 32px; font-size: 14px; }
        .connect-btn {
            display: inline-block;
            background: linear-gradient(135deg, #833AB4, #FD1D1D, #FCAF45);
            color: white;
            text-decoration: none;
            padding: 14px 32px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 16px;
            transition: opacity 0.2s;
        }
        .connect-btn:hover { opacity: 0.9; }
        .scopes {
            margin-top: 24px;
            text-align: left;
            font-size: 12px;
            color: #666;
        }
        .scopes li { margin: 4px 0; list-style: none; }
        .scopes li::before { content: "✓ "; color: #4CAF50; }
        .success {
            background: #1a2e1a;
            border: 1px solid #2d5a2d;
            border-radius: 12px;
            padding: 24px;
            margin-top: 24px;
        }
        .success h2 { color: #4CAF50; margin-bottom: 8px; }
        .token-info { font-size: 13px; color: #999; margin-top: 12px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">🔗</div>
        <h1>Connect Threads</h1>
        <p class="subtitle">Authorize access to post from your account</p>

        {% if connected %}
        <div class="success">
            <h2>✅ Connected!</h2>
            <p>@{{ username }} (ID: {{ user_id }})</p>
            <p class="token-info">Token expires in {{ expires_days }} days</p>
        </div>
        {% else %}
        <a href="{{ oauth_url }}" class="connect-btn">
            Login with Threads
        </a>
        <ul class="scopes">
            <li>Post text &amp; images</li>
            <li>Manage replies</li>
            <li>View insights</li>
        </ul>
        {% endif %}
    </div>
</body>
</html>
"""

SUCCESS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Threads — Connected!</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #101010;
            color: #f5f5f5;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .card {
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 16px;
            padding: 48px;
            max-width: 440px;
            width: 90%;
            text-align: center;
        }
        .check { font-size: 64px; margin-bottom: 16px; }
        h1 { color: #4CAF50; margin-bottom: 12px; }
        .info { color: #999; font-size: 14px; line-height: 1.6; }
        .token-info {
            margin-top: 20px;
            padding: 16px;
            background: #222;
            border-radius: 8px;
            font-size: 13px;
            text-align: left;
        }
        .token-info span { color: #4CAF50; }
    </style>
</head>
<body>
    <div class="card">
        <div class="check">✅</div>
        <h1>Connected!</h1>
        <p class="info">Threads account linked successfully.</p>
        <div class="token-info">
            <div>👤 <span>@{{ username }}</span></div>
            <div>🆔 <span>{{ user_id }}</span></div>
            <div>⏰ Token expires in <span>{{ expires_days }} days</span></div>
            <div>📁 Saved to <span>{{ token_file }}</span></div>
        </div>
        <p class="info" style="margin-top: 20px;">You can close this tab. Pipeline is ready to post!</p>
    </div>
</body>
</html>
"""

ERROR_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Threads — Error</title>
    <style>
        body {
            font-family: sans-serif;
            background: #101010;
            color: #f5f5f5;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }
        .card {
            background: #1a1a1a;
            border: 1px solid #5a2d2d;
            border-radius: 16px;
            padding: 48px;
            max-width: 440px;
            text-align: center;
        }
        .error { color: #f44336; font-size: 48px; margin-bottom: 16px; }
        h1 { color: #f44336; }
        .detail { color: #999; margin-top: 12px; font-size: 14px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="error">❌</div>
        <h1>Authorization Failed</h1>
        <p class="detail">{{ error }}</p>
        <p class="detail" style="margin-top: 20px;">
            <a href="/" style="color: #833AB4;">Try again</a>
        </p>
    </div>
</body>
</html>
"""


# ── Helpers ─────────────────────────────────────────────────────────
def load_app_config():
    if not CONFIG_FILE.exists():
        print(f"❌ Config not found: {CONFIG_FILE}")
        print(f"   Create with: {{\"app_id\": \"...\", \"app_secret\": \"...\"}}")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    return config["app_id"], config["app_secret"]


def exchange_code_for_token(app_id, app_secret, code, redirect_uri):
    """Exchange authorization code → short-lived → long-lived token."""
    # Step 1: code → short-lived
    r = requests.post(f"{THREADS_API}/oauth/access_token", data={
        "client_id": app_id,
        "client_secret": app_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    if r.status_code != 200:
        raise Exception(f"Short-lived token exchange failed: {r.status_code} {r.text}")

    short_token = r.json()["access_token"]
    print(f"  ✅ Short-lived token obtained")

    # Step 2: short-lived → long-lived
    r = requests.get(f"{THREADS_API}/access_token", params={
        "grant_type": "th_exchange_token",
        "client_secret": app_secret,
        "access_token": short_token,
    })

    if r.status_code == 200:
        data = r.json()
        token = data["access_token"]
        expires_in = data.get("expires_in", 60 * 86400)
        print(f"  ✅ Long-lived token ({expires_in // 86400} days)")
    else:
        print(f"  ⚠️  Long-lived exchange failed, using short-lived")
        token = short_token
        expires_in = 3600

    # Step 3: get user info
    r = requests.get(f"{THREADS_API}/me", params={
        "fields": "id,username",
        "access_token": token,
    })
    user_id = None
    username = None
    if r.status_code == 200:
        me = r.json()
        user_id = me.get("id")
        username = me.get("username")
        print(f"  ✅ User: @{username} (ID: {user_id})")

    return token, expires_in, user_id, username


def save_token(token, user_id, username, expires_in):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "access_token": token,
        "user_id": str(user_id) if user_id else None,
        "username": username,
        "expires_at": int(time.time()) + expires_in,
        "created_at": int(time.time()),
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)
    print(f"  💾 Token saved: {TOKEN_FILE}")


# ── Flask App ───────────────────────────────────────────────────────
def create_app(app_id, app_secret, redirect_uri):
    app = Flask(__name__)

    @app.route("/")
    def index():
        # Check if token already exists and is valid
        connected = False
        username = None
        user_id = None
        expires_days = 0

        if TOKEN_FILE.exists():
            with open(TOKEN_FILE) as f:
                tdata = json.load(f)
            if tdata.get("expires_at", 0) > time.time():
                connected = True
                username = tdata.get("username", "?")
                user_id = tdata.get("user_id", "?")
                expires_days = int((tdata["expires_at"] - time.time()) / 86400)

        params = {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "scope": ",".join(REQUIRED_SCOPES),
            "response_type": "code",
        }
        oauth_url = f"{THREADS_OAUTH}?{urlencode(params)}"

        return render_template_string(
            INDEX_HTML,
            oauth_url=oauth_url,
            connected=connected,
            username=username,
            user_id=user_id,
            expires_days=expires_days,
        )

    @app.route("/callback")
    def callback():
        code = request.args.get("code")
        error = request.args.get("error")

        if error:
            return render_template_string(ERROR_HTML, error=error)

        if not code:
            return render_template_string(ERROR_HTML, error="No authorization code received")

        try:
            print(f"\n{'='*50}")
            print(f"📥 Received authorization code")
            token, expires_in, user_id, username = exchange_code_for_token(
                app_id, app_secret, code, redirect_uri
            )
            save_token(token, user_id, username, expires_in)

            expires_days = expires_in // 86400
            return render_template_string(
                SUCCESS_HTML,
                username=username or "unknown",
                user_id=user_id or "unknown",
                expires_days=expires_days,
                token_file=str(TOKEN_FILE),
            )
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return render_template_string(ERROR_HTML, error=str(e))

    @app.route("/api/status")
    def api_status():
        """Quick API endpoint to check token status."""
        if not TOKEN_FILE.exists():
            return jsonify({"connected": False, "error": "No token file"})
        with open(TOKEN_FILE) as f:
            tdata = json.load(f)
        expires_at = tdata.get("expires_at", 0)
        return jsonify({
            "connected": expires_at > time.time(),
            "username": tdata.get("username"),
            "user_id": tdata.get("user_id"),
            "expires_at": expires_at,
            "days_left": max(0, int((expires_at - time.time()) / 86400)),
        })

    return app


# ── Tunnel ──────────────────────────────────────────────────────────
def start_tunnel(port):
    """Start cloudflared tunnel and return public URL."""
    print(f"🚇 Starting cloudflared tunnel → localhost:{port}...")

    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # cloudflared prints URL to stderr (merged into stdout via STDOUT)
    public_url = None
    import select
    start = time.time()
    while time.time() - start < 30:  # 30s timeout
        if proc.poll() is not None:
            break
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.1)
            continue
        if "trycloudflare.com" in line:
            for word in line.split():
                if "trycloudflare.com" in word:
                    public_url = word.strip().rstrip(".")
                    break
        if public_url:
            break

    return public_url, proc


# ── Main ────────────────────────────────────────────────────────────
def main():
    no_tunnel = "--no-tunnel" in sys.argv

    print("🔑 Threads OAuth Server")
    print("=" * 50)

    app_id, app_secret = load_app_config()
    print(f"App ID: {app_id}")

    # Start tunnel
    tunnel_proc = None
    if no_tunnel:
        redirect_uri = f"http://localhost:{PORT}"
        public_url = f"http://localhost:{PORT}"
        print(f"\n🌐 Running on: {public_url}")
    else:
        public_url, tunnel_proc = start_tunnel(PORT)
        if not public_url:
            print("❌ Tunnel failed. Use --no-tunnel for localhost only.")
            sys.exit(1)
        redirect_uri = public_url
        print(f"\n🌐 Tunnel URL: {public_url}")

    print(f"\n📋 Callback URL (set in Meta App Dashboard):")
    print(f"   {redirect_uri}")
    print(f"\n⚡ Open in browser:")
    print(f"   {public_url}")
    print(f"\n   Or Ctrl+C to stop.\n")

    app = create_app(app_id, app_secret, redirect_uri)

    try:
        app.run(host="0.0.0.0", port=PORT, debug=False)
    except KeyboardInterrupt:
        print("\n\n🛑 Server stopped.")
    finally:
        if tunnel_proc:
            tunnel_proc.terminate()


if __name__ == "__main__":
    main()
