# Pressbox Pipeline

Automated football content pipeline for [@parkthebus.football](https://www.threads.net/@parkthebus.football) on Threads.

Scrapes football news from 5 sources, detects hot/viral topics via entity clustering + **Google Trends**, scores with a multi-layered engine, selects from **6 viral content patterns**, generates 6-slide carousels via LLM (Mistral) with priority-guided prompt architecture and anti-hallucination grounding, and posts on schedule — fully automated with engagement feedback loop.

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
│  4. PATTERN SELECT  A (Rule-Break) / B (Contradiction) /    │
│                     C (Detail+Emotion) / D (Commentary) /    │
│                     E (Pressure Cooker) / F (Behind-Scenes)  │
│       ↓                                                      │
│  5. SCORE           16-component data-driven engine +        │
│                     context-aware bonuses + soft cap + tune  │
│       ↓                                                      │
│  6. VERIFY          Article: 1000+ chars, 150+ words,        │
│                     8+ unique sentences. Tries top 5.       │
│       ↓                                 │
│  7. FETCH           Extract full article text + og:image HD  │
│       ↓                                                      │
│  8. GENERATE        Mistral LLM → XML-prompted output with   │
│                     priority ladder, evidence rules, source  │
│                     validation, sensitive topic exception    │
│       ↓                                                      │
│  9. GROUND CHECK    Named entity match + hallucinated stage  │
│                     detection + number grounding (soft/hard)  │
│       ↓                                                      │
│ 10. EVALUATOR       9-rule stance check (must take a side)   │
│                     + engagement viability + up to 3 cycles  │
│       ↓                                                      │
│ 11. POST            Threads API (chained thread + image)     │
│       ↓                                                      │
│ 12. TRACK           posted_topics.json + hotness for A/B     │
│       ↓                                                      │
│ 13. NOTIFY          @Szejay_bot (4-line format)              │
└──────────────────────────────────────────────────────────────┘

Cron: every 60m, watchdog at :15.
```

## Viral Content Patterns

| Pattern | Style | Trigger Words | Top Performance |
|---------|-------|---------------|-----------------|
| **A — Rule-Break** | Authority violates own rule/ethos | FIFA broke, UEFA waived, IFAB ignores | **12M+** (parkthebus) |
| **B — Contradiction** | Opposing facts/claims exposed | contradicts, despite, while, yet | — |
| **C — Detail+Emotion** | Data-driven human interest | contract, sacrifice, journey, fee | ~191K |
| **D — Commentary** | Celebrity/pundit says something | slams, warns, hits out, reacts | ~403K |
| **E — Pressure Cooker** 🔥 | Player/manager under fire | NOT happy, fumes, speaks out, defiant | **634K** (Bellingham slap) |
| **F — Behind-the-Scenes** 🏗️ | Logistics, admin, VAR, ref | hotel, travel, fitness, decisions | **536K** (Norway hotel) |

Pattern selection is automatic: keyword + signal detection, not random. E and F are prioritised for post-tournament drama/news.

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
| User Feedback Boost | Hook-type or topic-type performs well | +15 |
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

## Prompt Architecture (v4)

Hybrid architecture — v3 editorial skeleton + proven arc templates + viral criteria:

### Instruction Priority

7-level priority ladder (higher = override lower):

1. **Accuracy** — Never sacrifice truth for viral pattern. Never invent.
2. **Safety** — No misinformation, no libellous claims.
3. **Story** — Every slide advances one coherent narrative.
4. **Clarity** — Simple words. Short sentences. Clear throughline.
5. **Tension** — Raise then hold tension. Binary question earns the answer.
6. **Brand** — @parkthebus.football voice: sharp, confident, casual.
7. **Style** — Forbidden phrases avoided. Caption format enforced.

### Source Validation

4-point silent pre-check before drafting:

- ⚠️ Vague source (unnamed "sources", "insiders") → downgrade certainty
- ⚠️ Conflicting reports → present the gap, don't choose one
- ⚠️ Out of context → check if quote/situation is recent
- ⚠️ Hyperbolic headline vs measured body → trust the body

### Evidence Rules

**Never invent:** exact quote, fee/contract value, club/league valuation, player age, date/time, statistic, injury/illness, incident description, motive/intent, tactical reason, consequence/ban. If article doesn't state it, don't write it.

**Uncertainty preservation:** use "reportedly", "allegedly", "according to [source]", "at least [number]" when source is indirect.

**Attribution rule:** source named once in S2, S3, or S4 — not S1, not all slides.

**External knowledge:** allowed only for S6 irony/comparison — must be undisputed (e.g., trophy count, league table position, fixture date).

### Per-Slide Constraints

- S1: EXACTLY 2 sentences, ≤25 words — dense hook that earns the scroll
- S2–S5: 2–3 sentences, one new insight/slide, every slide must have a take
- S6: One or two sentences. Story-specific question. Divisive topics = name two named options. Sensitive topics (injuries/abuse/discrimination) = reflective question, NOT divisive bait.
- MAX 15 words per sentence

### Sensitive Topic Exception

For articles involving injury, abuse, discrimination, or criminal allegations: S6 must be a reflective question ("How should the league handle this?") NOT divisive bait ("Was he right or wrong?").

### Self-Check (before output)

1. JSON valid? 2. Exactly 6 slides? 3. S1 ≤ 2 sentences? 4. S6 question matches sensitive/divisive rules? 5. Every claim supported by article? 6. No forbidden phrases? 7. Source attributed? 8. Caption format correct?

## Content Format

6-slide Threads carousel with pattern-specific arcs:

### Pattern A — Rule-Break arc
- S1 = Hook: "[Authority] just broke its own rule for [Team A] vs [Team B]. [Concrete detail] — [binary Q with irony/venue twist]"
- S2 = Physical detail (size, numbers, quotes, timeline)
- S3 = Lore/context (existing rule, sponsors, why first)
- S4 = Stakes escalation
- S5 = What makes this unique
- S6 = Binary Q (irony/venue twist)

### Pattern B — Contradiction arc
- S1 = Hook: "[Thing] is [claim] — but [contradicting evidence]. [Implication] — [binary Q]"
- S2 = The contradiction (make gap explicit)
- S3 = Evidence proving contradiction
- S4 = Why it matters
- S5 = Revealed motives/priorities
- S6 = Binary Q on interpretation

### Pattern C — Detail+Emotion arc
- S1 = Hook: "[Concrete number/detail] about [person/team]. [Emotional stake] — [binary Q]"
- S2 = Emotional entry point
- S3 = Tangible evidence (data/timeline)
- S4 = Stakeholder impact
- S5 = Larger irony
- S6 = Binary Q on future implications

### Pattern D — Commentary arc
- S1 = Hook: "[Name] just said [bold/controversial statement]. [Reason they have authority on this] — [binary Q]"
- S2 = Context of quote
- S3 = Why this voice matters
- S4 = Counterpoint/opposition
- S5 = How this affects real decisions
- S6 = Binary Q on whether opinion holds

### Pattern E — Pressure Cooker arc
- S1 = Hook: "[Player/Manager] [not happy/under fire] after [trigger event]. [Consequence at stake] — [binary Q]"
- S2 = Tension context
- S3 = Parties involved
- S4 = Stakes (job/transfer/board)
- S5 = What's unique (history/contract/timing)
- S6 = Binary Q on outcome

### Pattern F — Behind-the-Scenes arc
- S1 = Hook: "Why [team/authority] did [specific thing]. [How it affects fans/players] — [binary Q]"
- S2 = The situation
- S3 = Why it matters (time/budget/health)
- S4 = Who benefits/who loses
- S5 = What it reveals about organization
- S6 = Prediction binary

### Anti-Hallucination Grounding

3-layer system:

1. **Prompt Hardening** — Evidence rules + never-invent list + uncertainty preservation baked into system prompt
2. **Reference Data Injection** — Current date, player ages, tournament timeline (pre-computed)
3. **Post-Gen Number Check** — Scans for every `£/$/€` amount + statistic and warns if not in article; soft warn for names, hard block for stages

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
  "slide_1": "2-sentence hook. Dense, earns the scroll.",
  "slide_2": "One new insight.",
  "slide_3": "Next insight — evidence or lore.",
  "slide_4": "Stakes escalation.",
  "slide_5": "Setup the binary.",
  "slide_6": "Story-specific Q. Divisive = named options. Sensitive = reflective.",
  "caption": "Line 1 = hook. Last line = \"Agree or disagree — [question]?\"",
  "cover_image_keywords": "search terms",
  "needs_more_source": "if article insufficient, explain why"
}
```

If article lacks depth, `slide_1` starts with `"needs_more_source"` → pipeline skips article gracefully.

## Engagement Feedback Loop

```
Every run:
  1. pull_engagement() — update metrics for posts >12h old
  2. get_analytics_summary() — classify hooks/topics, compute boosts
  3. Score with analytics + Google Trends + hot topic boosts
  4. Select pattern (A/B/C/D/E/F) based on content signals
  5. Generate with selected arc structure + XML-prompted LLM
  6. Grounding check (names + stages + numbers)
  7. Evaluator (9-rule stance check, up to 3 cycles)
  8. Post to Threads
  9. Track with hotness_score for A/B comparison
```

Feedback delay: ~12–24 hours.

## Architecture

```
~/.hermes/scripts/
  run-mvp.sh                    ← Cron entry point
  watchdog-pressbox.sh          ← Health monitor (re-runs if stale)
  pressbox-engagement-report.sh ← Daily report

~/.hermes/pressbox-pipeline/
  pressbox-mvp.py               ← Main pipeline (prompt v4)
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
| Pressbox MVP | every 60m | Scrape → score → verify → generate → post |
| Pressbox Watchdog | `15 * * * *` | Re-runs pipeline if stale |
| Daily Report | `0 8 * * *` | Engagement summary via @Szejay_bot |
| Hourly Report | `0 * * * *` | Status report |

## Rate Limits

Pipeline handles Mistral API 429 gracefully with exponential backoff:
- 1st 429 → sleep 30s, retry
- 2nd 429 → sleep 45s, retry
- 3rd 429 → sleep 60s, move to next article
- All articles 429'd → exit (cron wrapper retries)

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
