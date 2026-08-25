# Pressbox Pipeline

Automated football content pipeline for [@parkthebus.football](https://www.threads.net/@parkthebus.football) on Threads.

Scrapes football news from 3 sources, detects hot/viral topics via entity clustering + **Google Trends**, scores with a multi-layered engine, selects from **3 active viral content patterns**, generates 6-slide carousels via LLM (Mistral) with priority-guided prompt architecture and anti-hallucination grounding, and posts on schedule — fully automated with engagement feedback loop.

## How It Works

```
┌──────────────────────────────────────────────────────────────┐
│  1. SCRAPE          3 sources (goal, mirror, bbc) —          │
│                     parallel + Stage 2 enrichment for goal   │
│                     (og:description, og:image, published_ts)│
│       ↓                                                      │
│  2. FILTER          Commercial/TV/sensitive/women/statement  │
│                     blocked + dedup + similarity + analytics │
│       ↓                                                      │
│  3. HOT DETECT      2h persistent cache + entity clustering  │
│                     (Union-Find) + GOOGLE TRENDS match       │
│       ↓                                                      │
│  4. PATTERN SELECT  D (Commentary, slams/warns) /            │
│                     E (Pressure Cooker) / F (Behind-Scenes)  │
│       ↓                                                      │
│  5. SCORE           16-component data-driven engine +        │
│                     authority persona boost + reversal-verb  │
│                     boost + conflict hook penalty +          │
│                     BBC balance + cold-source rotation       │
│       ↓                                                      │
│  6. VERIFY          Article: 1000+ chars, 150+ words,        │
│                     8+ unique sentences. Tries top 5.       │
│       ↓                                                      │
│  7. FETCH           Extract full article text + og:image HD  │
│       ↓                                                      │
│  8. GENERATE        Mistral LLM → XML-prompted output with   │
│                     priority ladder, evidence rules, source  │
│                     validation, sensitive topic exception,   │
│                     viral elements, S1 stop-scroll rules     │
│       ↓                                                      │
│  9. GROUND CHECK    Named entity match + hallucinated stage  │
│                     detection + number grounding (soft/hard) │
│       ↓                                                      │
│ 10. EVALUATOR       9-rule stance check (E/F + score≥70      │
│                     skip) + 1 retry cycle                    │
│       ↓                                                      │
│ 11. FAIL CLOSED     If generation/evaluator fails, skip post │
│       ↓                                                      │
│ 12. POST            Threads API (S1→S6 reply_to_id chain     │
│                     + image on S1 only)                      │
│       ↓                                                      │
│ 13. TRACK           posted_topics.json + hotness for A/B     │
│       ↓                                                      │
│ 14. NOTIFY          @Szejay_bot with predicted_views from    │
│                     engagement ring (median by source+hook)  │
└──────────────────────────────────────────────────────────────┘

Cron: every 60m, watchdog at :15.
```

## Viral Content Patterns

| Pattern | Style | Trigger Words | Top Performance |
|---------|-------|---------------|-----------------|
| **D — Commentary** | Celebrity/pundit says something | slams, warns, hits out, reacts | ~403K |
| **E — Pressure Cooker** 🔥 | Player/manager under fire | NOT happy, fumes, speaks out, defiant | **634K** (Bellingham slap) |
| **F — Behind-the-Scenes** 🏗️ | Logistics, admin, VAR, ref | hotel, travel, fitness, decisions | **536K** (Norway hotel) |

Pattern selection is automatic: keyword + signal detection, not random. Active set keeps D/E/F only. E and F are prioritised for post-tournament drama/news; D is safe fallback for ordinary commentary.

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
| `_TV_GUIDE` | "How to watch", "TV channel", "live stream", "kick-off time" |
| `_SENSITIVE_EXACT` / `_SENSITIVE_WILDCARD` | "charged with murder", "arrested", "domestic violence" |
| `_WOMEN` | Lionesses, NWSL, women's football |
| `_LOW_VALUE_GARBAGE` | "could line up", "salary", "transfer route", "roundup" |
| `/live/` `/quiz/` URLs | Live commentary, quiz pages (not articles) |
| Length gate | Article must be 1000+ chars, 150+ words, 8+ unique sentences |
| Body verification | Football signals ≥ 2, commercial signals < 2 |
| Volume gate | Min 2h gap between posts (≤12/day) |

## Scoring System

### Base Components (0–170+ pts)

| # | Component | Points | Data Source |
|---|-----------|--------|-------------|
| 1 | Keyword Match | +8/keyword (max 5 = 40) | |
| 2 | Category | 20 (transfer/match/drama) / 10 (international) / 0 | |
| 3 | Recency | 15/10/5/0 | |
| 4 | Data/Konkret | 15/7/0 | |
| 5 | Source Tier | **15** (Super) / 10 (Tier 1) / 5 (Tier 2) | goal avg 13K — demoted (clickbait-heavy) |
| 6 | Audience Reach | +10/big name (max 40) | |
| 7 | Drama Signal | +5/word (max 10) | |
| 8 | First Ever | +20/+10 | |
| 9 | Niche Nation | -15 | |
| 10 | Paradox Bonus | +12 | |
| 11 | Warning Bonus | +8 | |
| **12** | **Star Player** | **+20** | Data: +39% above baseline |
| **13** | **Conflict Hook** | **-15** (penalty) | Data: conflict avg 2.6K — formulaic, tank engagement |
| 14 | Timing Urgency | **+8** (1+ hit) | |
| **15** | **Human Story** | **+20** | Data: highest engagement rate (1.5%) |
| **16** | **Reversal Verb Boost** | **+30** | Data: slams/blasts/ban/boycott/resigns all top performers |

### Pipeline Bonuses (context-aware)

| Bonus | Trigger | Points |
|-------|---------|--------|
| **Authority-Persona** | FIFA/UEFA/Infantino/Champions League/IFAB/FA keyword | **+10** |
| Controversy Topic Type | `controversy` / `fifa_political` / `manager_sack` | +15 |
| User Feedback Boost | Hook-type or topic-type performs well (ring data) | +15 / -10 |
| Transfer Related | Transfer keywords | +10 |
| Hot Topic | Multi-source cluster (hotness ≥ 3.0) | +25 |
| Google Trends | Trending query matches article | +0.5~8.0 |
| Warm Topic | Multi-source cluster (hotness ≥ 1.5) | +15 |
| Peak Hour | 17–21 WIB + hot topic | +10 |
| **BBC Credibility** | source = bbc | **+5** |
| **BBC Balance** | bbc + not in last 2 posts | **+5** |
| **Cold-Source Rotation** | source not in last 2 posts | **+15** |
| Topic Penalty | Worst performing topics | -20 |
| Niche Topic | boots/kit/jersey/stadium rules | -30 |
| Auto-Tuning | ML-adjusted multipliers | ±15 |

### Guards & Caps

| Guard | What it does |
|-------|-------------|
| Hot relevance check | Entity must appear in title first half |
| Niche penalty | -30 for boots/kit/jersey/stadium |
| Soft cap | Above 100: `100 + (score - 100) × 0.3` |
| Failure guard | If generation/evaluator fails, skip post |

**Effective score range:**

```
Low-quality (boots/kit)   : 15–40
Average (preview/quiz)    : 40–65
Good (match result)       : 65–90
Hot drama (controversy)   : 90–130
Viral combo (star+reversal+authority) : 130–170
```

## Hook Classification (data-driven)

Hooks classified per title, then mapped to engagement ring for boost/penalty:

| Hook | Trigger words | Avg views | Treatment |
|------|---------------|-----------|-----------|
| `statement` | neutral / informational | 20K | ring boost +5 to +15 |
| `event` | just/dropped/banned/sacked/arrested | 11K | ring boost +5 to +15 |
| `controversy` | slams/blasts/row/erupts/scandal | 16K | ring boost +5 to +15 |
| `curiosity` | `?` in title | 14K | ring boost +5 to +15 |
| `conflict` | vs/against/clash/rival | 2.6K | ring penalty -10 + base -15 |

## Hot Topic Detection

**Dual-layer detection:**

1. **Entity clustering (internal):** 2h rolling window, Union-Find by player/team entity overlap. Multi-source coverage = viral boost.
2. **Google Trends (external):** UK RSS feed matched against article titles. Football-specific queries get priority boost.

## Image Handling

| Layer | Source | Quality |
|-------|--------|---------|
| Primary | `og:image` from article HTML | 1200px (HD) |
| Fallback | RSS `<media:thumbnail>` / `<enclosure>` | 240–480px |
| BBC upscale | `ichef.bbci.co.uk/480/` → `/1024/` | 1024px |
| Goal enrichment | `og:image` from article HTML (Stage 2) | 1200px |

## Sources

| Source | Method | Tier | Notes |
|--------|--------|------|-------|
| Goal.com | HTML scrape + Stage 2 og: enrichment | **2 (+5)** | Demoted — avg 13K, clickbait-heavy. BBC avg 30K 2.3× better. |
| BBC | RSS | **1 (+10)** | Image upscale to 1024px. Balance boost when under-rotated. Top viral source (Infantino 140K, World Cup drama 140K). |
| Mirror | RSS | 2 (+5) | Fresh 0–1h, Arsenal-heavy |

## Prompt Architecture (v4)

Hybrid architecture — v3 editorial skeleton + proven arc templates + viral elements:

### Instruction Priority

7-level priority ladder (higher = override lower):

1. **Accuracy** — Never sacrifice truth for viral pattern. Never invent.
2. **Safety** — No misinformation, no libellous claims.
3. **Story** — Every slide advances one coherent narrative.
4. **Clarity** — Simple words. Short sentences. Clear throughline. **No insider jargon or unexplained technical terms.**
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

**Attribution rule:** source named once in S2, S3, or S4 — not S1, not all slides. S5 = "TWIST + SOURCE" with explicit attribution format ("BBC reports", "according to X").

**External knowledge:** allowed only for S6 irony/comparison — must be undisputed (e.g., trophy count, league table position, fixture date).

### Per-Slide Constraints

- **S1 — HOOK (scroll-stopper):** EXACTLY 2 sentences, ≤25 words total. Sentence 1 = SPECIFIC person/club/authority + action verb + concrete fact (name, number, or decision). Sentence 2 = stakes, tension, or why-it-matters. **Stop-scroll test:** if you saw this in your feed, would you stop? Name early, number early, no em dash, no question in S1.
- **S2–S4** — One new insight per slide. 2–3 sentences. Evidence or lore.
- **S5 — TWIST + SOURCE** — Story-specific twist with explicit attribution. "BBC reports...", "according to UEFA...". 2–3 sentences.
- **S6 — comment-bait** — One or two sentences. Story-specific binary question. **Two-sided rule:** divisive topics = name two named options ("Will Infantino listen, or double down?"). Sensitive topics (injuries/abuse/discrimination) = reflective question, NOT divisive bait.
- MAX 15 words per sentence

### Sensitive Topic Exception

For articles involving injury, abuse, discrimination, or criminal allegations: S6 must be a reflective question ("How should the league handle this?") NOT divisive bait ("Was he right or wrong?").

### Viral Elements (use only when source supports)

Viral ≠ manufactured. Every element below must be supported by the source or a clearly attributed interpretation. If the source has no conflict, urgency, or stakes, skip — do not invent.

1. **Conflict** — Two sides, opposing claims, clashing parties
2. **Relatable stake** — Money, contract, fans, fairness
3. **Specific number** — Concrete fee, count, time, salary
4. **Stop-scroll S1** — Name + action + fact in first 7 words

### Self-Check (before output)

1. JSON valid? 2. Exactly 6 slides? 3. S1 ≤ 2 sentences, ≤ 25 words, no insider jargon? 4. S6 question matches sensitive/divisive rules? 5. Every claim supported by article? 6. No forbidden phrases? 7. Source attributed (S2–S5)? 8. Caption format correct?

## Content Format

6-slide Threads carousel with active pattern-specific arcs:

### Pattern D — Commentary arc
- S1 = Hook: "[Name] just said [bold/controversial statement]. [Reason they have authority on this] — [binary Q]"
- S2 = Context of quote
- S3 = Why this voice matters
- S4 = Counterpoint/opposition
- S5 = TWIST + SOURCE
- S6 = Binary Q on whether opinion holds

### Pattern E — Pressure Cooker arc
- S1 = Hook: "[Player/Manager] [not happy/under fire] after [trigger event]. [Consequence at stake] — [binary Q]"
- S2 = Tension context
- S3 = Parties involved
- S4 = Stakes (job/transfer/board)
- S5 = TWIST + SOURCE
- S6 = Binary Q on outcome

### Pattern F — Behind-the-Scenes arc
- S1 = Hook: "Why [team/authority] did [specific thing]. [How it affects fans/players] — [binary Q]"
- S2 = The situation
- S3 = Why it matters (time/budget/health)
- S4 = Who benefits/who loses
- S5 = TWIST + SOURCE
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
  "slide_5": "TWIST + SOURCE. Attribution explicit.",
  "slide_6": "Story-specific Q. Divisive = named options. Sensitive = reflective.",
  "caption": "Line 1 = hook. Last line = \"Agree or disagree — [question]?\"",
  "cover_image_keywords": "search terms",
  "needs_more_source": "if article insufficient, explain why"
}
```

If article lacks depth, `slide_1` starts with `"needs_more_source"` → pipeline skips article gracefully.

## Thread Chaining (reply_to_id)

Posts chained via `reply_to_id` so S1→S6 appears as a single Threads thread:

| Slide | reply_to_id | Image |
|-------|-------------|-------|
| S1 | `None` (root) | cover image |
| S2 | S1.post_id | — |
| S3 | S2.post_id | — |
| S4 | S3.post_id | — |
| S5 | S4.post_id | — |
| S6 | S5.post_id | — |

Implementation: `threads_poster.py:228-261` — each slide's `reply_to_id` is the previously PUBLISHED post's id, not creation_id. `stop_on_error=True` — partial failure raises; if S3 fails, S1+S2 already posted, root_id saved but chain broken.

## Engagement Feedback Loop

```
Every run:
  1. pull_engagement() — update metrics for posts >12h old (24h retry on failure)
  2. get_analytics_summary() — classify hooks/topics, compute boosts
  3. Score with analytics + Google Trends + hot topic boosts
  4. Select pattern (A/B/C/D/E/F) based on content signals
  5. Generate with selected arc structure + XML-prompted LLM
  6. Grounding check (names + stages + numbers)
  7. Evaluator (9-rule stance check, 1 cycle; skip for E/F or score≥70)
  8. Post to Threads as S1→S6 chain
  9. Track with hotness_score for A/B comparison
  10. Notify @Szejay_bot with predicted_views (median of past source+hook posts)
```

Feedback delay: ~12–24 hours.

## Round-2 Optimizations (10 Aug 2026)

10 data-driven fixes based on 187 measured posts:

| # | Optimization | File | Impact |
|---|--------------|------|--------|
| 1 | Evaluator relax (E/F skip, score≥70 skip, retries 3→1) | pressbox-mvp.py:1493-1510 + 2269-2287 | -50s/post |
| 2 | Pattern E trigger 4→1 keywords, +35 new triggers | pressbox-mvp.py:1276-1289 | E posts surface more often |
| 3 | Active pattern set D/E/F | pressbox-mvp.py:1944-1997 | Low/unused legacy patterns removed |
| 4 | goal.com tier 1→2; bbc exact match added | pressbox_scoring.py:166-181 | BBC prioritised, goal demoted |
| 5 | BBC credibility boost +5 + balance +5 | pressbox-mvp.py:956-960 + 1050-1055 | BBC share 8%→~18% |
| 6 | Hot topic guard (skip extractive if hotness≥2) | pressbox-mvp.py:2302-2309 | Captures trending topics |
| 7 | Controversy 1.5× removed; conflict hook penalty -15 | pressbox-mvp.py:915-940 | Bias to viral hooks, away from formulaic |
| 8 | (Posting hours: cron 60m, NOT 12-14 UTC) | — | Excluded per user instruction |
| 9 | Predicted views in Szejay notify | pressbox-mvp.py:2357-2370 | Tracking + forecasting |
| 10 | metrics_failed 24h reset | pressbox-mvp.py:490-525 | Retry fetch, no permanent skip |

### Bug Fixes (Round 2)

| Bug | Fix | File |
|-----|-----|------|
| `_query_ring` overloaded (return ±int used for both scoring AND predicted views) | Split: `_query_ring` (scoring) + `_query_ring_predicted` (median views) | pressbox-mvp.py:58-110 |
| goal.com scrape missing `description`, `published_ts`, `image_url` | Stage 2 parallel `og:description/og:image/article:published_time` fetch | pressbox-mvp.py:181-205 |
| goal.com image_url="" no fallback flag | Set `_needs_image_fallback=True` | pressbox-mvp.py:204 |
| Controversy 1.5× score boost (data shows statement 20K > controversy 16K) | Removed | pressbox-mvp.py:915-940 |
| Conflict hook +20 (worst performer 2.6K) | Inverted to penalty -15 | pressbox-mvp.py:927 |

### Replay Results (187 historical posts)

| Source | Before | After |
|--------|--------|-------|
| goal | 53% (98/187) | 53% (86/161) |
| bbc | 8% (15/187) | 9% (15/161) — rising toward 15-20% target with balance boost |
| mirror | 38% (70/187) | 37% (60/161) |

Score distribution: 27% (44/161) score ≥100 (viral candidates), 21% (33/161) score 80-99.

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
  threads_poster.py             ← Threads API + reply_to_id chaining
  tests/                        ← Filter + classifier tests

~/.hermes/pressbox/
  posted_topics.json            ← Post history + engagement + hotness
  article-cache.json            ← 2h article cache for hot detection
  .trends_cache.json            ← Google Trends 30min cache
```

## Cron

| Job | Schedule | Behavior |
|-----|----------|----------|
| Pressbox MVP | every 60m | Scrape → score → verify → generate → post |
| Pressbox Watchdog | `15 * * * *` | Re-runs pipeline if stale |
| Daily Report | `0 8 * * *` | Engagement summary via @Szejay_bot |
| Hourly Report | `0 * * * *` | Status report |

## Reliability & Operations (Aug 2026)

- **Single-run lock** (`/tmp/pressbox-mvp-internal.lock`): module-level `fcntl.flock` prevents concurrent direct invocations. Deliberately a DIFFERENT file from the wrapper lock `/tmp/pressbox-mvp.lock` (run-mvp.sh holds flock on fd 200; same-file internal flock would always fail → silent `SKIPPED_ALREADY_RUNNING` skip — fixed 11 Aug 2026).
- **Wrapper status contract**: `run-mvp.sh` writes machine-readable state to `/tmp/pressbox-last-status` — `ok <ts>` (posted), `SKIP <ts> <reason>` (normal no-candidate), `LOCKED <ts>` (flock held), `FAILED <ts> <reason>`. Watchdog reads `last-status` and does NOT retry on `SKIP*` labels (prevents LLM-call loops on normal skips).
- **Token budget gate**: hard reject >80k chars input, warn >48k. Evaluator + writer calls capped; `fact packet` ≤4k tokens (title + URL + source + top 15 sentences — never raw body).
- **LLM call journal**: every LLM call logged to `~/.hermes/pressbox/llm_calls.json` (run_id, stage, input chars, output tokens, model, status). Usage report: `python3 ~/.hermes/scripts/token-cost-report.py`.
- **Transient-only retry**: only HTTP 429 / 5xx / timeout retried (max 1). Deterministic rejects (grounding, contract, editorial) never retried. Editorials are fail-closed — thresholds never lowered to fill a slot.
- **Candidate fallback**: top-N (`[:3]`) candidate loop — if writer/validator rejects top article, next ranked article is tried. One failed writer call no longer kills the run.
- **Source fingerprint** (`source-fingerprints.json`): 3-title hash + 3h expiry + force fresh on any source skip. Detects RSS staleness/silent feed breakage; Mirror HTTP 202 CDN gate handled with short-UA retry.
- **Volume gate**: 30-min minimum between posts (≤48/day).

## Rate Limits

Pipeline handles Mistral API 429 gracefully with exponential backoff:
- Attempt 1 → on 429 backoff, retry (attempt 2/2)
- Attempt 2 → on 429 backoff, move to next candidate
- All candidates 429'd → exit (cron wrapper retries only transient failures)

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
