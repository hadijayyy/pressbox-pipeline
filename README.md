# Pressbox Pipeline

Automated football content pipeline for [@parkthebus.football](https://www.threads.net/@parkthebus.football) on Threads.

Scrapes football news from 5 sources, detects hot/viral topics via entity clustering, scores with a multi-layered engine, generates 6-slide carousels via LLM with anti-hallucination grounding, and posts hourly with HD images — fully automated with engagement feedback loop.

## How It Works

```
┌──────────────────────────────────────────────────────────────┐
│  1. SCRAPE          5 sources (skysports, goal, bbc,         │
│                     fourfourtwo, mirror) — parallel          │
│       ↓                                                      │
│  2. FILTER          Commercial/TV/sensitive/women blocked    │
│                     + dedup + similarity + analytics penalty  │
│       ↓                                                      │
│  3. HOT DETECT      4h persistent cache + entity clustering  │
│                     (Union-Find). Multi-source = viral boost │
│       ↓                                                      │
│  4. SCORE           12-component engine + context-aware      │
│                     bonuses + soft cap + auto-tuning         │
│       ↓                                                      │
│  5. VERIFY          Article: 1000+ chars, 150+ words,       │
│                     8+ unique sentences. Tries top 5.       │
│       ↓                                                      │
│  6. FETCH           Extract full article text + og:image HD  │
│       ↓                                                      │
│  7. GENERATE        Mistral LLM → JSON with 6 slides +      │
│                     caption + hashtags                       │
│       ↓                                                      │
│  8. POST            Threads API (chained thread + image)     │
│       ↓                                                      │
│  9. TRACK           posted_topics.json + hotness for A/B     │
│       ↓                                                      │
│ 10. NOTIFY          @Szejay_bot (4-line format)              │
└──────────────────────────────────────────────────────────────┘

Cron: every hour (skip 6, 8, 15, 21-22 WIB), watchdog at :15.
```

## Content Filters

| Filter | What it blocks |
|--------|---------------|
| `_COMMERCIAL` | Shopping/deals: "snap up", "buy now", "% off", Amazon/eBay |
| `_TV_GUIDE` | "How to watch", "TV channel", "live stream" |
| `_SENSITIVE` | "charged with murder", "arrested", "domestic violence" |
| `_WOMEN` | Lionesses, NWLS, women's football |
| `/live/` URLs | Live commentary pages (not articles) |
| Length gate | Article must be 1000+ chars, 150+ words, 8+ unique sentences |
| Body verification | Football signals ≥ 2, commercial signals < 2 |

## Scoring System

### Base Components (0–120 pts)

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
| 12 | Exclude Keywords | -1 (hard reject) |

### Pipeline Bonuses (context-aware)

| Bonus | Trigger | Points |
|-------|---------|--------|
| WC Related | Title has football context (match/goal/win) | +40 |
| WC Related (weak) | Only mentions team name | +10 |
| Transfer Related | Transfer keywords | +10 |
| Hot Topic | Multi-source cluster (hotness ≥ 3.0) | +25 |
| Warm Topic | Multi-source cluster (hotness ≥ 1.5) | +15 |
| Peak Hour | 17–21 WIB + hot topic | +10 |
| Hook Boost | Best performing hooks (from analytics) | +15 |
| Topic Penalty | Worst performing topics (from analytics) | -20 |
| Niche Topic | boots/kit/jersey/stadium rules | -30 |
| Auto-Tuning | ML-adjusted multipliers | ±15 |

### Guards & Caps

| Guard | What it does |
|-------|-------------|
| WC context check | +40 only if title has football keywords (match/beat/win/goal), else +10 |
| Hot relevance check | Entity must appear in title first half (prevents cluster pollution) |
| Niche penalty | -30 for boots/kit/jersey/stadium/ticket keywords |
| Soft cap | Above 100: `100 + (score - 100) × 0.3` — prevents runaway scores |

**Effective score range:**
```
Low-quality (boots/kit)   : 30–50
Average (preview/quiz)    : 50–70
Good (match result)       : 70–90
Hot drama (controversy)   : 90–110 (capped)
```

## Hot Topic Detection

4h rolling window with persistent article cache. Articles accumulate across runs (~80–120 over 4h vs ~20 per run).

**Algorithm:** Union-Find clustering by entity overlap:
- 2+ shared entities (teams, players, managers) → same cluster
- 1 entity + 4+ title words overlap → same cluster
- Score: `article_count × source_tier × recency`
- Cluster entities stored per URL for relevance checking

Accent normalization: `Mbappé` → `mbappe` via `unicodedata` so French/Spanish/Portuguese names cluster correctly.

## Image Handling

| Layer | Source | Quality |
|-------|--------|---------|
| Primary | `og:image` from article HTML | 1200px (HD) ✅ |
| Fallback | RSS `<media:thumbnail>` / `<enclosure>` | 240–480px |
| BBC upscale | `ichef.bbci.co.uk/480/` → `/1024/` | 1024px ✅ |

**Always prefers og:image (HD) over RSS thumbnail.** Cache stores and returns cached_image on reuse.

## Sources

| Source | Method | Tier | Notes |
|--------|--------|------|-------|
| SkySports | RSS | 1 | 24h freshness |
| Goal.com | HTML scrape | 1 | Direct homepage scrape |
| BBC | RSS | 1 | Image upscale to 1024px |
| FourFourTwo | RSS | 1 | |
| Mirror | RSS | 2 | Fresh 0–1h |

## Content Format

6-slide Threads carousel. Football Drama Prompt v1.0 — casual audience, drama-first tone.

| Slide | Role |
|-------|------|
| 1 | **Hook** — conflict, shock, or stakes (with HD image) |
| 2 | **Story Beat** — setup/context |
| 3 | **Story Beat** — turning point |
| 4 | **Story Beat** — cost/consequence |
| 5 | **Take** — grounded opinion/analysis |
| 6 | **Closing + CTA** — question + source URL |

Plus: **caption** (1 provocative sentence) + **hashtags** (max 1).

### Anti-Hallucination Grounding

All slides follow these rules:

1. **No invented tactical reasoning** — don't attribute strategic intent unless article states it
2. **No exaggerated paraphrasing** — preserve uncertainty and tone of original
3. **No speculative consequences** — only state consequences the article mentions
4. **Quotes must be exact** — word-for-word, or clearly marked as indirect speech
5. **Rumors flagged** — "according to reports" / "still unconfirmed" when applicable

### One Story Rule

If the article covers multiple matches/storylines, pick ONE. No roundup carousels.

### Banned Patterns

```
You won't believe... | In today's football world... | Sources say...
This is a game-changer | Fans are furious | Shocking | Insane
Let that sink in | Say what you want, but... | you've been warned
beware | watch out | Breaking: (generic scoreline openers)
```

## Output Format

LLM returns JSON (with plain text fallback):

```json
{
  "slide_1": "...",
  "slide_2": "...",
  "slide_3": "...",
  "slide_4": "...",
  "slide_5": "...",
  "slide_6": "...",
  "caption": "...",
  "hashtags": "#..."
}
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

Feedback delay: ~12–24 hours (post → collect metrics → next run uses real data).

## Architecture

```
~/.hermes/scripts/
  run-mvp.sh                 ← Cron entry point
  watchdog-pressbox.sh       ← Health monitor (re-runs if stale)
  pressbox-engagement-report.sh ← Daily report

~/.hermes/pressbox-pipeline/
  pressbox-mvp.py            ← Main pipeline
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
| Pressbox MVP | `0 0-5,7,9-14,16-20,23 * * *` | Scrape → score → verify → generate → post |
| Pressbox Watchdog | `15 0-5,7,9-14,16-20,23 * * *` | Re-runs pipeline if stale |
| Daily Report | `0 8 * * *` | Engagement summary → @Szejay_bot |
| Clean Cache | `0 5 * * *` | Purge expired entries |
| Auto-Tuning | Per-run | ML adjusts multipliers from 180+ posts |

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
