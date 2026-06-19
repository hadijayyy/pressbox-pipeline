#!/usr/bin/env python3
"""Press Box Auto-Adjust — Rule-Based Pipeline Optimizer.

Reads analytics_feedback.json + daily analytics data and generates
analytics_recommendations.json for the pipeline.

NO LLM NEEDED — pure rule-based logic. Zero failure rate.

Usage:
    python3 pressbox-auto-adjust.py
"""

import json, os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HOME = os.path.expanduser("~")
FEEDBACK_PATH = f"{HOME}/.hermes/pressbox/analytics_feedback.json"
RECOMMENDATIONS_PATH = f"{HOME}/.hermes/pressbox/analytics_recommendations.json"
DAILY_ANALYTICS_DIR = f"{HOME}/.hermes/cron/output/3a8e8174e9b6"
WIB = timezone(timedelta(hours=7))

# ── Load Analytics Feedback ────────────────────────────────────────
def load_feedback():
    """Load the latest analytics feedback data."""
    try:
        with open(FEEDBACK_PATH) as f:
            fb = json.load(f)
        # Check freshness
        gen_dt = datetime.fromisoformat(fb.get("generated_at", ""))
        if datetime.now(WIB) - gen_dt > timedelta(hours=48):
            print("⚠️ Feedback >48h old — using anyway (auto-adjust)")
        return fb
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"❌ Cannot load feedback: {e}")
        return None

# ── Load Daily Analytics ───────────────────────────────────────────
def load_daily_analytics():
    """Load the latest daily analytics report for hook/topic breakdown."""
    try:
        files = sorted(os.listdir(DAILY_ANALYTICS_DIR))
        if not files:
            return None
        latest = os.path.join(DAILY_ANALYTICS_DIR, files[-1])
        with open(latest) as f:
            content = f.read()
        # Parse the markdown report
        data = {
            "hooks": {},
            "topics": {},
            "best_hours": [],
        }
        current_section = None
        for line in content.split("\n"):
            line = line.strip()
            if "Hook Performance" in line:
                current_section = "hooks"
            elif "Topic Performance" in line:
                current_section = "topics"
            elif "Best Hours" in line:
                current_section = "hours"
            elif current_section == "hooks" and "•" in line:
                # Format: • hook_type: N posts, avg X replies
                parts = line.split(":")
                if len(parts) >= 2:
                    hook_name = parts[0].replace("•", "").strip()
                    rest = ":".join(parts[1:])
                    import re
                    avg_match = re.search(r"avg\s+([\d.]+)", rest)
                    count_match = re.search(r"(\d+)\s+posts", rest)
                    if avg_match:
                        data["hooks"][hook_name] = {
                            "avg_replies": float(avg_match.group(1)),
                            "count": int(count_match.group(1)) if count_match else 0,
                        }
            elif current_section == "topics" and "•" in line:
                parts = line.split(":")
                if len(parts) >= 2:
                    topic_name = parts[0].replace("•", "").strip()
                    rest = ":".join(parts[1:])
                    import re
                    avg_match = re.search(r"avg\s+([\d.]+)", rest)
                    count_match = re.search(r"(\d+)\s+posts", rest)
                    if avg_match:
                        data["topics"][topic_name] = {
                            "avg_replies": float(avg_match.group(1)),
                            "count": int(count_match.group(1)) if count_match else 0,
                        }
            elif current_section == "hours" and "•" in line:
                import re
                hours = re.findall(r"(\d{2}):00", line)
                data["best_hours"] = [int(h) for h in hours]
        return data
    except Exception as e:
        print(f"⚠️ Cannot load daily analytics: {e}")
        return None

# ── Generate Recommendations ───────────────────────────────────────
def generate_recommendations(fb, daily):
    """Generate pipeline recommendations based on analytics data."""
    recs = {
        "generated_at": datetime.now(WIB).isoformat(),
        "period": "Daily",
        "hours_analyzed": 24,
        "total_posts": fb.get("total_posts", 0),
        "total_views": 0,  # will fill if daily data available
        "total_replies": 0,
        "analysis": {
            "research_tweaks": {
                "keyword_additions": [],
                "keyword_removals": [],
            },
            "generate_tweaks": {
                "preferred_hooks": [],
                "cta_pattern": "",
                "tone_adjustment": "Conversational English. Bold numbers. High-impact words.",
            },
            "topic_strategy": {},
            "skip_topics": [],
        },
        "fallback": False,
    }

    topic_boosts = fb.get("topic_boosts", {})
    top_topics = fb.get("top_topics", [])
    skip_topics = fb.get("skip_topics", [])
    best_hours = fb.get("best_hours", [])
    overall_avg = fb.get("overall_avg_score", 50)

    # ── 1. SKIP TOPICS (from feedback) ─────────────────────────────
    recs["analysis"]["skip_topics"] = [s["pattern"] for s in skip_topics]

    # ── 2. TOPIC STRATEGY ──────────────────────────────────────────
    for t in top_topics:
        topic = t["topic"]
        avg = t["avg_score"]
        count = t["count"]
        boost = topic_boosts.get(topic, 1.0)

        strategy = "maintain"
        if boost >= 1.05:
            strategy = "boost"
        elif boost <= 0.90:
            strategy = "reduce"

        recs["analysis"]["topic_strategy"][topic] = {
            "avg_score": avg,
            "count": count,
            "boost": boost,
            "strategy": strategy,
        }

    # ── 3. RESEARCH KEYWORD ADDITIONS ──────────────────────────────
    # Keywords from high-performing topics
    keyword_map = {
        "fifa_political": ["FIFA", "controversy", "political", "ban", "protest", "World Cup controversy"],
        "transfer_rumor": ["transfer", "signing", "deal", "bid", "exclusive"],
        "tournament_news": ["World Cup", "match", "result", "qualifier", "group stage"],
        "player_profile": ["rise of", "story of", "career", "profile", "background"],
        "controversy": ["scandal", "outrage", "backlash", "banned"],
    }

    for t in top_topics:
        topic = t["topic"]
        boost = topic_boosts.get(topic, 1.0)
        if boost >= 1.05 and topic in keyword_map:
            recs["analysis"]["research_tweaks"]["keyword_additions"].extend(keyword_map[topic])

    # Remove duplicates
    recs["analysis"]["research_tweaks"]["keyword_additions"] = list(
        set(recs["analysis"]["research_tweaks"]["keyword_additions"])
    )

    # Keywords from low-performing topics → removals
    for t in skip_topics:
        pattern = t["pattern"]
        if pattern in keyword_map:
            recs["analysis"]["research_tweaks"]["keyword_removals"].extend(keyword_map[pattern])

    recs["analysis"]["research_tweaks"]["keyword_removals"] = list(
        set(recs["analysis"]["research_tweaks"]["keyword_removals"])
    )

    # ── 4. PREFERRED HOOKS ─────────────────────────────────────────
    if daily and daily.get("hooks"):
        # Find hooks with above-average replies
        hook_avgs = [h["avg_replies"] for h in daily["hooks"].values()]
        overall_hook_avg = sum(hook_avgs) / max(len(hook_avgs), 1)

        for hook_name, hook_data in daily["hooks"].items():
            if hook_data["avg_replies"] > overall_hook_avg and hook_data["count"] >= 2:
                recs["analysis"]["generate_tweaks"]["preferred_hooks"].append(hook_name)

    # ── 5. CTA PATTERN ─────────────────────────────────────────────
    # Analyze from daily analytics if CTA vs non-CTA data available
    if daily and daily.get("topics"):
        # Default: always include CTA
        recs["analysis"]["generate_tweaks"]["cta_pattern"] = "Always include CTA"

    # ── 6. TONE ADJUSTMENT ─────────────────────────────────────────
    # Boost controversial/aggressive tone for high-performing political topics
    if topic_boosts.get("fifa_political", 1.0) >= 1.05:
        recs["analysis"]["generate_tweaks"]["tone_adjustment"] = (
            "Conversational English. Bold numbers. High-impact words. "
            "Emphasize controversy and political drama. "
            "Use provocative questions to drive engagement."
        )
    elif topic_boosts.get("transfer_rumor", 1.0) >= 1.0:
        recs["analysis"]["generate_tweaks"]["tone_adjustment"] = (
            "Conversational English. Bold numbers. High-impact words. "
            "Focus on exclusive insider info and breaking news feel."
        )

    # ── 7. BEST HOURS ──────────────────────────────────────────────
    if best_hours:
        recs["analysis"]["best_hours"] = best_hours

    return recs

# ── Main ───────────────────────────────────────────────────────────
def main():
    print("🔧 Press Box Auto-Adjust — Rule-Based Pipeline Optimizer")
    print("=" * 60)

    fb = load_feedback()
    if not fb:
        print("❌ No feedback data available. Run analytics-feedback first.")
        return 1

    daily = load_daily_analytics()

    recs = generate_recommendations(fb, daily)

    # Write recommendations
    os.makedirs(os.path.dirname(RECOMMENDATIONS_PATH), exist_ok=True)
    tmp = RECOMMENDATIONS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(recs, f, indent=2)
    os.replace(tmp, RECOMMENDATIONS_PATH)

    # Print summary
    analysis = recs["analysis"]
    rt = analysis["research_tweaks"]
    gt = analysis["generate_tweaks"]
    ts = analysis["topic_strategy"]

    print(f"\n✅ Recommendations written: {RECOMMENDATIONS_PATH}")
    print(f"\n📊 Topic Strategy:")
    for topic, info in sorted(ts.items(), key=lambda x: x[1]["boost"], reverse=True):
        emoji = "🔥" if info["strategy"] == "boost" else "⬇️" if info["strategy"] == "reduce" else "➡️"
        print(f"  {emoji} {topic}: {info['avg_score']:.0f} avg, {info['boost']}x boost → {info['strategy']}")

    print(f"\n🔑 Research Keywords:")
    if rt["keyword_additions"]:
        print(f"  ➕ Add: {', '.join(rt['keyword_additions'][:10])}")
    if rt["keyword_removals"]:
        print(f"  ➖ Remove: {', '.join(rt['keyword_removals'][:10])}")

    print(f"\n🪝 Preferred Hooks: {', '.join(gt['preferred_hooks']) if gt['preferred_hooks'] else '(none)'}")
    print(f"📝 Tone: {gt['tone_adjustment'][:80]}...")
    print(f"⏩ Skip Topics: {', '.join(analysis['skip_topics']) if analysis['skip_topics'] else '(none)'}")

    return 0

if __name__ == "__main__":
    exit(main())
