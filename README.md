# Pressbox Pipeline — MVP

Automated football content pipeline for [@parkthebus.football](https://www.threads.net/@parkthebus.football) on Threads.

**One script. Scrape → Score → Generate → Post.**

## Architecture

```
pressbox-mvp.py       ← Main pipeline (scrape, score, LLM generate, post)
threads_poster.py     ← Threads Graph API wrapper
pressbox_common.py    ← Shared utils (paths, logging, dedup, classification)
pressbox_scoring.py   ← 7-component analytics-driven scoring (0-120 pts)
```

## Flow

1. **Scrape** — 3 sources in parallel (Mirror, SkySports RSS, Goal.com)
2. **Filter** — Dedup, sensitive content, TV guides, women's football, analytics skip list
3. **Score** — 7-component v17 scoring: keywords, category, recency, data, source tier, audience reach, drama signal
4. **Extract** — Fetch article text + og:image
5. **Generate** — Mistral Large → 6-slide thread (HOOK → WHAT → TENSION → HUMAN → UNRESOLVED → CTA)
6. **Grounding check** — Verify proper nouns + football stages against article (catches hallucinations)
7. **Post** — Chain via reply_to_id (Threads native thread format)
8. **Track** — Append to posted_topics.json (keeps last 200)

## Setup

```bash
pip install -r requirements.txt

# LLM API key
echo 'MISTRAL_API_KEY=***' > ~/.hermes/.env

# Threads token
echo '{"access_token": "TOKEN", "user_id": "26778473708441722"}' > ~/.hermes/threads_token.json

# Data dirs
mkdir -p ~/.hermes/pressbox
echo '{"topics": []}' > ~/.hermes/pressbox/posted_topics.json
```

## Usage

```bash
# Dry run (scrape + generate, no post)
python3 pressbox-mvp.py --dry-run

# Live run
python3 pressbox-mvp.py
```

## Cron

Single cron job runs every 2 hours:
```
0 */2 * * * cd /home/ubuntu/pressbox-pipeline && python3 -u pressbox-mvp.py
```

Built-in 30-minute cooldown prevents spam.

## Scoring (0-120 pts)

| Component | Max | Description |
|-----------|-----|-------------|
| Keyword Match | 40 | Football-specific keywords (transfer, match, drama, international) |
| Category | 20 | Topic type relevance |
| Recency | 15 | <6h=15, 6-24h=10, 24-48h=5 |
| Data/Konkret | 15 | Specific numbers (scores, fees, stats) |
| Source Tier | 10 | Tier 1 (BBC, Sky, Goal) vs Tier 2 (Mirror) |
| Audience Reach | 30 | +10 per big team/nation/star mentioned |
| Drama Signal | 15 | +5 per drama word in title |

Threshold: score >= 60 to publish. Analytics feedback adjusts boosts/skips dynamically.

## Grounding Validator

Post-generation check that catches hallucinated content:
- **Proper nouns**: Extracts multi-word capitalized names from generated slides, flags any not in the original article
- **Football stages**: Normalizes stage references (round of 16, R16, etc.), flags stages mentioned in slides but not article

Warnings are logged but don't block posting (soft gate).

## Sources

| Source | Method | Image | Notes |
|--------|--------|-------|-------|
| Mirror | HTML scrape | og:image (HD) | Per-article fetch |
| SkySports | RSS | media:content | 6h freshness |
| Goal.com | HTML scrape | og:image (4K) | Per-article fetch |

## Requirements

- Python 3.8+
- `requests`, `httpx`, `beautifulsoup4`, `python-dotenv`, `feedparser`
- Mistral API key
- Threads long-lived access token
