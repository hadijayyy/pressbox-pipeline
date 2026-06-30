#!/bin/bash
# Pressbox Engagement Report → @Szejay_bot
set -a; source ~/.hermes/.env; set +a
BOT_TOKEN="${SZEJAY_BOT_TOKEN}"
CHAT_ID="1022032312"
[ -z "$BOT_TOKEN" ] && echo "Missing SZEJAY_BOT_TOKEN" && exit 1

cd ~/.hermes/pressbox-pipeline

REPORT=$(python3 << 'PYEOF'
import json, os, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

WIB = timezone(timedelta(hours=7))
POSTED = os.path.expanduser("~/.hermes/pressbox/posted_topics.json")
data = json.load(open(POSTED))
topics = data.get("topics", [])
now = time.time()

h24 = [t for t in topics if t.get("posted_at") and 
       (now - datetime.fromisoformat(t["posted_at"]).timestamp()) < 86400]
with_v = [t for t in h24 if t.get("views") is not None]
pending = [t for t in h24 if t.get("views") is None]
views = sorted([t.get("views", 0) for t in with_v], reverse=True)
med = views[len(views)//2] if views else 0
avg = sum(views)/len(views) if views else 0

def classify(tl):
    if any(w in tl for w in ["slams","blasts","scandal","controversy","row","rift"]): return "controversy"
    if any(w in tl for w in ["vs","against","clash","rival","battle"]): return "conflict"
    if any(w in tl for w in ["?","how","why","what if","can","will"]): return "curiosity"
    if any(w in tl for w in ["just","dropped","lost","won","banned","sacked"]): return "event"
    return "statement"

by_hook = defaultdict(list)
for t in with_v:
    hook = classify((t.get("title") or "").lower())
    by_hook[hook].append(t.get("views", 0))

hook_lines = []
for h, vals in sorted(by_hook.items(), key=lambda x: sum(x[1])/len(x[1]) if x[1] else 0, reverse=True):
    hook_lines.append(f"  {h}: avg {sum(vals)//len(vals):,} ({len(vals)} posts)")

top3 = sorted(with_v, key=lambda x: x.get("views",0), reverse=True)[:3]
bot3 = sorted(with_v, key=lambda x: x.get("views",0))[:3]
top_lines = [f"  🏆 {t.get('views',0):,} - {t.get('title','?')[:50]}" for t in top3]
bot_lines = [f"  💩 {t.get('views',0):,} - {t.get('title','?')[:50]}" for t in bot3]

ab_status = 'active' if os.path.exists('/tmp/pressbox-last-post') else 'not triggered'

print(f"📊 Pressbox Daily Report")
print(f"━━━━━━━━━━━━━━━━━━━━━━")
print(f"Posts (24h): {len(h24)} ({len(with_v)} measured, {len(pending)} pending)")
print(f"Median:  {med:,}")
print(f"Average: {avg:,.0f}")
print(f"Range:   {min(views) if views else 0:,} - {max(views) if views else 0:,}")
print()
print("Best hooks:")
for l in hook_lines: print(l)
print()
print("Top posts:")
for l in top_lines: print(l)
print()
print("Worst posts:")
for l in bot_lines: print(l)
print()
print(f"Anti-bot: {ab_status}")
print(f"⏰ {datetime.now(WIB).strftime('%H:%M WIB, %d %b %Y')}")
PYEOF
)

# Send (escape for Telegram)
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=${REPORT}" > /dev/null 2>&1

echo "✅ Sent to @Szejay_bot"
