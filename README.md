# 📰 Press Box — Automated Football Content Pipeline

**Auto-publish football news threads to Threads (@parkthebus.football)**

Press Box is a fully automated content pipeline that scrapes football news (Guardian, Mirror), generates 8-slide threads via LLM (mimo-v2.5), and posts them to Threads with relevant images — completely unattended.

## ✨ Features

- **Automated research** — scrapes Guardian RSS + Mirror for fresh football news
- **Smart dedup** — URL-based, title Jaccard similarity (35% threshold), description dedup, topic overlap filter
- **LLM-generated threads** — 8 slides with hook (two-fragment format), storytelling arc, and CTA question
- **Image support** — automatically extracts and attaches article images (RSS `media:content` + `og:image` fallback)
- **Fallback loop** — tries up to 5 article candidates if URL/LLM fails
- **Staging recovery** — check-staging job auto-retries if pipeline fails
- **Analytics feedback** — daily engagement analysis tunes topic scoring
- **Robust error handling** — transient API retry, cookie page detection, garbage article detection, graceful image → text fallback

## 🏗 Architecture

```
                  ┌──────────────┐
                  │  Guardian    │
                  │  RSS + HTML  │────┐
                  └──────────────┘    │
                                      │
                  ┌──────────────┐    ├──► pressbox-research.py ──► pressbox-pipeline-v2.py
                  │  Mirror      │    │    (RSS scraper)            (merged pipeline:
                  │  HTML scrape │────┘    15 KB, ~5s)               research + filter +
                  └──────────────┘                                     score + generate)
                                                                          │
┌──────────────────────────────────────────────────────────────────────────┘
│
├── Staging (staging.json) — 8 slides content + image_url
│
├── pressbox-post.py — reads staging, posts via Threads API
│   └── pressbox-direct-post.py — low-level IMAGE/TEXT container API
│
└── pressbox-analytics-feedback.py — daily engagement analysis
```

### Cron Schedule

| Time | Script | Purpose |
|------|--------|---------|
| `:15` | `pressbox-pipeline-v2.py` | Pipeline run (silent) |
| `:25` | `pressbox-check-staging.py` | Recovery if :15 failed (silent) |
| `:35` | `pressbox-post.py` | Post to Threads (notifies user) |
| `23:00` | `pressbox-analytics-feedback.py` | Daily analytics |

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- Threads API access token (Meta Graph API)
- OpenCode Go API key (or any OpenAI-compatible LLM endpoint)
- Telegram Bot Token (for error alerts)

### Installation

```bash
git clone https://github.com/hadijayyy/pressbox-pipeline.git
cd pressbox-pipeline
pip install httpx requests newspaper3k lxml_html_clean python-dateutil
```

### Configuration

Copy `.env.example` to `~/.hermes/.env`:

```bash
cp .env.example ~/.hermes/.env
```

Edit with your credentials:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
OPENCODE_GO_API_KEY=your_opencode_go_api_key
THREADS_ACCESS_TOKEN=your_threads_graph_api_token
THREADS_USER_ID=your_threads_user_id
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
| `pressbox-research.py` | RSS scraper — fetches Guardian RSS + Mirror HTML, extracts title/url/description/image |
| `pressbox-pipeline-v2.py` | **Main pipeline** — scrape → filter → score → LLM generate → save staging |
| `pressbox-check-staging.py` | Recovery job — runs pipeline if staging is empty |
| `pressbox-post.py` | Post manager — reads staging, calls direct-post, updates tracking |
| `pressbox-direct-post.py` | Low-level Threads Graph API client — IMAGE/TEXT container creation + publish |
| `pressbox-analytics-feedback.py` | Daily analytics — fetches last 20 posts, calculates topic boosts |

### Pipeline Flow

```
pressbox-pipeline-v2.py:
  1. Parallel scrape Guardian RSS + Mirror (4-6s)
  2. Filter candidates (URL dedup, Jaccard similarity 35%, topic overlap, freshness 12h)
  3. Score with WC boost (+50), viral keywords (+15), analytics feedback boost
  4. Weighted random pick from top 5 candidates
  5. Fallback loop: try up to 5 candidates if URL/extract/LLM fails
  6. Verify URL, extract article (newspaper3k → curl)
  7. Extract article image (RSS media:content → og:image)
  8. LLM generate 8-slide JSON (mimo-v2.5, 90s timeout)
  9. Validate slides (CTA 2+ sentences, slide 8 title ends with ?, URL cleanup)
  10. Save to staging.json
```

### LLM Prompt Standards

- **Hook (Slide 1):** Two-fragment format. `[NUMBER] [Context]. [Number/Year] [Drama].` — NO verbs, NO full sentences. Max 8 words.
- **Storytelling (Slides 2-7):** Problem → Context → Comparison → Human Angle → Big Picture → Stakes. Each slide starts where previous ended.
- **CTA (Slide 8):** Provocative debate question with personal word (you/we/fans). Content: exactly 3 sentences + source URL.

## 🖼️ Image Support

The pipeline automatically attaches the article's main image to the **first slide** of every thread:

1. **Primary:** RSS `media:content` URL (Guardian — signed, always accessible)
2. **Fallback:** `og:image` meta tag from article HTML
3. **No image:** Graceful fallback to TEXT-only mode

**All errors are caught** — if image container creation fails at the API level, it falls back to TEXT with zero impact on the thread.

### Supported Image Sources

| Source | Method | Status |
|--------|--------|--------|
| Guardian | RSS `<media:content>` (signed URL) | ✅ Works |
| Mirror | HTML `og:image` meta tag | ✅ Works |

## 📊 Analytics Feedback Loop

At 23:00 daily, `pressbox-analytics-feedback.py`:
- Fetches last 20 posts via Threads API
- Calculates avg engagement per topic type (world_cup, transfer, controversy, etc.)
- Generates topic_boosts (1.5x-3x for high performers, 0.3x penalty for low)
- Saves to `analytics_feedback.json` — read by pipeline on next run

### Best Engagement Hours (from analytics)

Based on 20+ posts analysis: **7, 18, 20 WIB**

## 🔧 Data Files

| Path | Purpose |
|------|---------|
| `~/.hermes/pressbox/staging.json` | Pipeline output → Post input |
| `~/.hermes/pressbox/posted_topics.json` | URL + title dedup tracking |
| `~/.hermes/pressbox/scrape_cache.json` | 30-min URL scrape cache |
| `~/.hermes/pressbox/analytics_feedback.json` | Topic boosts from analytics |
| `~/.hermes/threads_token.json` | Threads API token |
| `~/.hermes/.env` | API keys |

## 🛡️ Error Handling

| Issue | Handling |
|-------|----------|
| URL fails | Skip candidate, try next in fallback loop |
| Garbage article (CSS/cookie page) | Detect via keyword indicators → skip |
| LLM timeout | Retry once, then skip candidate |
| LLM short response (<200 chars) | Retry once |
| JSON parse failure | Auto-repair braces, retry |
| CTA too short (<2 sentences) | Reject → retry LLM |
| Image container fails | Auto fallback to TEXT |
| Threads API transient error | Retry 3x with exponential backoff |
| All candidates fail | Exit code 1 → check-staging recovery at :25 |

## 🤝 Contributing

PRs welcome! Focus areas:
- Additional sources (BBC, ESPN, The Athletic)
- Multi-platform support (X/Twitter)
- Follower count tracking
- A/B testing module (vary slide count, hook format)

## 📄 License

MIT

---

*Built with ❤️ for @parkthebus.football*
