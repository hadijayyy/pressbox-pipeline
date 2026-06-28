#!/usr/local/bin/python3
"""Press Box Weekly Analytics — Deep Pattern Analysis (Fast Version)."""

import subprocess, sys
for _pkg, _mod in [("httpx","httpx"),("beautifulsoup4","bs4"),("requests","requests"),("python-dotenv","dotenv")]:
    try: __import__(_mod)
    except ImportError: subprocess.check_call([sys.executable,"-m","pip","install","--quiet","--root-user-action=ignore",_pkg],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

import json, os, httpx, re
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict, Counter

TOKEN_PATH = os.path.expanduser("~/.hermes/threads_token.json")
FEEDBACK_PATH = os.path.expanduser("~/.hermes/pressbox/analytics_weekly.json")
DAILY_FEEDBACK = os.path.expanduser("~/.hermes/pressbox/analytics_feedback.json")
WIB = timezone(timedelta(hours=7))

def get_token():
    with open(TOKEN_PATH) as f:
        data = json.load(f)
    return data["access_token"], data["user_id"]

def fetch_posts(tok, uid, limit=100):
    """Fetch posts with pagination (API max 50/request)."""
    all_posts = []
    after = None
    page_size = 50
    while len(all_posts) < limit:
        params = {"access_token": tok, "fields": "id,text,timestamp", "limit": min(page_size, limit - len(all_posts))}
        if after:
            params["after"] = after
        r = httpx.get(f"https://graph.threads.net/v1.0/{uid}/threads", params=params, timeout=15)
        resp = r.json()
        data = resp.get("data", [])
        if not data:
            break
        all_posts.extend(data)
        after = resp.get("paging", {}).get("cursors", {}).get("after")
        if not after:
            break
    return all_posts[:limit]

def fetch_engagement(tok, post_id):
    try:
        r = httpx.get(f"https://graph.threads.net/v1.0/{post_id}/insights",
                      params={"access_token": tok, "metric": "likes,replies,reposts,views,quotes", "period": "lifetime"},
                      timeout=10)
        m = {"likes": 0, "replies": 0, "reposts": 0, "views": 0, "quotes": 0}
        for x in r.json().get("data", []):
            m[x["name"]] = x["values"][0]["value"]
        return m
    except Exception:
        return {"likes": 0, "replies": 0, "reposts": 0, "views": 0, "quotes": 0}

def calc_score(m):
    return m["likes"] + m["replies"] * 3 + m["reposts"] * 2 + m["quotes"] * 2

def extract_hook(text):
    return text.strip().split('\n')[0].strip() if text else ''

def analyze_hooks(enriched):
    """Analyze hook patterns."""
    drama_words = ['scandal', 'chaos', 'banned', 'hero', 'shocking', 'outrage', 'fury', 'drama', 'crisis']
    
    results = {"drama": [], "non_drama": [], "short": [], "long": [], "question": [], "statement": []}
    
    for p in enriched:
        hook = extract_hook(p["text"]).lower()
        words = hook.split()
        score = p["score"]
        
        has_drama = any(w in drama_words for w in words)
        is_short = len(words) <= 8
        is_question = hook.endswith('?')
        
        if has_drama: results["drama"].append(score)
        else: results["non_drama"].append(score)
        
        if is_short: results["short"].append(score)
        else: results["long"].append(score)
        
        if is_question: results["question"].append(score)
        else: results["statement"].append(score)
    
    def avg(lst): return sum(lst) / max(len(lst), 1)
    
    return {
        "drama_avg": round(avg(results["drama"]), 1),
        "non_drama_avg": round(avg(results["non_drama"]), 1),
        "short_avg": round(avg(results["short"]), 1),
        "long_avg": round(avg(results["long"]), 1),
        "question_avg": round(avg(results["question"]), 1),
        "statement_avg": round(avg(results["statement"]), 1),
        "recommendation": {
            "use_drama": avg(results["drama"]) > avg(results["non_drama"]),
            "prefer_short": avg(results["short"]) > avg(results["long"]),
            "prefer_statements": avg(results["statement"]) > avg(results["question"]),
        }
    }

def main():
    tok, uid = get_token()
    print("📊 Weekly Analytics...")
    
    raw = fetch_posts(tok, uid, limit=100)
    print(f"📊 {len(raw)} posts fetched")
    
    enriched = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_engagement, tok, p["id"]): p for p in raw}
        for f in as_completed(futs):
            p = futs[f]
            m = f.result()
            enriched.append({
                "text": p.get("text", ""),
                "score": calc_score(m),
                "metrics": m,
            })
    
    enriched.sort(key=lambda x: x["score"], reverse=True)
    overall_avg = sum(p["score"] for p in enriched) / max(len(enriched), 1)
    
    # Analyze hooks
    hook_analysis = analyze_hooks(enriched)
    
    # Build feedback
    feedback = {
        "generated_at": datetime.now(WIB).isoformat(),
        "total_posts": len(enriched),
        "overall_avg_score": round(overall_avg, 1),
        "hook_analysis": hook_analysis,
        "top_hooks": [{"hook": extract_hook(p["text"]), "score": p["score"]} for p in enriched[:5]],
    }
    
    os.makedirs(os.path.dirname(FEEDBACK_PATH), exist_ok=True)
    tmp_path = FEEDBACK_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(feedback, f, indent=2)
    os.replace(tmp_path, FEEDBACK_PATH)
    print(f"✅ Saved: {FEEDBACK_PATH}")
    
    # Update daily feedback
    daily = {}
    if os.path.exists(DAILY_FEEDBACK):
        with open(DAILY_FEEDBACK) as f:
            daily = json.load(f)
    daily["weekly_hook_insights"] = hook_analysis["recommendation"]
    tmp_path = DAILY_FEEDBACK + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(daily, f, indent=2)
    os.replace(tmp_path, DAILY_FEEDBACK)
    
    # Print report
    print()
    print(f"📊 **Weekly Analytics** — {len(enriched)} posts")
    print()
    print("## 🎣 Hook Patterns")
    print(f"• Drama words: {'✅ YES' if hook_analysis['recommendation']['use_drama'] else '❌ NO'} ({hook_analysis['drama_avg']} vs {hook_analysis['non_drama_avg']})")
    print(f"• Short hooks: {'✅ YES' if hook_analysis['recommendation']['prefer_short'] else '❌ NO'} ({hook_analysis['short_avg']} vs {hook_analysis['long_avg']})")
    print(f"• Statements: {'✅ YES' if hook_analysis['recommendation']['prefer_statements'] else '❌ NO'} ({hook_analysis['statement_avg']} vs {hook_analysis['question_avg']})")
    print()
    print("## 🏆 Top Hooks")
    for h in feedback["top_hooks"]:
        print(f"• \"{h['hook'][:50]}\" — {h['score']}")
    
    return 0

if __name__ == "__main__":
    exit(main())
