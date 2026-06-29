# Pressbox Pipeline — MVP

Automated football content pipeline for [@parkthebus.football](https://www.threads.net/@parkthebus.football) on Threads.

**One script. Scrape → Score → Generate → Post → Collect Metrics → Loop.**

## Architecture

```
~/.hermes/scripts/
  run-mvp.sh              ← Cron entry point (with retry)
  watchdog-pressbox.sh    ← Auto-retry watchdog

~/.hermes/pressbox-pipeline/
  pressbox-mvp.py         ← Main pipeline (scrape, analytics, score, LLM generate, post)
  threads_poster.py       ← Threads Graph API wrapper + engagement metrics
  pressbox_common.py      ← Shared utils (paths, logging, dedup, classification)
  pressbox_scoring.py     ← 7-component analytics-driven scoring (0-125 pts)

~/.hermes/pressbox/
  posted_topics.json      ← Post history + engagement metrics (views, likes, replies, shares)
```

## One-Way Feedback Loop

```
┌──────────────────────────────────────────────────────────┐
│                  PRESSBOX PIPELINE                        │
│              (tiap 30 menit, cron existing)               │
│                                                          │
│  1. SCRAPE         mirror + skysports + goal.com         │
│       ↓                                                  │
│  2. PULL METRICS   post > 12h → views/likes/replies      │
│       ↓                                                  │
│  3. ANALYZE        best hooks/topics/sources dari data    │
│       ↓                                                  │
│  4. SCORE          pakai data real (+dynamic boost)       │
│       ↓                                                  │
│  5. GENERATE       LLM RCTOR framework, 6 slides         │
│       ↓                                                  │
│  6. GROUND CHECK   stage block, name warn                │
│       ↓                                                  │
│  7. POST           Threads API (chain/thread)            │
│       ↓                                                  │
│  8. TRACK          posted_topics.json updated             │
│                                                          │
│  ────────── loop balik ke step 1 ──────────              │
└──────────────────────────────────────────────────────────┘
```

### How Feedback Works

1. **Pull Metrics** — Setiap run, pipeline pull engagement data (views, likes, replies, shares) untuk post yang sudah >12 jam dari Threads Insights API
2. **Analyze Patterns** — Hitung rata-rata views per hook type (controversy, conflict, curiosity, event) dan topic type (transfer, match_result, injury_update, dll)
3. **Dynamic Scoring Boost** — Hook type yang proven high-engagement dapat +15 pts. Topic type yang worst performer kena -20 pts
4. **Auto-Skip** — Post yang metrics pull gagal di-mark (`metrics_failed`) supaya ga retry terus

**Feedback delay: ~12-24 jam** (post → collect metrics → next run pakai data real)

## Scoring (0-125 pts)

| # | Component | Max | Description |
|---|-----------|-----|-------------|
| 1 | Keyword Match | 40 | +8 per unique football keyword (transfer, match, drama, international) |
| 2 | Category Relevance | 20 | transfer/match/drama = 20, international = 10 |
| 3 | Recency | 15 | <6h = 15, 6-24h = 10, 24-48h = 5 |
| 4 | Data/Konkret | 15 | Specific data (score 3-1, fee £50m, %) = 15, vague digits = 7 |
| 5 | Source Tier | 10 | BBC/Sky/Goal = 10, Mirror/DailyMail = 5 |
| 6 | Audience Reach | 40 | +10 per big team/nation/star mentioned (cap 40) |
| 7 | Drama Signal | 15 | +5 per drama word in title (slams, blasts, exclusive, breaking, revealed, dll) |
| — | **Dynamic Hook Boost** | +15 | Hook type proven high-engagement dari analytics |
| — | **Dynamic Topic Penalty** | -20 | Topic type worst performer dari analytics |

**Threshold:** score >= 60 untuk pipeline.

### Hook Classification

| Hook Type | Trigger Words | Avg Performance |
|-----------|---------------|-----------------|
| controversy | slams, blasts, hits out, furious, scandal, row, rift | High |
| conflict | vs, against, clash, rival, battle, showdown | Medium-High |
| curiosity | ?, how, why, what if, can, will, could | Variable |
| event | just, dropped, lost, won, banned, sacked, arrested | High |
| statement | (default — no trigger) | Baseline |

## Sources

| Source | Method | Image | Notes |
|--------|--------|-------|-------|
| Mirror | HTML scrape | og:image (HD) | Per-article fetch |
| SkySports | RSS | media:content | 12h freshness |
| Goal.com | HTML scrape | og:image (4K) | Per-article fetch |

## Grounding Validator

Post-generation check that catches hallucinated content:
- **Football stages**: Stages mentioned in slides but not article → BLOCKS posting
- **Proper nouns**: Names in slides but not article → soft warn (logged, doesn't block)

## Threads Content Format

Each post = 6-slide thread using RCTOR framework:
1. **HOOK** — Punchy 1-2 sentence opener (STOP THE SCROLL)
2. **WHAT** — The actual story (who, what, where)
3. **TENSION** — Why it matters, the stakes
4. **HUMAN** — The emotional angle (quote, reaction, backstory)
5. **UNRESOLVED** — Open question, cliffhanger
6. **CTA** — Call-to-action + link back to source

Rules: <=500 chars/slide, sentence case, NO hashtags, NO emojis in text, native platform tone.

## Setup

```bash
# Clone repo
git clone https://github.com/hadijayyy/pressbox-pipeline.git ~/.hermes/pressbox-pipeline
cd ~/.hermes/pressbox-pipeline
pip install -r requirements.txt

# LLM API key
echo 'MISTRAL_API_KEY=your_key' >> ~/.hermes/.env

# Threads token (needs threads_manage_insights scope)
echo '{"access_token": "your_token", "user_id": "26778473708441722"}' > ~/.hermes/threads_token.json

# Data dirs
mkdir -p ~/.hermes/pressbox
echo '{"topics": []}' > ~/.hermes/pressbox/posted_topics.json

# Copy cron scripts
cp run-mvp.sh ~/.hermes/scripts/
cp watchdog.sh ~/.hermes/scripts/watchdog-pressbox.sh
```

## Usage

```bash
# Dry run (scrape + generate, no post)
python3 -u pressbox-mvp.py --dry-run

# Live run
bash run-mvp.sh
```

## Cron Setup (Hermes — no_agent)

Runs via `no_agent: true` cron jobs — zero token cost, direct script execution on host.

| Job | Schedule | Script | Deliver | Behavior |
|-----|----------|--------|---------|----------|
| Pressbox MVP | `0,30 * * * *` | `run-mvp.sh` | topic 20467 | Scrape → pull metrics → score → generate → post |
| Pressbox Watchdog | `15,45 * * * *` | `watchdog-pressbox.sh` | topic 20467 | Silent if OK, auto-retry if fail/stale |

**Cron scripts live in `~/.hermes/scripts/`** (host-visible path). Repo at `~/.hermes/pressbox-pipeline/`.

Built-in **15-minute cooldown** prevents duplicate posts.

## Threads API Requirements

| Scope | Purpose |
|-------|---------|
| `threads_basic` | Read profile, list posts |
| `threads_content_publish` | Create posts |
| `threads_manage_insights` | Pull engagement metrics (views, likes, replies, shares) |

Token stored at `~/.hermes/threads_token.json`. Exchange short-lived → long-lived via Meta Developer Dashboard.

## Requirements

- Python 3.8+
- `requests`, `beautifulsoup4`, `python-dotenv`, `feedparser`
- Mistral API key (`~/.hermes/.env`)
- Threads long-lived access token with `threads_manage_insights` scope (`~/.hermes/threads_token.json`)
