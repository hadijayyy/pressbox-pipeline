# 📰 Press Box — Automated Football Content Pipeline

**Auto-publish football news threads to Threads (@parkthebus.football)**

Press Box is a fully automated content pipeline that scrapes football news (Mirror, Sky Sports, Goal.com), generates 8-slide threads via LLM (deepseek-v4-flash), and posts them to Threads with relevant images — completely unattended.

## ⚡ Performance (v7 — Data Extraction Agent)

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| LLM time | 90-120s | **13.2s** | **9x faster** |
| Total time | 125s | **17.9s** | **7x faster** |
| Tokens | 7,000-9,000 | **2,382** | **73% reduction** |
| Reasoning | 24K-30K chars | **925 chars** | **97% reduction** |
| Retries | 2-3x | **0** | **First attempt success** |

**Key insight:** "Data Extraction Agent" prompt role bypasses DeepSeek's reasoning behavior. Model outputs JSON directly in `content` field instead of burying it in `reasoning_content`.

## ✨ Features

- **Automated research** — scrapes Mirror RSS + Sky Sports RSS + Goal.com for fresh football news
- **Smart dedup** — URL-based, title Jaccard similarity (35% threshold), 30-min article cache
- **LLM-generated threads** — 8 slides with hook, storytelling arc, and CTA question
- **Data Extraction Agent prompt** — bypasses reasoning, outputs JSON directly
- **3-level image fallback** — og:image → article body `<img>` → RSS image
- **Image validation** — HEAD request checks every image URL before use
- **Whitespace formatting** — blank line every 2 sentences for readability
- **Fallback loop** — tries up to 3 LLM retries with char count validation (200-450 chars)
- **Strategy 2 JSON extraction** — score-based fallback for reasoning-heavy responses
- **Analytics feedback** — daily engagement analysis tunes posting hours
- **Atomic staging** — tmp + os.replace prevents corruption

## 🏗 Architecture

```
:00 Pipeline ──► scrape (Mirror + Sky Sports + Goal.com)
                  ├── filter (dedup, similarity, freshness)
                  ├── score (WC boost +50, viral +25)
                  ├── extract (article text + 3-level image fallback)
                  ├── LLM generate (deepseek-v4-flash, Data Extraction Agent)
                  └── stage (staging.json)

:15 Check Staging ──► verify staging has valid content

:30 Post ──► read staging → post to Threads (slide-by-slide)
              └── verify all slides posted
```

### Cron Schedule

| Time | Script | Purpose |
|------|--------|---------|
| `:00` | `pressbox-pipeline-v7.py` | Pipeline generate (silent) |
| `:15` | `pressbox-check-staging.py` | Verify staging content |
| `:30` | `pressbox-post.py` | Post to Threads (notifies user) |
| `23:00` | `pressbox-analytics-feedback.py` | Daily analytics |
| `23:00` | `pressbox-analytics-llm.py` | LLM deep analysis |

### Flow per hour

```
:00 Pipeline → staging.json created
:15 Check → staging verified
:30 Post → slides posted to Threads → staging cleared
```

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- Threads API access token (Meta Graph API)
- OpenCode Go API key (or any OpenAI-compatible LLM endpoint)

### Installation

```bash
git clone https://github.com/hadijayyy/pressbox-pipeline.git
cd pressbox-pipeline
pip install httpx requests feedparser
```

### Configuration

Copy `.env.example` to `~/.hermes/.env`:

```bash
cp .env.example ~/.hermes/.env
```

Edit with your credentials:

```env
OPENCODE_GO_API_KEY=your_o..._key
```

### Threads API Token Setup

1. Create a Meta App with Threads API enabled
2. Generate a long-lived access token
3. Save to `~/.hermes/threads_token.json`:
```json
{
  "access_token": "***",
  "user_id": "your_user_id"
}
```

## 📁 Scripts

| Script | Description |
|--------|-------------|
| `pressbox-research.py` | RSS scraper — Mirror, Sky Sports, Goal.com extraction |
| `pressbox-pipeline-v7.py` | **Main pipeline** — scrape → filter → score → extract → LLM → stage |
| `pressbox-check-staging.py` | Recovery job — runs pipeline if staging is empty |
| `pressbox-post.py` | Post manager — reads staging, calls direct-post, updates tracking |
| `pressbox-direct-post.py` | Low-level Threads Graph API client — IMAGE/TEXT container creation |
| `pressbox-analytics-feedback.py` | Daily analytics — topic boosts, best/worst hours |
| `pressbox-analytics-llm.py` | LLM deep analysis — hooks, CTA, topic recommendations |

### Pipeline Flow (v7)

```
pressbox-pipeline-v7.py:
  1. Parallel scrape Mirror RSS + Sky Sports RSS + Goal.com (5-10s)
  2. Filter candidates (URL dedup, Jaccard similarity 35%, 30-min cache)
  3. Score with WC boost (+50), viral keywords (+25)
  4. Pick best candidate
  5. Extract article text via curl (with 30-min cache)
  6. Extract article image:
     a. og:image meta tag → HEAD validate
     b. Article body <img> (first content image) → HEAD validate
     c. RSS image URL → HEAD validate
  7. LLM generate 8-slide JSON (deepseek-v4-flash, Data Extraction Agent prompt)
  8. Retry up to 3x if char count fails (200-450 chars per slide)
  9. Save to staging.json (atomic write)
```

### LLM Prompt: Data Extraction Agent

The prompt is designed to bypass DeepSeek's reasoning behavior:

```
[ROLE & CONSTRAINTS]
You are a strict, high-speed Data Extraction Agent.
Your explicit instruction is to minimize latency and bypass any extended internal monologue or reasoning.

CRITICAL DIRECTIVES:
1. DO NOT use extensive reasoning or step-by-step thinking.
2. Keep your internal thinking process/monologue under 20 words.
3. Move directly to the final output.
4. Output ONLY a valid, raw JSON object.
```

**Result:** Model outputs JSON in `content` field (not `reasoning_content`), with minimal thinking (925 chars vs 30K before).

### Slide Schema

| Slide | Title | Content | Chars |
|-------|-------|---------|-------|
| 1 | HOOK | 1-2 punchy sentences, image_url | 150-300 |
| 2 | SPARK | What happened | 200-450 |
| 3 | WHY | Why it matters | 200-450 |
| 4 | TENSION | Conflict/stakes | 200-450 |
| 5 | HUMAN | Quotes/emotion | 200-450 |
| 6 | RIPPLE | Wider impact | 200-450 |
| 7 | UNRESOLVED | What's next | 200-450 |
| 8 | HOT TAKE | Pick a side + source URL | 200-450 |

## 🖼️ Image Support

The pipeline automatically attaches the article's main image to the **first slide** of every thread:

### 3-Level Fallback Chain

```
1. og:image meta tag → HEAD validate (check HTTP 200)
2. Article body <img> → HEAD validate (first content image, skip icons/logos)
3. RSS image URL → HEAD validate
```

### Supported Sources

| Source | og:image | Body img | RSS img | Status |
|--------|----------|----------|---------|--------|
| Mirror | ✅ 200 | ✅ 200 | ✅ 200 | Works |
| Sky Sports | ✅ 200 | ✅ 200 | ✅ 200 | Works |
| Goal.com | ✅ 200 | ✅ 200 | ✅ 200 | Works |
| Guardian | ❌ CDN blocked | - | - | Skipped |

## 📊 Analytics Feedback Loop

### Daily (23:00 WIB)

**analytics-feedback.py:**
- Fetches last 20 posts via Threads API
- Calculates avg engagement per topic
- Generates topic boosts (1.5x-3x high, 0.3x low)
- Saves to `analytics_feedback.json` → consumed by `pressbox-post.py` (worst_hours check)

**analytics-llm.py:**
- LLM deep analysis of hooks, CTA effectiveness, topic performance
- Traverses nested reply chains to detect CTA (slide 8)
- Saves recommendations to `analytics_recommendations.json`
- Generates markdown report → Telegram delivery

### Analytics Data Flow

```
analytics_feedback.json
  └──► pressbox-post.py (is_bad_hour check)

analytics_recommendations.json
  └──► LLM analysis → Telegram report (human review)
```

### Best Hours (from analytics)

Based on engagement data: **17:00, 23:00, 01:00 WIB**

## 🔧 Data Files

| Path | Purpose |
|------|---------|
| `~/.hermes/pressbox/staging.json` | Pipeline output → Post input |
| `~/.hermes/pressbox/posted_topics.json` | URL + title dedup tracking |
| `~/.hermes/pressbox/article_cache.json` | 30-min article text cache |
| `~/.hermes/pressbox/analytics_feedback.json` | Topic boosts from analytics |
| `~/.hermes/pressbox/analytics_recommendations.json` | LLM analysis results |
| `~/.hermes/threads_token.json` | Threads API token |
| `~/.hermes/.env` | API keys |

## 🛡️ Error Handling

| Issue | Handling |
|-------|----------|
| URL fails | Skip candidate, try next |
| LLM timeout | Retry up to 3x |
| LLM short response (<200 chars) | Retry with stricter prompt |
| JSON parse failure | Strategy 1: slide markers → Strategy 2: score-based fallback |
| JSON in reasoning_content | Extract via brace counting + content length scoring |
| Image og:image fails | Fallback to body `<img>` → RSS |
| Image HEAD check fails | Skip image, proceed text-only |
| Staging guard | Skip if unposted content exists |
| All candidates fail | Exit code 1 → check-staging recovery |

## 📝 Changelog

### v7.1 — Data Extraction Agent (2026-06-17)
- **9x faster LLM** (120s → 13s) via prompt engineering
- Rewrote prompt as "Data Extraction Agent" role
- Added `reasoning_budget` parameter (if supported)
- Added token usage tracking (prompt/completion/total)
- Added Strategy 2 JSON extraction (score-based)
- Added article cache (30-min TTL)
- Fixed `pressbox-check-staging.py` → v7 reference

### v7.0 — Initial Release
- 3-source scraper (Mirror + Sky Sports + Goal.com)
- LLM-generated 8-slide threads
- 3-level image fallback
- Analytics feedback loop

## 🤝 Contributing

PRs welcome! Focus areas:
- Additional sources (BBC, The Athletic)
- Multi-platform support (X/Twitter)
- A/B testing module

## 📄 License

MIT

---

*Built with ❤️ for @parkthebus.football*
