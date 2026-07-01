# Pressbox Pipeline

Automated football content pipeline for [@parkthebus.football](https://www.threads.net/@parkthebus.football) on Threads.

Scrapes football news from 5 sources, detects hot/viral topics via entity clustering, scores with a 12-component engine, generates 6-slide carousels via LLM, and posts hourly — fully automated with engagement feedback loop.

## How It Works

```
┌──────────────────────────────────────────────────────────────┐
│  1. SCRAPE          5 sources (skysports, goal, bbc,         │
│                     fourfourtwo, mirror) — parallel          │
│       ↓                                                      │
│  2. HOT DETECT      4h persistent cache + entity clustering  │
│                     (Union-Find). Multi-source = +25 boost   │
│       ↓                                                      │
│  3. SCORE           12-component engine + analytics boost    │
│                     Threshold: ≥40                            │
│       ↓                                                      │
│  4. FETCH           Extract article text + image             │
│       ↓                                                      │
│  5. GENERATE        Mistral LLM → 6-slide carousel          │
│       ↓                                                      │
│  6. POST            Threads API (chained thread)             │
│       ↓                                                      │
│  7. TRACK           posted_topics.json + hotness for A/B     │
│       ↓                                                      │
│  8. NOTIFY          @Szejay_bot (4-line format)              │
└──────────────────────────────────────────────────────────────┘

Cron: every hour (0 * * * *), watchdog at :15 re-runs if stale.
```

## Hot Topic Detection

4h rolling window with persistent article cache. Articles accumulate across runs (~80-120 over 4h vs ~20 per run).

**Algorithm:** Union-Find clustering by entity overlap:
- 2+ shared entities (teams, players, managers) → same cluster
- 1 entity + 4+ title words overlap → same cluster
- Score: `article_count × source_tier × recency`
- Boost: +25 (hotness ≥ 3.0) or +15 (≥ 1.5)

```
Example: "Mbappé hat-trick" appears in BBC + SkySports + Goal
  → 3 sources × 1.5 tier × 1.0 recency = 4.5 → +25 boost
  → Auto-selected as best topic
```

Accent normalization: `Mbappé` → `mbappe` via `unicodedata` so French/Spanish/Portuguese names cluster correctly.

## Scoring System (12 components, 0–157 pts)

| # | Component | Points |
|---|-----------|--------|
| 1 | Keyword Match | +8/keyword (max 5 = 40) |
| 2 | Category | 20/10/0 |
| 3 | Recency | 15/10/5/0 |
| 4 | Data/Konkret | 15/7/0 |
| 5 | Source Tier | 10/5/0 |
| 6 | Audience Reach | +10/big name (max 40) |
| 7 | Drama Signal | +5/word (max 15) |
| 8 | First Ever | +20/+10 |
| 9 | Niche Nation | -15 |
| 10 | Paradox Bonus | +12 |
| 11 | Warning Bonus | +8 |
| 12 | **Hot Topic** | +25/+15 |
| — | Analytics Boost | +15 (top hooks), -20 (worst topics) |

## Sources

| Source | Method | Tier | Image | Notes |
|--------|--------|------|-------|-------|
| SkySports | RSS | 1 | media:content | 24h freshness |
| Goal.com | HTML scrape | 1 | og:image | Direct homepage scrape |
| BBC | RSS | 1 | media:thumbnail → upscale 1024px | og:image fallback |
| FourFourTwo | RSS | 1 | og:image | |
| Mirror | RSS | 2 | media:content | Fresh 0-1h |

## Content Format

6-slide Threads carousel. Conversational football fan tone.

| Slide | Role |
|-------|------|
| 1 | **THE HOOK** — curiosity gap > controversy > conflict |
| 2 | **THE CONTEXT** — why fans should care |
| 3 | **THE CORE FACT/STAT** — most shocking stat |
| 4 | **THE IMPACT** — ripple effect on team/league |
| 5 | **THE VERDICT** — sharp, definitive take |
| 6 | **THE CTA** — debate question + URL |

### Viral Hook Patterns (75K views proven)

**Pattern A** — "Nobody's talking about":
```
X just became the first Y to do Z after [stat] — and nobody's talking about [scandal].
```

**Pattern B** — "While + Warning":
```
X just became the first Y in [tournament] history to [achievement] — while [paradox]. [Big team], you've been warned.
```

## Engagement Feedback Loop

```
Every run:
  1. pull_engagement() — update metrics for posts >12h old
  2. get_analytics_summary() — classify hooks/topics, compute boosts
  3. Score with analytics + hot topic boosts
  4. Post new content
  5. Track hotness_score for A/B comparison
```

Feedback delay: ~12-24 hours (post → collect metrics → next run uses real data).

## Architecture

```
~/.hermes/scripts/
  run-mvp.sh                 ← Cron entry point
  watchdog-pressbox.sh       ← Health monitor (re-runs if stale)
  pressbox-engagement-report.sh ← Daily report

~/.hermes/pressbox-pipeline/
  pressbox-mvp.py            ← Main pipeline (~1070 lines)
  pressbox_scoring.py        ← 12-component scoring engine
  threads_poster.py          ← Threads Graph API wrapper
  pressbox_common.py         ← Shared utilities

~/.hermes/pressbox/
  posted_topics.json         ← Post history + engagement + hotness
  article-cache.json         ← 4h article cache for hot detection
```

## Cron

| Job | Schedule | Behavior |
|-----|----------|----------|
| Pressbox MVP | `0 * * * *` | Scrape → score → generate → post |
| Pressbox Watchdog | `15 * * * *` | Re-runs pipeline if stale |
| Daily Report | `0 8 * * *` | Engagement summary → @Szejay_bot |

## Setup

```bash
git clone https://github.com/hadijayyy/pressbox-pipeline.git ~/.hermes/pressbox-pipeline
cd ~/.hermes/pressbox-pipeline
pip install requests beautifulsoup4 python-dotenv httpx

# API keys
echo 'MISTRAL_API_KEY=***' >> ~/.hermes/.env

# Threads token (requires threads_manage_insights scope)
echo '{"access_token": "***", "user_id": "your_user_id"}' > ~/.hermes/threads_token.json

# Data directories
mkdir -p ~/.hermes/pressbox
echo '{"topics": []}' > ~/.hermes/pressbox/posted_topics.json
```

## Usage

```bash
# Dry run (scrape + generate, no posting)
python3 -u pressbox-mvp.py --dry-run

# Live run
bash ~/.hermes/scripts/run-mvp.sh
```

## Threads API Scopes

| Scope | Purpose |
|-------|---------|
| `threads_basic` | Read profile, list posts |
| `threads_content_publish` | Create and publish posts |
| `threads_manage_insights` | Pull engagement metrics |

## Requirements

- Python 3.8+
- `requests`, `beautifulsoup4`, `python-dotenv`, `httpx`
- Mistral API key
- Threads long-lived access token with `threads_manage_insights` scope
