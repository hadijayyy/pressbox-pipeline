# Pressbox Pipeline

Automated football content pipeline for [@parkthebus.football](https://www.threads.net/@parkthebus.football) on Threads.

Scrapes football news from 5 sources, detects hot/viral topics via entity clustering + **Google Trends**, scores with a multi-layered engine, selects from **5 viral content patterns**, generates 6-slide carousels via LLM with anti-hallucination grounding, and posts on schedule — fully automated with engagement feedback loop.

## How It Works

```
┌──────────────────────────────────────────────────────────────┐
│  1. SCRAPE          5 sources (goal, mirror, bbc,            │
│                     fourfourtwo, skysports) — parallel       │
│       ↓                                                      │
│  2. FILTER          Commercial/TV/sensitive/women blocked    │
│                     + dedup + similarity + analytics penalty  │
│       ↓                                                      │
│  3. HOT DETECT      4h persistent cache + entity clustering  │
│                     (Union-Find) + GOOGLE TRENDS match       │
│       ↓                                                      │
│  4. PATTERN SELECT  A (Rule-Break) / C (Detail+Emotion) /   │
│                     D (Commentary) / E (Pressure Cooker) /   │
│                     F (Behind-the-Scenes)                    │
│       ↓                                                      │
│  5. SCORE           16-component data-driven engine +        │
│                     context-aware bonuses + soft cap + tune  │
│       ↓                                                      │
│  6. VERIFY          Article: 1000+ chars, 150+ words,        │
│                     8+ unique sentences. Tries top 5.       │
│       ↓                                                      │
│  7. FETCH           Extract full article text + og:image HD  │
│       ↓                                                      │
│  8. GENERATE        Mistral LLM → JSON with 6 slides +      │
│                     caption (per selected pattern arc)       │
│       ↓                                                      │
│  9. POST            Threads API (chained thread + image)     │
│       ↓                                                      │
│ 10. TRACK           posted_topics.json + hotness for A/B     │
│       ↓                                                      │
│ 11. NOTIFY          @Szejay_bot (4-line format)              │
└──────────────────────────────────────────────────────────────┘

Cron: every 80m, watchdog at :15.
```

## Viral Content Patterns

| Pattern | Style | Trigger Words | Top Performance |
|---------|-------|---------------|-----------------|
| **A — Rule-Break** | Authority violates own rule/ethos | FIFA broke, UEFA waived, IFAB ignores | **12M+** (parkthebus) |
| **C — Detail+Emotion** | Data-driven human interest | contract, sacrifice, journey, fee | ~191K |
| **D — Commentary** | Celebrity/pundit says something | slams, warns, hits out, reacts | ~403K |
| **E — Pressure Cooker** 🔥 | Player/manager under fire | NOT happy, fumes, speaks out, defiant | **634K** (Bellingham slap) |
| **F — Behind-the-Scenes** 🏗️ | Logistics, admin, VAR, ref | hotel, travel, fitness, decisions | **536K** (Norway hotel) |

Pattern selection is automatic: keyword + signal detection, not random. E and F are prioritised for post-World Cup football (pressure drama + news).

## Google Trends Integration

Every pipeline run fetches **Google Trends UK RSS** and matches trending queries against article titles:

- Football trends (player/team/transfer/match keywords) → hotness boost **+0.5~8.0**
- Non-football trends → minimal boost
- 30-min cache to avoid redundant API calls
- No API key required

## Content Filters

| Filter | What it blocks |
|--------|---------------|
| `_COMMERCIAL` | Shopping/deals: "snap up", "buy now", "% off", Amazon/eBay |
| `_TV_GUIDE` | "How to watch", "TV channel", "live stream" |
| `_SENSITIVE` | "charged with murder", "arrested", "domestic violence" |
| `_WOMEN` | Lionesses, NWSL, women's football |
| `/live/` `/quiz/` URLs | Live commentary, quiz pages (not articles) |
| Length gate | Article must be 1000+ chars, 150+ words, 8+ unique sentences |
| Body verification | Football signals ≥ 2, commercial signals < 2 |

## Scoring System

### Base Components (0–170+ pts)

| # | Component | Points | Data Source |
|---|-----------|--------|-------------|
| 1 | Keyword Match | +8/keyword (max 5 = 40) | |
| 2 | Category | 20 (transfer/match/drama) / 10 (international) / 0 | |
| 3 | Recency | 15/10/5/0 | |
| 4 | Data/Konkret | 15/7/0 | |
| 5 | Source Tier | **15** (Super: goal) / 10 (Tier 1) / 5 (Tier 2) / 0 (unknown=99) | goal avg 58K — 2.1x BBC |
| 6 | Audience Reach | +10/big name (max 40) | |
| 7 | Drama Signal | +5/word (max 10) | |
| 8 | First Ever | +20/+10 | |
| 9 | Niche Nation | -15 | |
| 10 | Paradox Bonus | +12 | |
| 11 | Warning Bonus | +8 | |
| **12** | **Star Player** | **+20** | Data: +39% above baseline |
| **13** | **Conflict Hook** | **+10** | Data: conflict avg 50.9K vs baseline 41.2K |
| 14 | Timing Urgency | **+8** (1+ hit) | |
| **15** | **Human Story** | **+20** | Data: highest engagement rate (1.5%) |
| **16** | **Low Performer Penalty** | **-15** | Data: factual/QA proven <2K |

### Pipeline Bonuses (context-aware)

| Bonus | Trigger | Points |
|-------|---------|--------|
| WC Related | Title has football context | +40 |
| WC Related (weak) | Only team name | +10 |
| Transfer Related | Transfer keywords | +10 |
| Hot Topic | Multi-source cluster (hotness ≥ 3.0) | +25 |
| Google Trends | Trending query matches article | +0.5~8.0 |
| Warm Topic | Multi-source cluster (hotness ≥ 1.5) | +15 |
| Peak Hour | 17–21 WIB + hot topic | +10 |
| Hook Boost | Best performing hooks (from analytics) | +15 |
| Topic Penalty | Worst performing topics | -20 |
| Niche Topic | boots/kit/jersey/stadium rules | -30 |
| Auto-Tuning | ML-adjusted multipliers | ±15 |

### Guards & Caps

| Guard | What it does |
|-------|-------------|
| WC context check | +40 only if title has football keywords, else +10 |
| Hot relevance check | Entity must appear in title first half |
| Niche penalty | -30 for boots/kit/jersey/stadium |
| Soft cap | Above 100: `100 + (score - 100) × 0.3` |

**Effective score range:**

```
Low-quality (boots/kit)   : 15–40
Average (preview/quiz)    : 40–65
Good (match result)       : 65–90
Hot drama (controversy)   : 90–130
Viral combo (star+conflict+human) : 130–170
```

## Hot Topic Detection

**Dual-layer detection:**

1. **Entity clustering (internal):** 4h rolling window, Union-Find by player/team entity overlap. Multi-source coverage = viral boost.
2. **Google Trends (external):** UK RSS feed matched against article titles. Football-specific queries get priority boost.

## Image Handling

| Layer | Source | Quality |
|-------|--------|---------|
| Primary | `og:image` from article HTML | 1200px (HD) |
| Fallback | RSS `<media:thumbnail>` / `<enclosure>` | 240–480px |
| BBC upscale | `ichef.bbci.co.uk/480/` → `/1024/` | 1024px |

## Sources

| Source | Method | Tier | Notes |
|--------|--------|------|-------|
| Goal.com | HTML scrape | **Super** (+15) | Avg 58K views — 2.1x any other source |
| SkySports | RSS | 1 (+10) | 24h freshness |
| BBC | RSS | 1 (+10) | Image upscale to 1024px |
| FourFourTwo | RSS | **2 (+5)** | Demoted — avg 27K, lowest engagement |
| Mirror | RSS | 2 (+5) | Fresh 0–1h |

## Content Format

6-slide Threads carousel with pattern-specific arcs:

### Pattern A — Rule-Break arc (viral parkthebus format)
- S1 = Hook: "Authority breaks own rule — specific detail — binary question"
- S2 = Specific physical detail (size, numbers, quotes)
- S3 = Lore/context (why it matters historically)
- S4 = Stakes escalation (who benefits/loses)
- S5 = Twist + source attribution
- S6 = Venue/location irony — NOT generic

### Pattern E — Pressure Cooker arc
- S1 = Hook: "[Player/Manager] not happy after [trigger]"
- S2 = Tension context
- S3 = Parties involved
- S4 = Stakes (job/transfer/board)
- S5 = Why it's unique
- S6 = Specific binary (NOT generic)

### Pattern F — Behind-the-Scenes arc
- S1 = Hook: "Why [team/authority] did [specific thing]"
- S2 = The situation
- S3 = Why it matters
- S4 = Who benefits/who loses
- S5 = The real story
- S6 = Prediction binary

### Anti-Hallucination Grounding

3-layer system:

1. **Prompt Hardening** — Rules baked into LLM system prompt
2. **Reference Data Injection** — Current date, player ages, tournament timeline
3. **Post-Gen Number Check** — Scans for every `£/$/€` amount and warns if not in article

Rules: No invented tactics, exact quotes only, flagged rumors, no speculation.

### Banned Patterns

```
You won't believe… | In today's football world… | Sources say…
This is a game-changer | Fans are furious | Shocking | Insane
Let that sink in | Say what you want, but… | you've been warned
beware | watch out | Breaking: (generic scoreline openers)
```

## Output Format

LLM returns JSON:

```json
{
  "slide_1": "...",
  "slide_2": "...",
  "slide_3": "...",
  "slide_4": "...",
  "slide_5": "...",
  "slide_6": "...",
  "caption": "...",
  "cover_image_keywords": "..."
}
```

## Engagement Feedback Loop

```
Every run:
  1. pull_engagement() — update metrics for posts >12h old
  2. get_analytics_summary() — classify hooks/topics, compute boosts
  3. Score with analytics + Google Trends + hot topic boosts
  4. Select pattern (A/C/D/E/F) based on content signals
  5. Generate with selected arc structure
  6. Post to Threads
  7. Track with hotness_score for A/B comparison
```

Feedback delay: ~12–24 hours.

## Architecture

```
~/.hermes/scripts/
  run-mvp.sh                    ← Cron entry point
  watchdog-pressbox.sh          ← Health monitor (re-runs if stale)
  pressbox-engagement-report.sh ← Daily report

~/.hermes/pressbox-pipeline/
  pressbox-mvp.py               ← Main pipeline
  pressbox_scoring.py           ← 16-component scoring engine
  pressbox_common.py            ← Shared utilities
  google_trends.py              ← Google Trends RSS fetcher

~/.hermes/pressbox/
  posted_topics.json            ← Post history + engagement + hotness
  article-cache.json            ← 4h article cache for hot detection
  .trends_cache.json            ← Google Trends 30min cache
```

## Cron

| Job | Schedule | Behavior |
|-----|----------|----------|
| Pressbox MVP | every 80m | Scrape → score → verify → generate → post |
| Pressbox Watchdog | `15 * * * *` | Re-runs pipeline if stale |
| Daily Report | `0 8 * * *` | Engagement summary via @Szejay_bot |
| Hourly Report | `0 * * * *` | Status report |

## Setup

```bash
git clone https://github.com/hadijayyy/pressbox-pipeline.git ~/.hermes/pressbox-pipeline
cd ~/.hermes/pressbox-pipeline
pip install requests beautifulsoup4 python-dotenv httpx

# API keys
echo 'MISTRAL_API_KEY=***' >> ~/.hermes/.env

# Threads token
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
