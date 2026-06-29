# Pressbox Pipeline — MVP

Automated football content pipeline for [@parkthebus.football](https://www.threads.net/@parkthebus.football) on Threads.

**One script. Scrape → Score → Generate → Post.**

## Architecture

```
~/.hermes/scripts/
  run-mvp.sh              ← Cron entry point (with retry)
  watchdog-pressbox.sh    ← Auto-retry watchdog

~/.hermes/pressbox-pipeline/
  pressbox-mvp.py         ← Main pipeline (scrape, score, LLM generate, post)
  threads_poster.py       ← Threads Graph API wrapper
  pressbox_common.py      ← Shared utils (paths, logging, dedup, classification)
  pressbox_scoring.py     ← 7-component analytics-driven scoring (0-120 pts)
  run-mvp.sh              ← Repo-local entry point
  watchdog.sh             ← Repo-local watchdog
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
# Clone repo
git clone https://github.com/hadijayyy/pressbox-pipeline.git ~/.hermes/pressbox-pipeline
cd ~/.hermes/pressbox-pipeline
pip install -r requirements.txt

# LLM API key
echo 'MISTRAL_API_KEY=your_key' >> ~/.hermes/.env

# Threads token
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
| Pressbox MVP | `0,30 * * * *` | `run-mvp.sh` | topic 20467 | Scrape → score → generate → post |
| Pressbox Watchdog | `15,45 * * * *` | `watchdog-pressbox.sh` | topic 20467 | Silent if OK, auto-retry if fail/stale |

**Cron scripts live in `~/.hermes/scripts/`** (host-visible path). Repo at `~/.hermes/pressbox-pipeline/`.

Built-in **15-minute cooldown** prevents duplicate posts.

**Container vs Host:** Scripts, repo, credentials, and data all live on the host filesystem (`/home/ubuntu/.hermes/`). Container overlay (`/root/.hermes/`) is separate — don't write pressbox files there.

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

Threshold: score >= 30 to publish. Analytics feedback adjusts boosts/skips dynamically.

## Grounding Validator

Post-generation check that catches hallucinated content:
- **Football stages**: Stages mentioned in slides but not article → BLOCKS posting
- **Proper nouns**: Names in slides but not article → soft warn (logged, doesn't block)

## Sources

| Source | Method | Image | Notes |
|--------|--------|-------|-------|
| Mirror | HTML scrape | og:image (HD) | Per-article fetch |
| SkySports | RSS | media:content | 12h freshness |
| Goal.com | HTML scrape | og:image (4K) | Per-article fetch |

## Requirements

- Python 3.8+
- `requests`, `beautifulsoup4`, `python-dotenv`, `feedparser`
- Mistral API key (`~/.hermes/.env`)
- Threads long-lived access token (`~/.hermes/threads_token.json`)
