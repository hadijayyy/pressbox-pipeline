# Pressbox Pipeline

Automated football content pipeline for [@parkthebus.football](https://www.threads.net/@parkthebus.football) on Threads.

Scrapes football news from 3 sources, scores topics using a 7-component analytics engine, generates viral 6-slide threads via LLM, and posts to Threads — all fully automated with a self-improving feedback loop.

## How It Works

```
┌────────────────────────────────────────────────────────────┐
│                    PRESSBOX PIPELINE                        │
│                 (runs every 30 minutes)                     │
│                                                            │
│  1. SCRAPE          Mirror + SkySports + Goal.com          │
│       ↓                                                    │
│  2. PULL METRICS    Posts >12h old → views/likes/replies    │
│       ↓                                                    │
│  3. ANALYZE         Best hooks, topics, sources from data   │
│       ↓                                                    │
│  4. SCORE           Real data + dynamic boost/penalty       │
│       ↓                                                    │
│  5. GENERATE        LLM → 6-slide RCTOR thread             │
│       ↓                                                    │
│  6. GROUND CHECK    Verify names + stages against article   │
│       ↓                                                    │
│  7. POST            Threads API (chained thread)            │
│       ↓                                                    │
│  8. TRACK           Update posted_topics.json               │
│       ↓                                                    │
│  ────────── loop back to step 1 ──────────                 │
└────────────────────────────────────────────────────────────┘
```

### Feedback Loop

The pipeline continuously improves itself using real engagement data:

1. **Pull Metrics** — Each run fetches engagement data (views, likes, replies, shares) from the Threads Insights API for posts older than 12 hours
2. **Analyze Patterns** — Calculates average views per hook type (controversy, conflict, curiosity, event) and topic type (transfer, match_result, injury_update, etc.)
3. **Dynamic Scoring** — Proven high-engagement hook types get +15 points. Worst-performing topic types get -20 points
4. **Failure Tracking** — Posts where metrics pull fails are marked (`metrics_failed`) to avoid repeated attempts

**Feedback delay: ~12-24 hours** (post → collect metrics → next run uses real data)

## Scoring System (0–125 points)

Seven additive components, each independently capped:

| # | Component | Max | Description |
|---|-----------|-----|-------------|
| 1 | Keyword Match | 40 | +8 per unique football keyword (transfer, match, drama, international) |
| 2 | Category Relevance | 20 | transfer/match/drama = 20, international = 10 |
| 3 | Recency | 15 | <6h = 15, 6–24h = 10, 24–48h = 5 |
| 4 | Data/Specificity | 15 | Concrete data (score 3-1, fee £50m, 15%) = 15, vague digits = 7 |
| 5 | Source Tier | 10 | BBC/Sky/Goal = 10, Mirror/DailyMail = 5 |
| 6 | Audience Reach | 40 | +10 per big team/nation/star mentioned (cap 40) |
| 7 | Drama Signal | 15 | +5 per drama word in title (slams, blasts, exclusive, breaking, revealed, etc.) |
| — | **Dynamic Hook Boost** | +15 | Hook type proven high-engagement from analytics |
| — | **Dynamic Topic Penalty** | -20 | Worst-performing topic type from analytics |

**Threshold:** score >= 60 to publish.

### Hook Classification

| Hook Type | Trigger Words | Performance |
|-----------|---------------|-------------|
| controversy | slams, blasts, hits out, furious, scandal, row, rift | High |
| conflict | vs, against, clash, rival, battle, showdown | Medium-High |
| curiosity | ?, how, why, what if, can, will, could | Variable |
| event | just, dropped, lost, won, banned, sacked, arrested | High |
| statement | (default — no trigger) | Baseline |

## Content Format (RCTOR)

Each post is a 6-slide thread using the RCTOR narrative framework:

| Slide | Role | Purpose |
|-------|------|---------|
| 1 | **R – Reality** | Hook — punchy 1-2 sentence opener that stops the scroll |
| 2 | **C – Context** | The story — who, what, where |
| 3 | **T – Tension** | Why it matters, the stakes |
| 4 | **O – Outcome** | The human angle — quote, reaction, backstory |
| 5 | **R – Reflection** | Open question, cliffhanger |
| 6 | **CTA** | Call-to-action + link back to source |

**Rules:** ≤500 characters per slide, sentence case, no hashtags, no emojis in text, native platform tone.

## Sources

| Source | Method | Image | Notes |
|--------|--------|-------|-------|
| Mirror | HTML scrape | og:image (HD) | Per-article fetch |
| SkySports | RSS | media:content | 12-hour freshness filter |
| Goal.com | HTML scrape | og:image (4K) | Per-article fetch |

## Grounding Validator

A post-generation safety check that catches hallucinated content:

- **Football stages** — Stages mentioned in generated slides but not in the original article → **blocks posting**
- **Proper nouns** — Names in slides but not in article → soft warning (logged, does not block)

## Architecture

```
~/.hermes/scripts/
  run-mvp.sh              ← Cron entry point (with retry)
  watchdog-pressbox.sh    ← Auto-retry watchdog

~/.hermes/pressbox-pipeline/
  pressbox-mvp.py         ← Main pipeline (scrape, analytics, score, LLM, post)
  threads_poster.py       ← Threads Graph API wrapper + engagement metrics
  pressbox_common.py      ← Shared utilities (paths, logging, dedup, classification)
  pressbox_scoring.py     ← 7-component analytics-driven scoring engine

~/.hermes/pressbox/
  posted_topics.json      ← Post history + engagement metrics (views, likes, replies, shares)
```

## Setup

```bash
# Clone repo
git clone https://github.com/hadijayyy/pressbox-pipeline.git ~/.hermes/pressbox-pipeline
cd ~/.hermes/pressbox-pipeline
pip install -r requirements.txt

# Mistral API key
echo 'MISTRAL_API_KEY=your_key' >> ~/.hermes/.env

# Threads token (requires threads_manage_insights scope)
echo '{"access_token": "your_token", "user_id": "your_user_id"}' > ~/.hermes/threads_token.json

# Data directories
mkdir -p ~/.hermes/pressbox
echo '{"topics": []}' > ~/.hermes/pressbox/posted_topics.json

# Copy cron scripts
cp run-mvp.sh ~/.hermes/scripts/
cp watchdog.sh ~/.hermes/scripts/watchdog-pressbox.sh
```

## Usage

```bash
# Dry run (scrape + generate, no posting)
python3 -u pressbox-mvp.py --dry-run

# Live run
bash run-mvp.sh
```

## Cron Setup

Runs via Hermes `no_agent: true` cron jobs — zero token cost, direct script execution on host.

| Job | Schedule | Script | Behavior |
|-----|----------|--------|----------|
| Pressbox MVP | `0,30 * * * *` | `run-mvp.sh` | Scrape → pull metrics → score → generate → post |
| Pressbox Watchdog | `15,45 * * * *` | `watchdog-pressbox.sh` | Silent if OK, auto-retry if fail/stale |

Built-in **15-minute cooldown** prevents duplicate posts.

## Threads API Requirements

| Scope | Purpose |
|-------|---------|
| `threads_basic` | Read profile, list posts |
| `threads_content_publish` | Create and publish posts |
| `threads_manage_insights` | Pull engagement metrics (views, likes, replies, shares) |

Token stored at `~/.hermes/threads_token.json`. Exchange short-lived token → long-lived token via [Meta Developer Dashboard](https://developers.facebook.com/).

## Requirements

- Python 3.8+
- `requests`, `beautifulsoup4`, `python-dotenv`, `feedparser`
- Mistral API key (`~/.hermes/.env`)
- Threads long-lived access token with `threads_manage_insights` scope
