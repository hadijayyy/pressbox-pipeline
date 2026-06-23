#!/usr/bin/env python3
"""Send a message to a Telegram topic via Bot API."""
import os, sys, json, urllib.request

def load_token():
    env_path = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("TELEGRAM_BOT_TOKEN=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")

BOT_TOKEN = load_token()
CHAT_ID = "1022032312"
TOPIC_ID = 20467

def send(text):
    if not BOT_TOKEN:
        print("no token", file=sys.stderr)
        return False
    url = "https://api.telegram.org/bot{}/sendMessage".format(BOT_TOKEN)
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": text[:4000],
        "message_thread_id": TOPIC_ID,
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read()).get("ok", False)

if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read().strip()
    if text:
        sys.exit(0 if send(text) else 1)
    sys.exit(0)
