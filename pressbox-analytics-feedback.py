#!/usr/bin/env python3
"""Press Box Analytics → Feedback Loop (Fast Version).

Fetches last 20 posts, analyzes engagement, outputs:
1. analytics_feedback.json  — consumed by pipeline for topic boosts
2. analytics_report.md      — Telegram delivery

Usage:
    python3 ~/.hermes/scripts/pressbox-analytics-feedback.py
"""

import json, os, httpx, re
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

TOKEN_PATH = os.path.expanduser("~/.hermes/threads_token.json")
FEEDBACK_PATH = os.path.expanduser("~/.hermes/pressbox/analytics_feedback.json")
REPORT_PATH = os.path.expanduser("~/.hermes/pressbox/analytics_report.md")
WIB = timezone(timedelta(hours=7))

def get_token():
    with open(TOKEN_PATH) as f:
        data = json.load(f)
    return data["access_token"], data["user_id"]

def fetch_recent_posts(tok, uid, limit=20):
    r = httpx.get(f"https://graph.threads.net/v1.0/{uid}/threads",
                  params={"access_token": tok, "fields": "id,text,timestamp", "limit": limit},
                  timeout=15)
    return r.json().get("data", [])

def fetch_engagement(tok, post_id):
    try:
        r = httpx.get(f"https://graph.threads.net/v1.0/{post_id}/insights",
                      params={"access_token": tok, "metric": "likes,replies,reposts,views,quotes", "period": "lifetime"},
                      timeout=8)
        m = {"likes": 0, "replies": 0, "reposts": 0, "views": 0, "quotes": 0}
        for x in r.json().get("data", []):
            m[x["name"]] = x["values"][0]["value"]
        return m
    except:
        return {"likes": 0, "replies": 0, "reposts": 0, "views": 0, "quotes": 0}

def calc_score(m):
    return m["likes"] + m["replies"] * 3 + m["reposts"] * 2 + m["quotes"] * 2

def extract_topic(text):
    text = text.lower()
    for topic, pats in {
        "world_cup": ["world cup", "fifa", "qualifier", "2026"],
        "transfer": ["transfer", "signing", "deal", "bid", "join"],
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

def to_wib_hour(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(WIB).hour

def main():
    tok, uid = get_token()
    raw = fetch_recent_posts(tok, uid, limit=20)
    if not raw:
        print("No posts found.")
        return 0

    print(f"📊 Analyzing {len(raw)} posts...")
    enriched = []
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
                "wib_hour": to_wib_hour(p["timestamp"]),
            })

    enriched.sort(key=lambda x: x["score"], reverse=True)
    overall_avg = sum(p["score"] for p in enriched) / max(len(enriched), 1)

    # Topic analysis
    topic_stats = defaultdict(lambda: {"count": 0, "total": 0})
    for p in enriched:
        for t in [extract_topic(p["text"])]:
            topic_stats[t]["count"] += 1
            topic_stats[t]["total"] += p["score"]

    # Generate boosts
    boosts = {}
    for topic, stats in topic_stats.items():
        if stats["count"] >= 2:
            ratio = stats["total"] / stats["count"] / max(overall_avg, 1)
            if ratio >= 1.5:
                boosts[topic] = min(round(ratio, 1), 3.0)
            elif ratio < 0.5:
                boosts[topic] = 0.3

    # Skip patterns (topics with avg score < 40% of overall)
    skip = []
    for topic, stats in topic_stats.items():
        if stats["count"] >= 2:
            avg = stats["total"] / stats["count"]
            if avg < overall_avg * 0.4:
                skip.append({"pattern": topic, "avg_score": round(avg, 1), "instances": stats["count"]})

    # Best/worst hours
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
        "topic_boosts": boosts,
        "top_topics": [{"topic": t, "avg_score": round(s["total"]/s["count"], 1), "count": s["count"]}
                       for t, s in sorted(topic_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:5]],
        "skip_topics": skip,
        "best_hours": best_hours,
        "worst_hours": worst_hours,
    }

    os.makedirs(os.path.dirname(FEEDBACK_PATH), exist_ok=True)
    # Atomic write — write to temp file then rename (prevents corruption)
    tmp_path = FEEDBACK_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(feedback, f, indent=2)
    os.replace(tmp_path, FEEDBACK_PATH)
    print(f"✅ Feedback: {FEEDBACK_PATH}")

    # Build report
    rpt = []
    rpt.append(f"📊 **Press Box Analytics** — {datetime.now(WIB).strftime('%d %b %H:%M WIB')}")
    rpt.append(f"_{len(enriched)} posts analyzed_")
    rpt.append("")
    rpt.append("## 🎯 Topic Performance")
    for t in feedback["top_topics"][:5]:
        emoji = "🔥" if t["avg_score"] > overall_avg else "📊"
        rpt.append(f"{emoji} **{t['topic']}:** {t['avg_score']:.0f} avg ({t['count']} posts)")
    rpt.append("")
    if boosts:
        rpt.append("## ⬆️ Pipeline Boosts")
        for t, m in sorted(boosts.items(), key=lambda x: x[1], reverse=True):
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
