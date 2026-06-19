#!/usr/bin/env python3
"""Press Box Analytics → Feedback Loop (v2 — Historical Merge).

Fetches last 50 posts, merges with previous analytics data using
weighted average (70% new + 30% old) for cumulative learning.

Output:
1. analytics_feedback.json — consumed by pipeline for topic boosts
2. analytics_report.md — Telegram delivery

Usage:
    python3 ~/.hermes/scripts/pressbox-analytics-feedback.py
"""

import json, os, httpx, re
from datetime import datetime, timezone, timedelta
from collections import defaultdict

TOKEN_PATH = os.path.expanduser("~/.hermes/threads_token.json")
FEEDBACK_PATH = os.path.expanduser("~/.hermes/pressbox/analytics_feedback.json")
REPORT_PATH = os.path.expanduser("~/.hermes/pressbox/analytics_report.md")
WIB = timezone(timedelta(hours=7))

# Merge weights: 90% new data + 10% historical (new data dominates)
NEW_WEIGHT = 0.9
OLD_WEIGHT = 0.1

def get_token():
    with open(TOKEN_PATH) as f:
        data = json.load(f)
    return data["access_token"], data["user_id"]

def fetch_all_posts(tok, uid):
    """Fetch ALL posts using pagination (not just 50)."""
    import time as _t
    posts = []
    url = f"https://graph.threads.net/v1.0/{uid}/threads"
    params = {"access_token": tok, "fields": "id,text,timestamp", "limit": 50}
    while True:
        r = httpx.get(url, params=params, timeout=15)
        data = r.json()
        batch = data.get("data", [])
        posts.extend(batch)
        paging = data.get("paging", {})
        next_url = paging.get("next")
        if not next_url or not batch:
            break
        url = next_url
        params = None  # BUGFIX: {} strips query params, None keeps them
        _t.sleep(0.3)
    return posts

def fetch_engagement(tok, post_id):
    try:
        r = httpx.get(f"https://graph.threads.net/v1.0/{post_id}/insights",
                      params={"access_token": tok, "metric": "likes,replies,reposts,views,quotes", "period": "lifetime"},
                      timeout=8)
        m = {"likes": 0, "replies": 0, "reposts": 0, "views": 0, "quotes": 0}
        for x in r.json().get("data", []):
            m[x["name"]] = x["values"][0]["value"]
        return m
    except Exception as e:
        print(f"   ⚠️ Failed to fetch insights for {post_id}: {e}")
        return {"likes": 0, "replies": 0, "reposts": 0, "views": 0, "quotes": 0}

def calc_score(m):
    return m["likes"] + m["replies"] * 3 + m["reposts"] * 2 + m["quotes"] * 2

def extract_topic(text):
    text = text.lower()
    for topic, pats in {
        "world_cup": ["world cup", "qualifier", "2026", "tournament", "wc", "fifa"],
        "transfer": ["transfer", "signing", "deal", "bid", "join"],
        "fifa_political": ["boycott", "qatar", "corruption", "scandal", "politic", "human rights", "migrant", "banned"],
        "controversy": ["controversy", "scandal", "banned", "fined", "racism"],
        "match_result": ["win", "lose", "defeat", "victory", "beat"],
        "injury": ["injury", "injured", "sidelined"],
        "team_profile": ["guide", "profile", "squad", "lineup"],
        "gossip": ["rumour", "reportedly", "linked"],
        "young_talent": ["young", "academy", "debut"],
        "record": ["record", "history", "milestone"],
    }.items():
        for p in pats:
            if p in text:
                return topic
    return "general"

def load_previous_feedback():
    """Load previous analytics feedback for historical merge."""
    try:
        with open(FEEDBACK_PATH) as f:
            old = json.load(f)
        # Check freshness: only merge if < 48h old
        gen_dt = datetime.fromisoformat(old.get("generated_at", ""))
        if datetime.now(WIB) - gen_dt > timedelta(hours=48):
            print("   ⚠️ Previous data >48h old — ignoring")
            return None
        return old
    except (OSError, IOError, json.JSONDecodeError, ValueError, TypeError):
        return None

def merge_topic_boosts(old_boosts, new_boosts):
    """Merge topic boosts using weighted average."""
    all_topics = set(list(old_boosts.keys()) + list(new_boosts.keys()))
    merged = {}
    for topic in all_topics:
        old_val = old_boosts.get(topic, 1.0)  # default 1.0 if not present
        new_val = new_boosts.get(topic, 1.0)
        merged[topic] = round(old_val * OLD_WEIGHT + new_val * NEW_WEIGHT, 2)
    return merged

def merge_topic_stats(old_stats, new_stats):
    """Merge topic stats (count + total) for accurate averages."""
    all_topics = set(list(old_stats.keys()) + list(new_stats.keys()))
    merged = {}
    for topic in all_topics:
        old_c = old_stats.get(topic, {"count": 0, "total": 0})
        new_c = new_stats.get(topic, {"count": 0, "total": 0})
        merged[topic] = {
            "count": old_c["count"] + new_c["count"],
            "total": old_c["total"] + new_c["total"],
        }
    return merged

def main():
    tok, uid = get_token()
    raw = fetch_all_posts(tok, uid)
    if not raw:
        print("No posts found.")
        return 0

    print(f"📊 Analyzing {len(raw)} posts...")

    # Load historical data
    old_data = load_previous_feedback()
    if old_data:
        print(f"   📈 Merging with historical data ({old_data.get('total_posts', '?')} posts)")
    else:
        print("   ℹ️ No historical data — fresh analysis")

    # Fetch engagement for new posts
    enriched = []
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_engagement, tok, p["id"]): p for p in raw}
        for f in as_completed(futs):
            p = futs[f]
            m = f.result()
            enriched.append({
                "text": p.get("text", ""),
                "ts": p["timestamp"],
                "metrics": m,
                "score": calc_score(m),
                "wib_hour": datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00")).astimezone(WIB).hour,
            })

    enriched.sort(key=lambda x: x["score"], reverse=True)
    overall_avg = sum(p["score"] for p in enriched) / max(len(enriched), 1)

    # Topic analysis
    new_topic_stats = defaultdict(lambda: {"count": 0, "total": 0})
    for p in enriched:
        for t in [extract_topic(p["text"])]:
            new_topic_stats[t]["count"] += 1
            new_topic_stats[t]["total"] += p["score"]

    # Generate new boosts
    new_boosts = {}
    for topic, stats in new_topic_stats.items():
        if stats["count"] >= 2:
            ratio = stats["total"] / stats["count"] / max(overall_avg, 1)
            if ratio >= 1.2:  # lowered from 1.5 — was too conservative
                new_boosts[topic] = min(round(ratio, 1), 3.0)
            elif ratio < 0.5:
                new_boosts[topic] = 0.3

    # Merge with historical
    if old_data:
        old_boosts = old_data.get("topic_boosts", {})
        merged_boosts = merge_topic_boosts(old_boosts, new_boosts)
        # Merge topic stats for skip calculation
        old_stats_raw = {}
        for t in old_data.get("top_topics", []):
            old_stats_raw[t["topic"]] = {"count": t["count"], "total": t["avg_score"] * t["count"]}
        merged_stats = merge_topic_stats(old_stats_raw, dict(new_topic_stats))
        overall_avg = sum(s["total"] for s in merged_stats.values()) / max(sum(s["count"] for s in merged_stats.values()), 1)
    else:
        merged_boosts = new_boosts
        merged_stats = dict(new_topic_stats)

    # Skip patterns
    skip = []
    for topic, stats in merged_stats.items():
        if stats["count"] >= 2:
            avg = stats["total"] / stats["count"]
            if avg < overall_avg * 0.4:
                skip.append({"pattern": topic, "avg_score": round(avg, 1), "instances": stats["count"]})

    # Hourly analysis
    hourly = defaultdict(lambda: {"count": 0, "total": 0, "dead": 0})
    for p in enriched:
        h = p["wib_hour"]
        hourly[h]["count"] += 1
        hourly[h]["total"] += p["score"]
        if p["metrics"]["views"] < 100 and p["metrics"]["replies"] == 0:
            hourly[h]["dead"] += 1

    sorted_h = sorted(hourly.items(), key=lambda x: x[1]["total"] / max(x[1]["count"], 1), reverse=True)
    best_hours = [h for h, _ in sorted_h[:3]]
    worst_hours = [h for h, s in sorted_h if s["dead"] / max(s["count"], 1) >= 0.7]

    # Build feedback JSON
    feedback = {
        "generated_at": datetime.now(WIB).isoformat(),
        "total_posts": len(enriched),
        "overall_avg_score": round(overall_avg, 1),
        "topic_boosts": merged_boosts,
        "top_topics": [{"topic": t, "avg_score": round(s["total"]/s["count"], 1), "count": s["count"]}
                       for t, s in sorted(merged_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:5]],
        "skip_topics": skip,
        "best_hours": best_hours,
        "worst_hours": worst_hours,
    }

    os.makedirs(os.path.dirname(FEEDBACK_PATH), exist_ok=True)
    tmp_path = FEEDBACK_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(feedback, f, indent=2)
    os.replace(tmp_path, FEEDBACK_PATH)
    print(f"✅ Feedback: {FEEDBACK_PATH}")

    # Build report
    rpt = []
    rpt.append(f"📊 **Press Box Analytics** — {datetime.now(WIB).strftime('%d %b %H:%M WIB')}")
    rpt.append(f"_{len(enriched)} posts analyzed {'(merged with history)' if old_data else '(fresh)'}_")
    rpt.append("")
    rpt.append("## 🎯 Topic Performance")
    for t in feedback["top_topics"][:5]:
        emoji = "🔥" if t["avg_score"] > overall_avg else "📊"
        rpt.append(f"{emoji} **{t['topic']}:** {t['avg_score']:.0f} avg ({t['count']} posts)")
    rpt.append("")
    if merged_boosts:
        rpt.append("## ⬆️ Pipeline Boosts")
        for t, m in sorted(merged_boosts.items(), key=lambda x: x[1], reverse=True):
            rpt.append(f"• {t}: {m}x boost")
        rpt.append("")
    if skip:
        rpt.append("## ⏩ Skip Topics")
        for s in skip:
            rpt.append(f"• **{s['pattern']}:** {s['avg_score']:.0f} avg ({s['instances']} posts)")
        rpt.append("")
    rpt.append("## ⏰ Best Hours")
    rpt.append(f"• Best: {', '.join(f'{h:02d}:00' for h in best_hours)}")
    if worst_hours:
        rpt.append(f"• Skip: {', '.join(f'{h:02d}:00' for h in worst_hours)}")
    rpt.append("")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(rpt))
    print(f"✅ Report: {REPORT_PATH}")
    print("\n".join(rpt))
    return 0

if __name__ == "__main__":
    exit(main())
