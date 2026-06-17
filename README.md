# 📰 Press Box — Automated Football Content Pipeline

**Auto-publish football news threads to Threads (@parkthebus.football)**

Press Box is a fully automated content pipeline that scrapes football news (Guardian, Mirror, Sky Sports), generates 8-slide threads via LLM (deepseek-v4-flash), and posts them to Threads with relevant images — completely unattended.

## ✨ Features

- **Automated research** — scrapes Guardian RSS + Mirror + Sky Sports for fresh football news
- **Smart dedup** — URL-based, title Jaccard similarity (35% threshold), 30-min scrape cache
- **LLM-generated threads** — 8 slides with hook, storytelling arc, and CTA question
- **3-level image fallback** — og:image → article body `<img>` → RSS image
- **Image validation** — HEAD request checks every image URL before use
- **Whitespace formatting** — blank line every 2 sentences for readability
- **Fallback loop** — tries up to 3 LLM retries with char count validation (250-450 chars)
- **Analytics feedback** — daily engagement analysis tunes posting hours
- **CTA detection** — traverses nested reply chains to detect slide 8 questions
- **Atomic staging** — tmp + os.replace prevents corruption

## 🏗 Architecture

```
:00 Pipeline ──► scrape (Guardian + Mirror + Sky Sports)
                  ├── filter (dedup, similarity, freshness)
                  ├── score (WC boost +50, viral +25)
                  ├── extract (article text + 3-level image fallback)
                  ├── LLM generate (deepseek-v4-flash, 8 slides JSON)
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
OPENCODE_GO_API_KEY=your_opencode_go_api_key
```

### Threads API Token Setup

1. Create a Meta App with Threads API enabled
2. Generate a long-lived access token
3. Save to `~/.hermes/threads_token.json`:
```json
{
  "access_token": "your_token",
  "user_id": "your_user_id"
}
```

## 📁 Scripts

| Script | Description |
|--------|-------------|
| `pressbox-research.py` | RSS scraper — Guardian, Mirror, Sky Sports extraction |
| `pressbox-pipeline-v7.py` | **Main pipeline** — scrape → filter → score → extract → LLM → stage |
| `pressbox-check-staging.py` | Recovery job — runs pipeline if staging is empty |
| `pressbox-post.py` | Post manager — reads staging, calls direct-post, updates tracking |
| `pressbox-direct-post.py` | Low-level Threads Graph API client — IMAGE/TEXT container creation |
| `pressbox-analytics-feedback.py` | Daily analytics — topic boosts, best/worst hours |
| `pressbox-analytics-llm.py` | LLM deep analysis — hooks, CTA, topic recommendations |

### Pipeline Flow (v7)

```
pressbox-pipeline-v7.py:
  1. Parallel scrape Guardian RSS + Mirror + Sky Sports (5-10s)
  2. Filter candidates (URL dedup, Jaccard similarity 35%, 30-min cache)
  3. Score with WC boost (+50), viral keywords (+25)
  4. Pick best candidate
  5. Extract article text via curl
  6. Extract article image:
     a. og:image meta tag → HEAD validate
     b. Article body <img> (first content image) → HEAD validate
     c. RSS image URL → HEAD validate
  7. LLM generate 8-slide JSON (deepseek-v4-flash, reasoning_effort=low)
  8. Retry up to 3x if char count fails (250-450 chars per slide)
  9. Save to staging.json (atomic write)
```

### LLM Prompt Standards

- **Hook (Slide 1):** 1-2 punchy sentences. 250-450 chars. Include image URL.
- **Storytelling (Slides 2-7):** Problem → Context → Comparison → Human Angle → Big Picture → Stakes.
- **CTA (Slide 8):** Provocative debate question with `?` + personal word (you/we/fans). 3 sentences + source URL.
- **Formatting:** Blank line between every 2 sentences for readability.

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
| Guardian | ✅ 200 | ✅ 200 | ✅ 200 | Works |
| Mirror | ✅ 200 | ✅ 200 | ✅ 200 | Works |
| Sky Sports | ✅ 200 | ✅ 200 | ✅ 200 | Works |
| BBC Sport | ✅ 200 | ✅ 200 | - | Works |
| ESPN | ❌ 403 | ❌ 403 | - | Blocked |
| Reuters | ❌ 401 | ❌ 401 | - | Blocked |

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
| `~/.hermes/pressbox/scrape_cache.json` | 30-min URL scrape cache |
| `~/.hermes/pressbox/analytics_feedback.json` | Topic boosts from analytics |
| `~/.hermes/pressbox/analytics_recommendations.json` | LLM analysis results |
| `~/.hermes/threads_token.json` | Threads API token |
| `~/.hermes/.env` | API keys |

## 🛡️ Error Handling

| Issue | Handling |
|-------|----------|
| URL fails | Skip candidate, try next |
| LLM timeout | Retry up to 3x |
| LLM short response (<250 chars) | Retry with stricter prompt |
| JSON parse failure | Extract from reasoning_content (brace counting) |
| Image og:image fails | Fallback to body `<img>` → RSS |
| Image HEAD check fails | Skip image, proceed text-only |
| Staging guard | Skip if unposted content exists |
| All candidates fail | Exit code 1 → check-staging recovery |

## 🤝 Contributing

PRs welcome! Focus areas:
- Additional sources (BBC, The Athletic)
- Multi-platform support (X/Twitter)
- A/B testing module

## 📄 License

MIT

---

*Built with ❤️ for @parkthebus.football*
