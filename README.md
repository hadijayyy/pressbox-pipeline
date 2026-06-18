# 📰 Press Box — Automated Football Content Pipeline

**Auto-publish football news threads to Threads (@parkthebus.football)**

Press Box is a fully automated content pipeline that scrapes football news (Mirror, Sky Sports, Goal.com), generates 8-slide threads via LLM (deepseek-v4-flash), and posts them to Threads with relevant images — completely unattended.

## ⚡ Performance

| Metric | v6.0 | v7.2 | Improvement |
|--------|------|------|-------------|
| LLM time | 67.7s | **30.9s** | **54% faster** |
| Total time | 73.1s | **39.9s** | **45% faster** |
| Tokens | 8,635 | **4,400** | **49% reduction** |
| Completion tokens | 7,025 | **2,793** | **60% reduction** |
| Prompt tokens | 1,610 | **1,607** | — |
| Reasoning chars | 27,558 | **9,028** | **67% reduction** |

**Key changes:** Sentence-count validation (replaced char-count), removed Step 1 fact extraction, capped max_tokens at 6K.

## ✨ Features

- **Automated research** — scrapes Mirror RSS + Sky Sports RSS + Goal.com for fresh football news
- **Smart dedup** — URL-based, title Jaccard similarity (35% threshold), 30-min article cache
- **LLM-generated threads** — 8 slides with sentence-count blueprints and grounding rules
- **Sentence-count validation** — replaces char-count, ensures consistent slide density
- **3-level image fallback** — og:image → article body `<img>` → RSS image
- **Image validation** — HEAD request checks every image URL before use
- **Content filtering** — skips women's football (title, description, URL)
- **Strategy 2 JSON extraction** — score-based fallback for reasoning-heavy responses
- **Analytics feedback** — daily engagement analysis tunes posting hours
- **Atomic staging** — tmp + os.replace prevents corruption

## 🏗 Architecture

```
:00 Pipeline ──► scrape (Mirror + Sky Sports + Goal.com)
                  ├── filter (dedup, similarity, freshness, women's football)
                  ├── score (WC boost +50, viral +25)
                  ├── extract (article text + 3-level image fallback)
                  ├── LLM generate (deepseek-v4-flash, v7.0 prompt)
                  ├── validate (sentence count per slide)
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

### Pipeline Flow

```
pressbox-pipeline-v7.py:
  1. Parallel scrape Mirror RSS + Sky Sports RSS + Goal.com (5-10s)
  2. Filter candidates (URL dedup, Jaccard similarity 35%, 30-min cache, women's football)
  3. Score with WC boost (+50), viral keywords (+25)
  4. Pick best candidate
  5. Extract article text via curl (with 30-min cache)
  6. Extract article image:
     a. og:image meta tag → HEAD validate
     b. Article body <img> (first content image) → HEAD validate
     c. RSS image URL → HEAD validate
  7. LLM generate 8-slide JSON (deepseek-v4-flash, v7.0 sentence-count prompt)
  8. Validate sentence count per slide (retry up to 3x)
  9. Save to staging.json (atomic write)
```

### LLM Prompt (v7.0)

Sentence-count based prompt with per-slide blueprints:

```
slide_1 — HOOK (2 sentences max)
slide_2 — SPARK (4-5 sentences)
slide_3 — WHY (4-5 sentences)
slide_4 — TENSION (4-5 sentences)
slide_5 — HUMAN (3-4 sentences)
slide_6 — RIPPLE (3-4 sentences) [ANALYSIS — exempt from grounding]
slide_7 — UNRESOLVED (3-4 sentences)
slide_8 — OPINION + CTA (3-4 sentences)
```

Key rules:
- Each slide has a sentence-count range (not char count)
- slide_6 explicitly exempt from grounding rules (analysis)
- Writing rules: punchy, conversational, no em-dash/hashtags
- Grounding: all facts from article only, no invented names/quotes

### Sentence Count Targets

| Slide | Sentences | Role |
|-------|-----------|------|
| 1 (HOOK) | 1-2 | Stop scroll |
| 2 (SPARK) | 4-5 | What happened |
| 3 (WHY) | 4-5 | Why it matters |
| 4 (TENSION) | 4-5 | Conflict/stakes |
| 5 (HUMAN) | 3-4 | One person |
| 6 (RIPPLE) | 3-4 | Analysis (exempt from grounding) |
| 7 (UNRESOLVED) | 3-4 | Open question |
| 8 (CTA) | 3-4 | Opinion + url |

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
| Sentence count fail (< min) | Retry with stricter prompt |
| JSON parse failure | Strategy 1: slide markers → Strategy 2: score-based fallback |
| JSON in reasoning_content | Extract via brace counting + content length scoring |
| Image og:image fails | Fallback to body `<img>` → RSS |
| Image HEAD check fails | Skip image, proceed text-only |
| Staging guard | Skip if unposted content exists |
| All candidates fail | Exit code 1 → check-staging recovery |

## 📝 Changelog

### v7.2 — Sentence Counts + Speed (2026-06-18)
- **54% faster LLM** (67.7s → 30.9s) via prompt optimization
- Sentence-count validation (replaced char-count 200-450)
- Per-slide sentence blueprints (2-5 sentences each)
- Removed Step 1 fact extraction (saves tokens + reasoning)
- Capped max_tokens at 6K (was 10K)
- Added women's football filter (title, description, URL)
- slide_6 explicitly exempt from grounding rules

### v7.1 — Data Extraction Agent (2026-06-17)
- 9x faster LLM (120s → 13s) via prompt engineering
- Rewrote prompt as "Data Extraction Agent" role
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
