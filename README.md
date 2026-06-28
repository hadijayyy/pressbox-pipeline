# 📰 Press Box Pipeline

**Automated football content pipeline → Threads (@parkthebus.football)**

Scrapes football news (Mirror, Sky Sports, Goal.com), generates 6-slide threads via LLM, and posts to Threads with images — fully unattended.

## How It Works

```
:00  Pipeline    → scrape → filter → score → LLM generate → stage
:05  Health check → silent on success, alert on failure
:10  Auto-fix     → re-run pipeline for safe failures
:15  Staging check → if empty, auto-recover
:30  Post         → read staging → post 6-slide thread → permalink
:58  Pre-flight   → py_compile all scripts before next cycle
```

Runs hourly. Self-heals via circuit breaker + health check + auto-fix (3-layer safety net).

## Quick Start

```bash
git clone https://github.com/hadijayyy/pressbox-pipeline.git
cd pressbox-pipeline
pip install -r requirements.txt
cp .env.example ~/.hermes/.env   # add your API keys
```

### Required credentials

| File | Contents |
|------|----------|
| `~/.hermes/.env` | `MISTRAL_API_KEY=...` (primary LLM) |
| `~/.hermes/threads_token.json` | `{"access_token": "...", "user_id": "26778473708441722"}` |

LLM chain: `mistral-large-latest` (primary) → `qwen/qwen3-32b` via 9router (fallback).

## Scripts

| Script | Purpose |
|--------|---------|
| `pressbox-pipeline-v7.py` | Main pipeline — scrape → filter → score → LLM → stage |
| `pressbox-post.py` | Post manager — staging → Threads API → permalink |
| `pressbox-research.py` | RSS scraper (Mirror, Sky Sports, Goal.com) |
| `pressbox-common.py` | Shared: classifier (11 categories), filters, scoring |
| `pressbox-scoring.py` | Topic + image scoring (7-component, 0–120 scale) |
| `pressbox-direct-post.py` | Threads Graph API client (container creation) |
| `post_pressbox_thread.py` | Chain driver — creates + publishes slide chain |
| `pressbox-preflight.py` | `py_compile` all scripts (`:58` cron) |
| `pressbox-health-check.py` | Scan cron output, alert on failure |
| `pressbox-autofix.py` | Re-run pipeline for transient failures |
| `pressbox-check-staging.py` | Recovery — runs pipeline if staging empty |
| `pressbox-cb-run.sh` | Circuit breaker runner |
| `hermes_circuit_breaker.py` | Per-job CLOSED/HALF-OPEN/OPEN state |
| `pressbox-analytics-feedback.py` | Daily engagement analytics |
| `pressbox-analytics-llm.py` | LLM deep analysis — hooks, topics, recommendations |

## Scoring (7 components, max ~120 pts)

| Component | Max | What it measures |
|-----------|-----|------------------|
| Keyword relevance | 40 | Title + description keyword density |
| Category | 20 | Topic type (injury > transfer rumour) |
| Recency | 15 | Publication freshness |
| Data richness | 15 | Stats, quotes, numbers present |
| Source tier | 10 | Mirror/Sky/Goal rank |
| Audience reach | 30 | Big teams/nations/stars mentioned |
| Drama signal | 15 | Conflict words in title |

Bonuses: concrete-event verbs (+15), generic rumour penalty (−10), niche penalty (−20).

## Architecture

- **11-category classifier** — injury, transfer, managerial_change, fifa_political, WC_team_guide, controversy, tactical_analysis, match_result, player_profile, tournament_news, other
- **Smart dedup** — URL-based + title Jaccard similarity (35%/50% relaxed), 30-min cache
- **Relaxed filter** — threshold loosens when scrape < 10 topics
- **3-level image fallback** — og:image → body `<img>` → RSS, with `score_image()` player-photo preference
- **6-slide LLM prompt** — strict grounding (article-only facts), anti-hallucination, auto-trim
- **Atomic staging** — tmp + `os.replace()` prevents corruption
- **30-min cooldown** — prevents duplicate posts

## Cron Schedule

| Time | Script | Purpose |
|------|--------|---------|
| `:00` | `pressbox-pipeline-v7.py` | Generate thread |
| `:05` | `pressbox-health-check.py` | Verify pipeline |
| `:10` | `pressbox-autofix.py` | Re-run on failure |
| `:15` | `pressbox-check-staging.py` | Recover if empty |
| `:30` | `pressbox-post.py` | Post to Threads |
| `:58` | `pressbox-preflight.py` | Syntax check all scripts |
| `23:00` | `pressbox-analytics-feedback.py` | Daily analytics |
| `23:22` | `pressbox-analytics-llm.py` | LLM analysis |

## Testing

```bash
pytest tests/
```

57 tests: classifier (42), filter (13), smoke (2).

## License

MIT

*Built for @parkthebus.football*
