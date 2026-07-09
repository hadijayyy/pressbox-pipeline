#!/bin/bash
# Hourly Pressbox report → @szejay_bot
set -a; source ~/.hermes/.env 2>/dev/null; set +a
TOKEN_FILE="$HOME/.szejay_token"
CHAT="1022032312"
POSTED="$HOME/.hermes/pressbox/posted_topics.json"
STATUS="/tmp/pressbox-last-post"

[ -z "$SZEJAY_BOT_TOKEN" ] && [ -f "$TOKEN_FILE" ] && SZEJAY_BOT_TOKEN=$(cat "$TOKEN_FILE")
[ -z "$SZEJAY_BOT_TOKEN" ] && exit 0

NOW_WIB=$(TZ=Asia/Jakarta date '+%H:%M WIB, %d %b')

# Last post info
LAST_POST="N/A"
if [ -f "$STATUS" ]; then
    LAST_POST=$(cat "$STATUS" | sed 's/ok //' | xargs -I{} TZ=Asia/Jakarta date -d {} '+%H:%M WIB %d %b' 2>/dev/null || echo "$(cat "$STATUS")")
fi

# Engagement stats from posted_topics.json
STATS=$(python3 -c "
import json, sys
from datetime import datetime, timezone, timedelta
WIB = timezone(timedelta(hours=7))
try:
    with open('$POSTED') as f: data = json.load(f)
    topics = data.get('topics', [])
    total = len(topics)
    with_views = [t for t in topics if t.get('views') is not None]
    today = datetime.now(WIB).date()
    today_posts = [t for t in topics if t.get('posted_at','')[:10] == str(today)]
    
    total_views = sum(t.get('views', 0) for t in with_views)
    total_likes = sum(t.get('likes', 0) for t in with_views)
    total_replies = sum(t.get('replies', 0) for t in with_views)
    
    # Best post today
    best_today = max(today_posts, key=lambda t: t.get('views', 0)) if today_posts else None
    
    print(f'📊 Total posts: {total}')
    print(f'📅 Today: {len(today_posts)} posts')
    print(f'👁️ Total views: {total_views:,}')
    print(f'❤️ Total likes: {total_likes:,}')
    print(f'💬 Total replies: {total_replies:,}')
    if best_today:
        print(f'🏆 Best today: {best_today[\"title\"][:50]}')
        print(f'   {best_today.get(\"views\",0):,} views')
    print(f'🕐 Last post: $LAST_POST')
except Exception as e:
    print(f'⚠️ Stats error: {e}')
" 2>&1)

MSG="📊 <b>Pressbox Hourly Report</b>

⏰ $NOW_WIB

$STATS"

curl -s -X POST "https://api.telegram.org/bot${SZEJAY_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=$CHAT" \
    --data-urlencode "text=$MSG" \
    --data-urlencode "parse_mode=HTML" \
    -d "disable_web_page_preview=true" > /dev/null 2>&1
