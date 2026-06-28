#!/usr/local/bin/python3
"""
PRESS BOX ANALYTICS LLM — Smart Daily/Weekly Review.
Fetches Threads posts + engagement, sends to LLM for deep analysis,
saves structured recommendations → research & generate scripts adapt.

Usage:
    python3 pressbox-analytics-llm.py              # daily (24h)
    python3 pressbox-analytics-llm.py --weekly      # weekly (7d)

Output: Markdown report + analytics_recommendations.json
"""
import json, os, sys, re, httpx
from datetime import datetime, timezone, timedelta

# Import shared classifier from pressbox_common — single source of truth
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pressbox_common import classify_topic_type

HOME = os.path.expanduser("~")
TOKEN_PATH = f"{HOME}/.hermes/threads_token.json"
ENV_PATH = f"{HOME}/.hermes/.env"
RECOMMENDATIONS_FILE = f"{HOME}/.hermes/pressbox/analytics_recommendations.json"
WIB = timezone(timedelta(hours=7))

# ── API config — same chain as v7 pipeline ──
env_config = {}
if os.path.exists(ENV_PATH):
    with open(ENV_PATH) as _env:
        for line in _env:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_config[k.strip()] = v.strip().strip("\"'")

MISTRAL_API_KEY = env_config.get("MISTRAL_API_KEY", "")

# Provider registry (same as v7 pipeline)
PROVIDERS = {
    "mistral-large-latest": {
        "base_url": "https://api.mistral.ai/v1/chat/completions",
        "api_key":  MISTRAL_API_KEY,
    },
    "qwen/qwen3-32b": {
        "base_url": "http://localhost:20128/v1/chat/completions",
        "api_key":  "9router-noauth",  # 9router doesn't need auth
    },
}

# Model chain: Mistral primary → 9router fallback
MODEL_CHAIN = [
    {"model": "mistral-large-latest", "max_tokens": 4000},
    {"model": "qwen/qwen3-32b",       "max_tokens": 4000},
]
LLM_TIMEOUT = 120

# ── HOOK FORMULAS (for LLM classification) ──
HOOK_FORMULAS = {
    "negative_hook": "The Negative Hook: Challenge a common mistake/fear — '90% of fans get X wrong. Here's the truth...'",
    "credibility_result": "The Credibility + Result: 'We analyzed data over timeframe. Here are N key takeaways...'",
    "contrarian": "The Contrarian: Go against popular opinion — 'Everyone says X, but that's exactly why Y is better.'",
    "specific_transformation": "The Specific Transformation: 'From bad to good. Here's the N-step system that changed everything.'",
    "curiosity_gap": "The Curiosity Gap: 'There's one hidden factor behind topic. It's not obvious thing, it's...'",
}

TOPIC_CATEGORIES = [
    "injury_update", "transfer_rumor", "managerial_change", "fifa_political",
    "WC_team_guide", "controversy", "tactical_analysis", "match_result",
    "player_profile", "tournament_news", "other"
]


def get_token():
    with open(TOKEN_PATH) as f:
        data = json.load(f)
    return data["access_token"], data["user_id"]


def fetch_posts(tok, uid, hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    posts = []
    url = f"https://graph.threads.net/v1.0/{uid}/threads"
    params = {"access_token": tok, "fields": "id,text,timestamp", "limit": 50}
    while True:
        r = httpx.get(url, params=params, timeout=10)
        data = r.json()
        for p in data.get("data", []):
            ts = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
            if ts < cutoff:
                return posts
            posts.append(p)
        paging = data.get("paging", {})
        if "next" not in paging:
            break
        url = paging["next"]
    return posts


def fetch_engagement(tok, post_id):
    r = httpx.get(
        f"https://graph.threads.net/v1.0/{post_id}/insights",
        params={"access_token": tok, "metric": "likes,replies,reposts,views,quotes", "period": "lifetime"},
        timeout=10,
    )
    metrics = {"likes": 0, "replies": 0, "reposts": 0, "views": 0, "quotes": 0}
    for m in r.json().get("data", []):
        metrics[m["name"]] = m["values"][0]["value"]
    return metrics


def fetch_last_reply_text(tok, root_id):
    """Traverse nested reply chain to find the last slide (slide 8 with CTA)."""
    pid = root_id
    last_text = ""
    for _ in range(10):  # max 10 levels deep
        try:
            r = httpx.get(
                f"https://graph.threads.net/v1.0/{pid}/replies",
                params={"access_token": tok, "fields": "id,text", "limit": 1},
                timeout=10,
            )
            replies = r.json().get("data", [])
            if not replies:
                break
            last_text = replies[0].get("text", "")
            pid = replies[0]["id"]
        except Exception:
            break
    return last_text


def classify_hook(text):
    """Classify which hook formula a post opening matches."""
    if not text:
        return "unknown"
    first_200 = text[:200].lower()
    if any(w in first_200 for w in ["wrong", "mistake", "truth", "actually", "challenge"]):
        return "negative_hook"
    if any(w in first_200 for w in ["everyone says", "popular opinion", "contrary", "believe it or not"]):
        return "contrarian"
    if any(w in first_200 for w in ["hidden", "secret", "revealed", "didn't know", "here's why", "the reason"]):
        return "curiosity_gap"
    if any(w in first_200 for w in ["analyzed", "data", "numbers", "stats", "breakdown"]):
        return "credibility_result"
    if any(w in first_200 for w in ["from ", "to ", "changed", "turned", "transformed"]):
        return "specific_transformation"
    return "uncategorized"


# classify_topic_type is now imported from pressbox_common (single source of truth)
# Local duplicate removed on 22 Jun 2026 — was causing skip_topics mismatch with v7 pipeline.


def call_llm(prompt, max_retries=4):
    """Call LLM with model chain fallback (same order as v7 pipeline).
    Returns parsed JSON or None.
    Chain: mistral-large-latest → qwen/qwen3-32b via 9router
    """
    system_msg = f"You are a social media analytics expert. Current time: {datetime.now(WIB).strftime('%A, %d %B %Y %H:%M WIB')}. Analyze the data and return ONLY valid JSON."

    for attempt, entry in enumerate(MODEL_CHAIN):
        model_name = entry["model"]
        max_tok = entry["max_tokens"]
        provider = PROVIDERS.get(model_name)

        if not provider or not provider.get("api_key"):
            print(f"  ⏭️ Skipping {model_name} (no API key)", file=sys.stderr)
            continue

        url = provider["base_url"]
        api_key = provider["api_key"]

        print(f"  🤖 LLM attempt {attempt + 1}/{max_retries} ({model_name} via {provider.get('provider', url.split('/')[2])})...", file=sys.stderr, flush=True)

        headers = {"Content-Type": "application/json"}
        headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": max_tok,
            "temperature": 0.5,
            "stream": True,
        }

        try:
            full_content = ""
            reasoning = ""

            with httpx.stream("POST", url, headers=headers, json=payload, timeout=LLM_TIMEOUT) as r:
                if r.status_code != 200:
                    print(f"  ❌ LLM error: HTTP {r.status_code} ({model_name})", file=sys.stderr)
                    continue
                for line in r.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        if delta.get("content"):
                            full_content += delta["content"]
                        if delta.get("reasoning_content"):
                            reasoning += delta["reasoning_content"]
                    except json.JSONDecodeError:
                        continue

            # Use content if available, otherwise fall back to reasoning
            content = full_content.strip() if full_content.strip() else reasoning.strip()

            if not content:
                print(f"  ❌ Empty response from LLM ({model_name})", file=sys.stderr)
                continue

            # Strip thinking from content — find valid JSON
            # Remove markdown code blocks
            content = re.sub(r'```(?:json)?\s*', '', content)
            content = re.sub(r'\s*```', '', content)

            # Find outermost JSON object using brace counting
            brace_depth = 0
            json_start = -1
            for i, c in enumerate(content):
                if c == '{':
                    if brace_depth == 0:
                        json_start = i
                    brace_depth += 1
                elif c == '}':
                    brace_depth -= 1
                    if brace_depth == 0 and json_start >= 0:
                        json_str = content[json_start:i+1]
                        try:
                            result = json.loads(json_str)
                            print(f"  ✅ Got valid JSON from {model_name}", file=sys.stderr)
                            return result
                        except json.JSONDecodeError:
                            continue

            # No valid JSON object found — try the whole content
            try:
                result = json.loads(content)
                print(f"  ✅ Got valid JSON from {model_name}", file=sys.stderr)
                return result
            except json.JSONDecodeError:
                pass

            # Also try reasoning content
            if reasoning and reasoning != content:
                reasoning_clean = re.sub(r'```(?:json)?\s*', '', reasoning)
                reasoning_clean = re.sub(r'\s*```', '', reasoning_clean)
                brace_depth = 0
                json_start = -1
                for i, c in enumerate(reasoning_clean):
                    if c == '{':
                        if brace_depth == 0:
                            json_start = i
                        brace_depth += 1
                    elif c == '}':
                        brace_depth -= 1
                        if brace_depth == 0 and json_start >= 0:
                            json_str = reasoning_clean[json_start:i+1]
                            try:
                                result = json.loads(json_str)
                                print(f"  ✅ Got valid JSON from reasoning ({model_name})", file=sys.stderr)
                                return result
                            except json.JSONDecodeError:
                                continue
                try:
                    result = json.loads(reasoning_clean)
                    print(f"  ✅ Got valid JSON from reasoning ({model_name})", file=sys.stderr)
                    return result
                except json.JSONDecodeError:
                    pass

        except Exception as e:
            print(f"  ❌ LLM call failed for {model_name}: {e}", file=sys.stderr)
            continue

    print("  ❌ All LLM models failed — returning None", file=sys.stderr)
    return None


def main():
    is_weekly = "--weekly" in sys.argv
    hours = 168 if is_weekly else 24
    period = "Weekly" if is_weekly else "Daily"
    
    print(f"📊 **Press Box {period} Analytics** — {datetime.now(WIB).strftime('%d %b %Y, %H:%M WIB')}", flush=True)
    print(file=sys.stderr)
    
    # 1. Fetch data
    try:
        tok, uid = get_token()
    except Exception as e:
        print(f"❌ Token error: {e}", file=sys.stderr)
        print("❌ Gak bisa baca token Threads API.")
        return 1
    
    print(f"  📥 Fetching posts (last {hours}h)...", flush=True, file=sys.stderr)
    raw_posts = fetch_posts(tok, uid, hours=hours)
    
    if not raw_posts:
        print("  ⚠️ No posts found.", flush=True, file=sys.stderr)
        print(f"⚠️ **{period} Review:** No posts in the last {hours}h.")
        return 0
    
    # 2. Fetch engagement concurrently
    print(f"  📊 Fetching engagement for {len(raw_posts)} posts...", flush=True, file=sys.stderr)
    enriched = []
    for p in raw_posts:
        try:
            metrics = fetch_engagement(tok, p["id"])
        except Exception:
            metrics = {"likes": 0, "replies": 0, "reposts": 0, "views": 0, "quotes": 0}
        
        text = p.get("text", "") or ""
        # Check CTA: root text OR last reply (slide 8 is a reply with "?")
        last_reply = fetch_last_reply_text(tok, p["id"])
        has_cta = (
            "?" in text.split("\n")[-1]
            or text.strip().endswith("?")
            or "?" in last_reply.split("\n")[-1]
            or last_reply.strip().endswith("?")
        )
        enriched.append({
            "id": p["id"],
            "text": text[:500],  # Truncate for LLM token budget
            "ts": p["timestamp"],
            "metrics": metrics,
            "score": metrics["likes"] * 1 + metrics["replies"] * 3 + metrics["reposts"] * 2 + metrics["quotes"] * 2,
            "wib_hour": datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00")).astimezone(WIB).hour,
            "hook": classify_hook(text),
            "topic": classify_topic_type(text),
            "has_cta": has_cta,
        })
    
    enriched.sort(key=lambda x: x["score"], reverse=True)
    
    # 3. Build aggregates for LLM
    total = len(enriched)
    top5 = enriched[:5]
    worst5 = enriched[-5:] if len(enriched) >= 5 else enriched
    total_views = sum(p["metrics"]["views"] for p in enriched)
    total_replies = sum(p["metrics"]["replies"] for p in enriched)
    
    # Hook distribution
    hook_dist = {}
    for p in enriched:
        h = p["hook"]
        hook_dist.setdefault(h, {"count": 0, "total_replies": 0, "total_score": 0})
        hook_dist[h]["count"] += 1
        hook_dist[h]["total_replies"] += p["metrics"]["replies"]
        hook_dist[h]["total_score"] += p["score"]
    
    # Topic distribution
    topic_dist = {}
    for p in enriched:
        t = p["topic"]
        topic_dist.setdefault(t, {"count": 0, "total_replies": 0, "total_score": 0})
        topic_dist[t]["count"] += 1
        topic_dist[t]["total_replies"] += p["metrics"]["replies"]
        topic_dist[t]["total_score"] += p["score"]
    
    # Hourly performance
    hour_perf = {}
    for p in enriched:
        h = p["wib_hour"]
        hour_perf.setdefault(h, {"count": 0, "replies": 0, "views": 0})
        hour_perf[h]["count"] += 1
        hour_perf[h]["replies"] += p["metrics"]["replies"]
        hour_perf[h]["views"] += p["metrics"]["views"]
    
    # CTA analysis
    cta_posts = [p for p in enriched if p["has_cta"]]
    no_cta_posts = [p for p in enriched if not p["has_cta"]]
    avg_cta_reply = sum(p["metrics"]["replies"] for p in cta_posts) / max(len(cta_posts), 1)
    avg_no_cta_reply = sum(p["metrics"]["replies"] for p in no_cta_posts) / max(len(no_cta_posts), 1)
    
    # Dead posts
    dead_posts = [p for p in enriched if p["metrics"]["views"] < 100 and p["metrics"]["replies"] == 0]
    
    print(f"  ✅ {total} posts, {total_views} views, {total_replies} replies", flush=True, file=sys.stderr)
    print(file=sys.stderr)
    
    # 4. Send to LLM for deep analysis
    print(f"  🤖 Analyzing with LLM...", flush=True, file=sys.stderr)
    
    # Build data packet for LLM
    posts_summary = []
    for p in enriched:
        posts_summary.append({
            "text_preview": p["text"][:200],
            "hook": p["hook"],
            "topic_type": p["topic"],
            "has_cta": p["has_cta"],
            "likes": p["metrics"]["likes"],
            "replies": p["metrics"]["replies"],
            "reposts": p["metrics"]["reposts"],
            "views": p["metrics"]["views"],
            "score": p["score"],
            "hour": p["wib_hour"],
        })
    
    llm_prompt = f"""You are a social media content strategist for @parkthebus.football (football news Threads account).
Analyze the last {hours}h of performance data and return JSON with creative, innovative recommendations.

CONTEXT:
- Account: @parkthebus.football — football news, World Cup 2026, transfers
- Content cycle: Research → Generate (LLM writes slides) → Post to Threads
- 5 hook formulas used: Negative Hook, Credibility+Result, Contrarian, Specific Transformation, Curiosity Gap
- Posting schedule: ~12-15 posts/day, every hour at :30

HOOK FORMULAS REFERENCE:
{json.dumps(HOOK_FORMULAS, indent=2)}

PERFORMANCE DATA:
- Period: last {hours}h
- Total posts: {total}
- Total views: {total_views}
- Total replies: {total_replies}
- Avg views/post: {total_views/max(total,1):.0f}
- Avg replies/post: {total_replies/max(total,1):.1f}
- Posts with CTA: {len(cta_posts)} (avg {avg_cta_reply:.1f} replies)
- Posts without CTA: {len(no_cta_posts)} (avg {avg_no_cta_reply:.1f} replies)
- Dead posts (<100 views, 0 replies): {len(dead_posts)}

HOOK DISTRIBUTION:
{json.dumps({k: {"count": v["count"], "avg_replies": round(v["total_replies"]/max(v["count"],1), 1), "avg_score": round(v["total_score"]/max(v["count"],1), 1)} for k, v in hook_dist.items()}, indent=2)}

TOPIC DISTRIBUTION:
{json.dumps({k: {"count": v["count"], "avg_replies": round(v["total_replies"]/max(v["count"],1), 1), "avg_score": round(v["total_score"]/max(v["count"],1), 1)} for k, v in topic_dist.items()}, indent=2)}

HOURLY PERFORMANCE:
{json.dumps({str(h)+":00": {"posts": v["count"], "replies": v["replies"], "views": v["views"], "avg_reply": round(v["replies"]/max(v["count"],1), 1)} for h, v in sorted(hour_perf.items())}, indent=2)}

TOP 5 POSTS (highest score):
{json.dumps([{"preview": p["text"][:150], "hook": p["hook"], "topic": p["topic"], "score": p["score"], "replies": p["metrics"]["replies"], "has_cta": p["has_cta"]} for p in top5], indent=2)}

BOTTOM 5 POSTS (lowest score):
{json.dumps([{"preview": p["text"][:150], "hook": p["hook"], "topic": p["topic"], "score": p["score"], "replies": p["metrics"]["replies"], "has_cta": p["has_cta"]} for p in worst5], indent=2)}

Return ONLY a JSON object with this EXACT structure:
{{
  "summary": {{
    "engagement_rate": "X% (up/down vs baseline)",
    "top_performer_insight": "1-sentence on what's working best",
    "biggest_gap": "1-sentence on what's missing"
  }},
  "topic_analysis": {{
    "best_topic_types": ["topic1", "topic2"],
    "worst_topic_types": ["topic3"],
    "topic_recommendation": "Specific: prioritize X topic types, avoid Y"
  }},
  "hook_analysis": {{
    "best_hook": "which hook formula performs best for this account",
    "hook_performance": {{
      "hook_name": "avg replies, insight"
    }},
    "hook_recommendation": "Specific: use X hook for WC content, Y hook for transfers"
  }},
  "cta_analysis": {{
    "cta_effectiveness": "CTA vs no-CTA comparison",
    "best_cta_pattern": "what type of question drives most replies",
    "cta_recommendation": "Specific CTA formulas to use"
  }},
  "timing_analysis": {{
    "best_hours": ["hour1", "hour2"],
    "worst_hours": ["hour3"],
    "timing_recommendation": "Specific schedule adjustment"
  }},
  "content_gaps": {{
    "missing_topics": ["what's trending but not covered"],
    "missed_opportunities": ["angles not exploited"],
    "gap_recommendation": "How to fill gaps"
  }},
  "ab_testing": {{
    "test_to_run": "1 specific A/B test to run next cycle",
    "hypothesis": "what we expect to learn",
    "success_metric": "how to measure"
  }},
  "research_tweaks": {{
    "topic_priority_shift": "Should research prioritize different topics?",
    "source_tweaks": "Any source changes?",
    "keyword_additions": ["new keywords to add for better topic filtering"],
    "keyword_removals": ["keywords not working"]
  }},
  "generate_tweaks": {{
    "preferred_hooks": ["curiosity_gap", "contrarian", "stat_shock", "human_emotion", "behind_scenes", "controversial_take", "nostalgia"],
    "hook_assignments": "Which hook for which content type?",
    "slide_count": "optimal slides per post based on engagement data",
    "tone_adjustment": "More/less aggressive, formal/casual?",
    "cta_pattern": "Ask a polarizing question (e.g. 'Is it time to move on?', 'Am I wrong?', 'Who says no?')",
    "length_adjustment": "Longer/shorter slides based on engagement"
  }},
  "experiments": [
    "1 specific experiment to try next period"
  ],
  "action_items": [
    "3-5 concrete actions sorted by impact"
  ]
}}

IMPORTANT:
- Be specific, not generic. Don't say "post better content" — say "use Contrarian hook for transfer rumors"
- Base insights on the actual data above, not generic advice
- Suggest INNOVATIVE changes — things we haven't tried
- Research_tweaks and generate_tweaks will be AUTOMATICALLY applied to scripts
- For preferred_hooks, pick 2-4 specific hooks from: curiosity_gap, contrarian, stat_shock, human_emotion, behind_scenes, controversial_take, nostalgia. NEVER return "uncategorized".
- For cta_pattern, give a concrete question formula (e.g. "Ask a polarizing either/or question"), never "Always include CTA"
"""
    
    llm_result = call_llm(llm_prompt)
    
    # 5. Save recommendations
    if llm_result:
        # Merge with metadata
        output = {
            "generated_at": datetime.now(WIB).isoformat(),
            "period": period,
            "hours_analyzed": hours,
            "total_posts": total,
            "total_views": total_views,
            "total_replies": total_replies,
            "analysis": llm_result
        }
        os.makedirs(os.path.dirname(RECOMMENDATIONS_FILE), exist_ok=True)
        with open(RECOMMENDATIONS_FILE, 'w') as f:
            json.dump(output, f, indent=2)
        print(f"  ✅ Recommendations saved", flush=True, file=sys.stderr)
    else:
        print(f"  ⚠️ LLM analysis failed — using fallback stats", flush=True, file=sys.stderr)
        # Fallback: save basic stats
        output = {
            "generated_at": datetime.now(WIB).isoformat(),
            "period": period,
            "hours_analyzed": hours,
            "total_posts": total,
            "total_views": total_views,
            "total_replies": total_replies,
            "analysis": None,
            "fallback": True
        }
        os.makedirs(os.path.dirname(RECOMMENDATIONS_FILE), exist_ok=True)
        with open(RECOMMENDATIONS_FILE, 'w') as f:
            json.dump(output, f, indent=2)
    
    # 6. Print human-readable report
    print(file=sys.stderr)
    print(f"📊 **Press Box {period} Review** — {datetime.now(WIB).strftime('%d %b %Y, %H:%M WIB')}", flush=True)
    print(f"_{total} posts · {total_views} views · {total_replies} replies_", flush=True)
    print(flush=True)
    
    # Engagement summary
    eng_rate = total_replies / max(total_views, 1) * 100
    print(f"**Engagement:** {eng_rate:.2f}% | Avg {total_replies/max(total,1):.1f} replies/post", flush=True)
    print(f"**CTA Impact:** {avg_cta_reply:.1f} replies (with CTA) vs {avg_no_cta_reply:.1f} (without)", flush=True)
    print(f"**Dead posts:** {len(dead_posts)}/{total}", flush=True)
    print(flush=True)
    
    # Top 5
    print(f"**{'📈 Weekly' if is_weekly else '🔥 Daily'} Top 5:**", flush=True)
    for i, p in enumerate(top5, 1):
        preview = p["text"][:80].replace("\n", " ")
        print(f"{i}. {preview}...", flush=True)
        print(f"   👍{p['metrics']['likes']} 💬{p['metrics']['replies']} 🔁{p['metrics']['reposts']} 👀{p['metrics']['views']} | 🪝{p['hook']} | 📂{p['topic']} | {'✅CTA' if p['has_cta'] else '❌noCTA'}", flush=True)
    print(flush=True)
    
    # Hook insights
    print(f"**🎣 Hook Performance:**", flush=True)
    hook_sorted = sorted(hook_dist.items(), key=lambda x: x[1]["total_replies"]/max(x[1]["count"],1), reverse=True)
    for hook_name, data in hook_sorted:
        avg_r = data["total_replies"] / max(data["count"], 1)
        bar = "█" * min(int(avg_r), 20)
        print(f"• {hook_name}: {data['count']} posts, avg {avg_r:.1f} replies {bar}", flush=True)
    print(flush=True)
    
    # Topic insights
    print(f"**📂 Topic Performance:**", flush=True)
    topic_sorted = sorted(topic_dist.items(), key=lambda x: x[1]["total_replies"]/max(x[1]["count"],1), reverse=True)
    for topic_name, data in topic_sorted[:6]:
        avg_r = data["total_replies"] / max(data["count"], 1)
        bar = "█" * min(int(avg_r), 20)
        print(f"• {topic_name}: {data['count']} posts, avg {avg_r:.1f} replies {bar}", flush=True)
    print(flush=True)
    
    # LLM Recommendations
    if llm_result and llm_result.get("action_items"):
        print(f"**💡 AI Recommendations:**", flush=True)
        for item in llm_result.get("action_items", []):
            print(f"• {item}", flush=True)
        print(flush=True)
        
        if llm_result.get("ab_testing"):
            test = llm_result["ab_testing"]
            print(f"**🧪 Next Experiment:** {test.get('test_to_run', 'N/A')}", flush=True)
            print(f"  Hypothesis: {test.get('hypothesis', 'N/A')}", flush=True)
            print(flush=True)
        
        if llm_result.get("generate_tweaks"):
            gt = llm_result["generate_tweaks"]
            print(f"**⚙️ Auto-Applied to Generate Phase:**", flush=True)
            if gt.get("preferred_hooks"):
                print(f"• Hooks: {', '.join(gt['preferred_hooks'])}", flush=True)
            if gt.get("cta_pattern"):
                print(f"• CTA: {gt['cta_pattern']}", flush=True)
            print(flush=True)
        
        if llm_result.get("research_tweaks"):
            rt = llm_result["research_tweaks"]
            print(f"**⚙️ Auto-Applied to Research Phase:**", flush=True)
            if rt.get("topic_priority_shift"):
                print(f"• Topic shift: {rt['topic_priority_shift']}", flush=True)
            if rt.get("keyword_additions"):
                print(f"• New keywords: {', '.join(rt['keyword_additions'][:5])}", flush=True)
            print(flush=True)
    
    # Dead posts
    if dead_posts:
        print(f"**💀 Dead Posts ({len(dead_posts)}):**", flush=True)
        for p in dead_posts[:5]:
            print(f"• {p['text'][:60].replace(chr(10),' ')}... 👀{p['metrics']['views']}h:{p['wib_hour']}", flush=True)
        print(flush=True)
    
    return 0


if __name__ == "__main__":
    exit(main())
