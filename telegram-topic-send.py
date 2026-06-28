#!/usr/local/bin/python3
"""Send a message to a Telegram topic via Bot API. --summary for compact table format."""
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

def _detect_type(raw):
    """Detect output type: pipeline, post, or generic."""
    if "Pipeline done:" in raw or "DRY RUN" in raw or "Scrape:" in raw:
        return "pipeline"
    if "[FATAL]" in raw or "Cannot load pressbox" in raw:
        return "pipeline"
    if "PRESS BOX POST" in raw or "Posting to Threads" in raw:
        return "post"
    if "Circuit" in raw and ("succeeded" in raw or "failed" in raw):
        return "cb"
    return "generic"

def _format_pipeline(raw):
    """Format pipeline output as clean table."""
    lines = raw.strip().splitlines()
    status = "✅"
    title = ""
    source = ""
    score = ""
    slides = ""
    model = ""
    tokens_total = ""
    timing = ""
    stg_status = ""
    url = ""

    for line in lines:
        ls = line.strip()
        # Title from "Pipeline done: X (N slides, N tokens, Ns)"
        m = re.search(r"Pipeline done: (.+?)(?:\s*\(|$)", ls)
        if m:
            title = m.group(1).strip()
        # Title from "✅ X (N slides) [type]"
        m = re.search(r"✅ (.+?)\s*\(\d+ slides\)", ls)
        if m and not title:
            title = m.group(1).strip()
        # DRY RUN title
        m = re.search(r"DRY RUN — (.+?)\s*\(", ls)
        if m:
            title = m.group(1).strip()
        # Score
        m = re.search(r"score=(\d+)", ls)
        if m:
            score = m.group(1)
        # Source
        m = re.search(r"\(score=\d+\)", ls)
        if m:
            # Get source from topic type line
            pass
        m = re.search(r"Topic type: (\w+)", ls)
        if m:
            source = m.group(1).replace("_", " ").title()
        # Slides
        m = re.search(r"(\d+) slides", ls)
        if m:
            slides = m.group(1)
        # Model
        m = re.search(r"Chain: (.+?)$", ls)
        if m:
            model = m.group(1).strip()
        m = re.search(r"LLM attempt \d+/\d+ \((.+?) via", ls)
        if m and not model:
            model = m.group(1).strip()
        # Tokens
        m = re.search(r"Tokens: prompt=(\d+) completion=(\d+) total=(\d+)", ls)
        if m:
            tokens_total = m.group(3)
        # Timing
        m = re.search(r"Scrape:([\d.]+)s\s+LLM:([\d.]+)s\s+Total:([\d.]+)s", ls)
        if m:
            timing = f"{m.group(3)}s (scrape {m.group(1)}s + LLM {m.group(2)}s)"
        # Staging
        m = re.search(r"Staging (ready|unposted)", ls)
        if m:
            stg_status = m.group(1)
        # Errors
        if "❌" in ls or "failed" in ls.lower() or "[FATAL]" in ls:
            status = "❌"
        # Skip messages
        if "skip" in ls.lower() and "unposted" in ls.lower():
            stg_status = "unposted (skip)"
        if "new topic" in ls.lower() and "found" in ls.lower():
            stg_status = "no new topics"

    # Build table
    rows = []
    rows.append(f"{status} **Pipeline**")
    if title:
        rows.append(f"📰 {title[:80]}")
    if source:
        rows.append(f"📂 {source}")
    if score:
        rows.append(f"⭐ Score: {score}")
    if slides:
        rows.append(f"📊 Slides: {slides}")
    if model:
        rows.append(f"🤖 Model: {model}")
    if tokens_total:
        rows.append(f"🎯 Tokens: {tokens_total}")
    if timing:
        rows.append(f"⏱️ {timing}")
    if stg_status:
        rows.append(f"📦 Staging: {stg_status}")
    if status == "❌":
        # Extract error line
        for line in lines:
            if "❌" in line or "[FATAL]" in line or "failed" in line.lower():
                rows.append(f"💥 {line.strip()[:100]}")
                break
    return "\n".join(rows)

def _format_post(raw):
    """Format post output as clean table."""
    lines = raw.strip().splitlines()
    status = "✅"
    title = ""
    url = ""
    post_id = ""
    cooldown = ""
    timing = ""

    for line in lines:
        ls = line.strip()
        # Title from staging loaded
        m = re.search(r"Staging loaded: (.+?)\s*\(", ls)
        if m:
            title = m.group(1).strip()
        # Title from success
        m = re.search(r"✅ (.+)", ls)
        if m and not title:
            c = m.group(1).strip()
            if "Chain" not in c and "Last slide" not in c and "tracker" not in c:
                title = c[:80]
        # URL
        m = re.search(r"(https://www\.threads\.com/\S+)", ls)
        if m:
            url = m.group(1)
        # Cooldown
        if "skip" in ls.lower() or "cooldown" in ls.lower() or "menit" in ls.lower():
            cooldown = ls.strip().lstrip("⏸️ ").strip()
        # Errors
        if "❌" in ls or "failed" in ls.lower():
            status = "❌"
        # Chain verified
        if "Chain structure OK" in ls:
            post_id = "chain verified"
        if "Last slide verified OK" in ls:
            post_id = "slides verified"

    # Build table
    rows = []
    rows.append(f"{status} **Post**")
    if title:
        rows.append(f"📰 {title[:80]}")
    if url:
        rows.append(f"🔗 {url}")
    if post_id:
        rows.append(f"🔍 {post_id}")
    if cooldown:
        rows.append(f"⏸️ {cooldown[:80]}")
    if status == "❌":
        for line in lines:
            if "❌" in line or "failed" in line.lower() or "error" in line.lower():
                rows.append(f"💥 {line.strip()[:100]}")
                break
    return "\n".join(rows)

def _format_cb(raw):
    """Format circuit breaker wrapper output."""
    lines = raw.strip().splitlines()
    status = "✅"
    job_id = ""
    duration = ""
    cb_state = ""

    for line in lines:
        ls = line.strip()
        m = re.search(r"succeeded in (\d+)s", ls)
        if m:
            duration = f"{m.group(1)}s"
        m = re.search(r"failed: (.+)", ls)
        if m:
            status = "❌"
            duration = m.group(1)
        m = re.search(r"Circuit (ALLOW|OPEN|HALF_OPEN): (\w+)", ls)
        if m:
            cb_state = m.group(2)
        if "Circuit breaker OPEN" in ls:
            status = "⛔"
    return f"{status} CB: {cb_state} | {duration}"

def extract_summary(raw):
    """Detect output type and format as clean table report."""
    dtype = _detect_type(raw)
    if dtype == "pipeline":
        return _format_pipeline(raw)
    elif dtype == "post":
        return _format_post(raw)
    elif dtype == "cb":
        return _format_cb(raw)
    # Generic fallback: last 3 non-empty lines
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    return "\n".join(lines[-3:])

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
