#!/usr/local/bin/python3
"""Send a message to a Telegram topic via Bot API. --summary for compact format."""
import os, sys, json, re, urllib.request

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

def extract_summary(raw):
    lines = raw.strip().splitlines()
    title = ""
    permalink = ""
    status = "\u2705"
    stats = ""
    for line in lines:
        ls = line.strip()
        m = re.search(r"\u2705 (.+)", ls)
        if m and "succeeded" not in ls and "Chain" not in ls and "Last slide" not in ls and "Pipeline done" not in ls and "tracker" not in ls.lower():
            c = m.group(1).strip()
            if len(c) > 10 and "HTTP" not in c:
                title = c
        m = re.search(r"(https://www\.threads\.com/\S+)", ls)
        if m:
            permalink = m.group(1)
        m = re.search(r"Pipeline done: (.+)", ls)
        if m:
            title = m.group(1).strip()
        if "\u274c" in ls or "failed" in ls.lower():
            status = "\u274c"
        m = re.search(r"succeeded in (\d+)s", ls)
        if m:
            stats = f"\u23f1\ufe0f {m.group(1)}s"
    parts = []
    if title:
        parts.append(f"{status} {title[:80]}")
    if permalink:
        parts.append(f"\U0001f517 {permalink}")
    if stats:
        parts.append(stats)
    if not parts:
        for l in reversed(lines):
            if l.strip():
                parts.append(l.strip()[:100])
                if len(parts) >= 3:
                    break
        parts.reverse()
    return "\n".join(parts)

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
    use_summary = "--summary" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--summary"]
    text = args[0] if args else sys.stdin.read().strip()
    if not text:
        sys.exit(0)
    if use_summary:
        text = extract_summary(text)
    if text:
        sys.exit(0 if send(text) else 1)
    sys.exit(0)
