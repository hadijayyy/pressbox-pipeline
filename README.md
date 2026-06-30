# Pressbox Pipeline

Automated football content pipeline for [@parkthebus.football](https://www.threads.net/@parkthebus.football) on Threads.

Scrapes football news from 3 sources, scores topics using a 9-component analytics engine, generates viral 6-slide threads via LLM, and posts to Threads — fully automated with anti-bot randomization and self-improving feedback loop.

## How It Works

```
┌────────────────────────────────────────────────────────────┐
│                    PRESSBOX PIPELINE                        │
│           (fires every 30 min, posts ~every 50-90 min)     │
│                                                            │
│  0. ANTI-BOT CHECK   Random gap 50-90 min + random sleep   │
│       ↓                                                    │
│  1. SCRAPE           SkySports (RSS) + Goal.com + FFT      │
│       ↓                                                    │
│  2. PULL METRICS     Posts >12h old → views/likes/replies   │
│       ↓                                                    │
│  3. ANALYZE          Best hooks, topics, sources from data  │
│       ↓                                                    │
│  4. SCORE            9-component engine (threshold ≥40)     │
│       ↓                                                    │
│  5. CANNIBALIZE      Skip duplicate topics (keep highest)   │
│       ↓                                                    │
│  6. GENERATE         Mistral LLM → 6-slide thread           │
│       ↓                                                    │
│  7. GROUND CHECK     Verify names + stages against article  │
│       ↓                                                    │
│  8. POST             Threads API (chained thread)           │
│       ↓                                                    │
│  9. TRACK            posted_topics.json + summary report    │
│       ↓                                                    │
│  ────────── loop back to step 0 ──────────                 │
└────────────────────────────────────────────────────────────┘
```

### Anti-Bot Randomization

Two-layer system prevents predictable posting patterns:

1. **Layer 1 — Random gap check:** Each run rolls a random gap (50-90 min). If elapsed time since last post < gap, skip silently.
2. **Layer 2 — Random sleep:** When gap check passes, sleep 0-20 min before posting. Breaks exact `:00`/`:30` cron grid.

**Result:** ~15 posts/day, intervals range 50-130 min, never same pattern. 11 unique intervals per 13 posts (human-like).

### Feedback Loop

1. **Pull Metrics** — Each run fetches engagement data (views, likes, replies, shares) from Threads Insights API for posts older than 12 hours
2. **Analyze Patterns** — Calculates average views per hook type and topic type
3. **Dynamic Scoring** — Proven high-engagement hook types get +15 points. Worst-performing topic types get -20 points

**Feedback delay: ~12-24 hours** (post → collect metrics → next run uses real data)

## Scoring System (0–145 points)

Nine additive components, each independently capped:

| # | Component | Max | Description |
|---|-----------|-----|-------------|
| 1 | Keyword Match | 40 | +8 per unique football keyword |
| 2 | Category Relevance | 20 | transfer/match/drama = 20, international = 10 |
| 3 | Recency | 15 | <6h = 15, 6–24h = 10, 24–48h = 5 |
| 4 | Data/Specificity | 15 | Concrete data (score 3-1, fee £50m) = 15 |
| 5 | Source Tier | 10 | SkySports/Goal/FFT = 10 |
| 6 | Audience Reach | 40 | +10 per big team/nation/star (cap 40) |
| 7 | Drama Signal | 15 | +5 per drama word (slams, blasts, breaking, etc.) |
| 8 | **"First ever" + Stat Boost** | +20 | "first player/team to..." + specific number |
| 9 | **Niche Nation Penalty** | -15 | Hong Kong, DR Congo, etc. (low audience ceiling) |
| — | Dynamic Hook Boost | +15 | Hook type proven high-engagement from analytics |
| — | Dynamic Topic Penalty | -20 | Worst-performing topic type from analytics |

**Threshold:** score ≥ 40 to publish.

## Content Format

Each post is a 6-slide Threads carousel. Style: conversational, witty, die-hard football fan tone with slang/banter ("cooked", "benched", "baller").

| Slide | Role | Purpose |
|-------|------|---------|
| 1 | **THE HOOK** | 1-2 sentence opener. Curiosity gap > controversy > conflict (gated on topic size) |
| 2 | **THE CONTEXT** | Connect hook to news. Why fans should care. 40-60 words |
| 3 | **THE CORE FACT/STAT** | Most shocking stat broken down simply. 40-60 words |
| 4 | **THE IMPACT** | Ripple effect on team/league/season. 40-60 words |
| 5 | **THE VERDICT** | Sharp, definitive, cinematic. Leaves room for debate. 30-50 words |
| 6 | **THE CTA** | Debate question that forces a side. URL appended automatically. 30-40 words |

### Winning Hook Pattern (75K views proven)

```
"X just became the first Y to do Z after [specific stat] — and nobody's talking about [scandal]."
```

- **"Nobody's talking about"** only for big names (WC, CL, Premier League)
- **Niche topics** forced to direct conflict/hot take instead
- **Em-dash (—)** used for drama and emphasis

### Grounding Validator

Post-generation safety check:

- **Football stages** mentioned in slides but not in article → **blocks posting**
- **Proper nouns** in slides but not in article → soft warning (logged)

## Sources

| Source | Method | Image | Notes |
|--------|--------|-------|-------|
| SkySports | RSS | media:content | 24h freshness filter |
| Goal.com | HTML scrape | og:image (4K) | Direct scrape |
| FourFourTwo | RSS | og:image (HD) | Tier 1 source |

## Architecture

```
~/.hermes/scripts/
  run-mvp.sh              ← Cron entry point (anti-bot + retry + delivery)
  watchdog-pressbox.sh    ← Health monitor

~/.hermes/pressbox-pipeline/
  pressbox-mvp.py         ← Main pipeline (scrape, analytics, score, LLM, post)
  threads_poster.py       ← Threads Graph API wrapper + engagement metrics
  pressbox_common.py      ← Shared utilities (paths, logging, dedup, classification)
  pressbox_scoring.py     ← 9-component analytics-driven scoring engine

~/.hermes/pressbox/
  posted_topics.json      ← Post history + engagement metrics
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
echo '{"access_token": "***", "user_id": "your_user_id"}' > ~/.hermes/threads_token.json

# Data directories
mkdir -p ~/.hermes/pressbox
echo '{"topics": []}' > ~/.hermes/pressbox/posted_topics.json
mkdir -p ~/.hermes/content-pipeline/drafts/football

# Copy cron scripts
cp scripts/run-mvp.sh ~/.hermes/scripts/
cp scripts/watchdog-pressbox.sh ~/.hermes/scripts/
```

## Usage

```bash
# Dry run (scrape + generate, no posting)
python3 -u pressbox-mvp.py --dry-run

# Live run
bash ~/.hermes/scripts/run-mvp.sh
```

## Cron Setup

Runs via Hermes `no_agent: true` cron jobs — zero token cost, direct script execution.

| Job | Schedule | Script | Behavior |
|-----|----------|--------|----------|
| Pressbox MVP | `0,30 * * * *` | `run-mvp.sh` | Anti-bot check → scrape → score → generate → post |
| Pressbox Watchdog | `15,45 * * * *` | `watchdog-pressbox.sh` | Health monitor, silent if OK |

**Anti-bot timing:** Cron fires every 30 min, but actual posting is ~50-90 min apart (randomized). ~15 posts/day.

**Cooldown:** 15-minute floor in Python prevents duplicate posts.

## Threads API Requirements

| Scope | Purpose |
|-------|---------|
| `threads_basic` | Read profile, list posts |
| `threads_content_publish` | Create and publish posts |
| `threads_manage_insights` | Pull engagement metrics |

Token stored at `~/.hermes/threads_token.json`.

## Requirements

- Python 3.8+
- `requests`, `beautifulsoup4`, `python-dotenv`
- Mistral API key (`~/.hermes/.env`)
- Threads long-lived access token with `threads_manage_insights` scope
