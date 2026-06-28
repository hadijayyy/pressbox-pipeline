# Press Box Pipeline v7 — Complete Workflow Documentation

> **Purpose:** Automated football content engine for @parkthebus.football on Threads
> **Target:** 1 post/hour, 12-15 posts/day, World Cup 2026 focus
> **Architecture:** 3-stage cron pipeline + analytics feedback loop
> **Runtime:** ~30-50s per pipeline run, 120s cron limit

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Cron Schedule Overview](#2-cron-schedule-overview)
3. [Stage 1: Research + Generate (Pipeline)](#3-stage-1-research--generate-pipeline)
4. [Stage 2: Check Staging](#4-stage-2-check-staging)
5. [Stage 3: Post to Threads](#5-stage-3-post-to-threads)
6. [Analytics Feedback Loop](#6-analytics-feedback-loop)
7. [File Structure & Data Flow](#7-file-structure--data-flow)
8. [LLM Configuration](#8-llm-configuration)
9. [Image Handling](#9-image-handling)
10. [Error Handling & Guards](#10-error-handling--guards)
11. [Quality Controls](#11-quality-controls)
12. [Known Issues & Improvement Areas](#12-known-issues--improvement-areas)

---

## 1. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    PRESS BOX PIPELINE v7                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│  │  SCRAPE  │───▶│  FILTER  │───▶│  SCORE   │───▶│ EXTRACT  │  │
│  │ 3 sources│    │ dedup +  │    │  pick    │    │ curl +   │  │
│  │ parallel │    │ analytics│    │  best    │    │ og:image │  │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘  │
│                                                       │         │
│                                                       ▼         │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────────────┐  │
│  │  POST    │◀───│  CHECK   │◀───│    LLM GENERATE (8)      │  │
│  │  to      │    │ STAGING  │    │  deepseek-v4-flash       │  │
│  │  Threads │    │  :15     │    │  retry ×3 on bad slides  │  │
│  │  :30     │    └──────────┘    └──────────────────────────┘  │
│  └──────────┘                                                   │
│       │                                                         │
│       ▼                                                         │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              ANALYTICS FEEDBACK LOOP                      │   │
│  │  23:00 — fetch Threads engagement → topic_boosts          │   │
│  │  23:00 — LLM deep analysis → recommendations.json        │   │
│  │  Next day: pipeline consumes boosts + recommendations     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Core Principle:** Pipeline generates content → staging file → post reads staging → publishes to Threads. The staging file is the handoff mechanism between generate and post.

---

## 2. Cron Schedule Overview

| Time (WIB) | Job | Script | Action | Delivery |
|------------|-----|--------|--------|----------|
| `:00` | **Pipeline** | `pressbox-pipeline-v7.py` | Scrape → Filter → Score → Extract → LLM → Stage | local |
| `:15` | **Check Staging** | `pressbox-check-staging.py` | If staging empty → re-run pipeline | local |
| `:30` | **Post** | `pressbox-post.py` | Read staging → Post to Threads → Verify | origin (Telegram) |
| `23:00` | **Analytics Feedback** | `pressbox-analytics-feedback.py` | Fetch engagement → topic_boosts.json | local |
| `23:00` | **Analytics LLM** | `pressbox-analytics-llm.py` | Deep LLM analysis → recommendations.json | origin |
| `23:00 (Sun)` | **Weekly Analytics** | `pressbox-analytics-llm.py --weekly` | 7-day deep analysis | origin |

**Timing Flow Per Hour:**
```
:00 Pipeline runs (~30-50s) → writes staging.json
:15 Check Staging → verifies staging has content → if empty, re-runs pipeline
:30 Post → reads staging → posts to Threads → clears staging
:31-:59 → staging empty, next hour's pipeline will fill it at :00
```

---

## 3. Stage 1: Research + Generate (Pipeline)

**File:** `pressbox-pipeline-v7.py` (778 lines)
**Trigger:** Every hour at `:00`
**Timeout:** 120s cron limit

### 3.1 Scrape (Parallel)

```python
# 3 sources scraped concurrently via ThreadPoolExecutor(max_workers=3)
├── Guardian RSS: theguardian.com/football/rss (14 items)
├── Mirror scrape: mirror.co.uk/sport/football/news/ (per-article HTML)
└── Sky Sports RSS: skysports.com/rss/11095 (12 items)
```

**Research module** (`pressbox-research.py`, 326 lines):
- Generic RSS scraper: parses `<item>` elements, extracts title/link/description/pubdate/image
- Mirror scraper: per-article HTML scrape for og:title, og:description, og:image
- Image extraction from RSS: `media:content` namespace, picks largest width, upgrades to 1200px
- Viral keyword matching: HIGH_VIRAL (5pts each) + MED_VIRAL (3pts each)
- WC boost: +50 pts for World Cup keywords
- Transfer boost: +13 pts for transfer keywords
- Deduplication: overlap ratio 0.60-0.85 depending on source + WC status
- Returns top 25 topics (so pipeline has enough after filtering posted)

**Output:** ~30-40 topics with title, url, source, score, wc_boost, transfer_related, viral_related, image_url

### 3.2 Filter

```
For each topic, apply these filters in order:
├── ✗ Empty title/url → skip
├── ✗ Source not in {guardian, mirror, skysports} → skip
├── ✗ URL already in posted_topics.json → skip (exact match)
├── ✗ URL in scrape_cache.json (30-min window) → skip (recent scrape)
├── ✗ Title similar to posted titles (Jaccard ≥ 0.35) → skip (semantic dedup)
├── ✗ Topic type in skip_topics from analytics → skip (low-performing)
├── ✓ Keyword boost from analytics_recommendations.json → +10 pts per hit
└── ✓ Pass → add to filtered list
```

**Similarity Detection** (`is_similar`):
- Lowercases + applies team name shortening (e.g., "Manchester City" → "Man City")
- Removes stopwords (50 common English words)
- Jaccard similarity: `|intersection| / |union|` ≥ 0.35 → duplicate
- This catches variations like "Ronaldo leaves Man Utd" vs "Cristiano Ronaldo departs Manchester United"

### 3.3 Score — Pick Best

**Scoring formula** (cumulative):
```
Base score from research module (8-15 pts)
+ Controversy keywords: +30 (outrage, scandal, banned, boycott, protest, chaos, crisis)
+ Drama keywords: +20 (secret, hidden, exposed, shocking, epic, comeback, revenge)
- Boring keywords: -15 (quiz, lineup, live updates, preview, analysis, opinion)
+ Short title (≤8 words): +15
- Long title (>15 words): -10
+ World Cup keywords: +50 (world cup, fifa, qualifier, wc 2026)
+ WC boost flag: +40
+ Viral boost flag: +25
+ Analytics topic_boosts multiplier (e.g., "transfer_rumor": 1.5x)
+ Analytics keyword_additions: +10 pts per keyword hit
```

**Pick:** Highest scoring topic → `best`

### 3.4 Extract

```
curl -sL --max-time 10 -A "Mozilla/5.0" <article_url>
├── Extract raw HTML
├── Strip HTML tags → article_text (first 2000 chars)
├── Extract og:image via regex (4 patterns: og:image, twitter:image)
│   ├── HEAD check (curl -sIL) → must return HTTP 200
│   └── validate_image_quality() → width ≥ 400px, ratio 0.5-2.5
├── Fallback 1: extract_body_image() → first <img> in article body
│   ├── Parse HTML for <article>, <main>, <div class="article|story|content|post">
│   ├── Skip tiny images: icon, logo, avatar, pixel, spacer, 1x1, badge
│   └── HEAD check + validate_image_quality()
└── Fallback 2: RSS image_url from research module
    └── HEAD check + validate_image_quality()
```

**Article text:** First 2000 chars of stripped HTML → fed to LLM

### 3.5 LLM Generate (8 Slides)

**Model:** `deepseek-v4-flash` via OpenCode API
**Max tokens:** 6000
**Temperature:** 0.7
**Reasoning effort:** "low"
**Timeout:** 180s per attempt
**Max retries:** 3

**System prompt:**
```
You are a slide content generator. You think briefly, then output immediately.
RULES:
- Reason for NO MORE than 3-4 sentences total
- Do not explore alternatives or second-guess
- Output ONLY valid JSON, no markdown, no explanation
- Start your response with { immediately after thinking
```

**User prompt includes:**
- Article text (2000 chars)
- Source URL
- Slide rules (150-300 chars for slide 1, 250-450 chars for slides 2-7)
- JSON format template
- Tone adjustment from analytics recommendations
- CTA pattern from analytics recommendations

**Slide Structure:**
```json
{
  "slide_1": {"title": "HOOK", "content": "1-2 punchy sentences, 150-300 chars", "image_url": "..."},
  "slide_2": {"title": "THE PROBLEM", "content": "What happened, 250-450 chars"},
  "slide_3": {"title": "THE CONTEXT", "content": "Why it matters, 250-450 chars"},
  "slide_4": {"title": "THE COMPARISON", "content": "Similar past, 250-450 chars"},
  "slide_5": {"title": "HUMAN ANGLE", "content": "Quotes/emotion, 250-450 chars"},
  "slide_6": {"title": "BIGGER PICTURE", "content": "Implications, 250-450 chars"},
  "slide_7": {"title": "THE STAKES", "content": "Climax before CTA, 250-450 chars"},
  "slide_8": {"title": "PROVOCATIVE QUESTION?", "content": "3 sentences + Source URL"}
}
```

**JSON Extraction (critical path):**
```python
# 1. Try content field first
if content:
    candidate_json = strip_markdown_fences(content)

# 2. If content empty (deepseek-v4-flash puts JSON in reasoning), extract from reasoning
if not candidate_json and reasoning:
    # Find first '{' in reasoning
    # Count brace depth: { = +1, } = -1
    # When depth == 0 → extract that JSON object
    # Validate: must be dict with ≥3 keys
```

**Retry Logic:**
- Attempt 1-3: each gets fresh LLM call
- If no JSON found → retry
- If char count fails (slides outside 150-450 range) → retry
- If all 3 attempts fail → exit(1), pipeline fails

### 3.6 Validate & Stage

**Post-LLM validation:**
```
✓ All 8 slides present (slide_1 through slide_8)
✓ Each slide is a dict with "title" and "content"
✓ Slide 1: 150-300 chars
✓ Slides 2-7: 250-450 chars (exit(1) if outside range)
✓ Slide 8: must contain "?" in title
✓ Slide 8 content: trimmed to 400 chars max at sentence boundary
```

**Staging write (atomic):**
```python
staging_obj = {
    "schema_version": 1,
    "status": "ready",
    "topic": best,           # Full topic object from research
    "content": joined,       # 8 slides joined by "\n---\n"
    "written_at": "2026-06-17T18:00:00+07:00",
    "is_wc": True/False,
    "is_transfer": True/False,
    "mode": "thread",
    "slides": 8,
    "image_url": "https://...",
    "image_width": 1200,
    "image_height": 674,
}

# Atomic write: write to .tmp, then os.replace() → staging.json
tmp = STAGING + ".tmp"
write_to(tmp, staging_obj)
os.replace(tmp, STAGING)
```

---

## 4. Stage 2: Check Staging

**File:** `pressbox-check-staging.py` (71 lines)
**Trigger:** Every hour at `:15`
**Purpose:** Safety net — if pipeline failed at `:00`, re-run it

**Logic:**
```
1. Check if staging.json exists AND has topic + content
   ├── YES → exit(0), staging ready for :30 post
   └── NO → re-run pipeline with retries

2. Re-run pipeline:
   ├── MAX_RETRIES = 1 (just 1 attempt)
   ├── subprocess.run(pressbox-pipeline-v7.py, timeout=180)
   ├── If exit(0) → success
   └── If exit(non-0) → log error, :30 post will have nothing

3. Also checks staging-v3.json (legacy fallback)
```

**Known Issue:** The check script references `pressbox-pipeline-v2.py` (line 13), not v7. This is a bug — it should reference v7.

---

## 5. Stage 3: Post to Threads

**File:** `pressbox-post.py` (243 lines)
**Trigger:** Every hour at `:30`
**Delivery:** Sends result to Telegram (origin)

### 5.1 Pre-Post Checks

```python
# 1. BAD HOUR CHECK
if is_bad_hour():      # checks analytics_feedback.json → worst_hours
    skip               # Don't post during low-engagement hours

# 2. FREQUENCY CHECK  
if is_posting_too_frequent():  # posted < 30 min ago
    skip               # Quality > Quantity
```

### 5.2 Post Flow

```
1. Read staging.json
   ├── Check staging-v3.json first (legacy), then staging.json
   ├── Must have topic + content
   └── If empty → exit(0), silent

2. Write content to latest.md
   └── staging["content"] → ~/.hermes/content-pipeline/drafts/football/latest.md

3. Post to Threads via pressbox-direct-post.py
   ├── --file latest.md
   ├── --image <image_url> (if available)
   ├── Parses slides by "---" separator
   ├── Posts root slide first (with image if available)
   ├── Creates reply chain: each slide replies to previous
   ├── 0.5s delay between slides for indexing
   └── Returns root_id + permalink

4. Extract root_id and permalink from output

5. SAFETY: if partial post (< 4 slides), auto-delete
   └── python3 pressbox-direct-post.py --delete <root_id>

6. Verify post via verify-last-slide.py

7. Update posted_topics.json tracking
   ├── Find [PENDING] entry or create new
   └── Store: title, post_id, timestamp, source, description

8. Clear staging (both staging.json and staging-v3.json)

9. Clear latest.md

10. Report to Telegram
    ├── ✅ Posted: <title> (8 slides)
    └── https://www.threads.com/@parkthebus.football/post/<root_id>
```

### 5.3 Direct Post Details

**File:** `pressbox-direct-post.py` (290 lines)

**Container creation flow:**
```
For each slide:
1. create_container(media_type="TEXT", text=slide_content, reply_to_id=parent)
   ├── If root slide + image_url → try IMAGE container first
   │   ├── If IMAGE fails → fallback to TEXT
   │   └── Retry on HTTP 500 / transient errors (1 retry)
   └── TEXT container with reply_to_id

2. publish(container_id)
   └── Returns published post ID

3. If root slide → fetch permalink via API

4. Wait 0.5s for indexing

5. Chain: parent_pid = current_pid
```

**Image attachment:**
- Only root slide (slide 1) gets image
- Uses `media_type: "IMAGE"` with `image_url` parameter
- If image fails (HTTP 500, transient, timeout) → falls back to TEXT
- Image URL must be publicly accessible (no auth)

---

## 6. Analytics Feedback Loop

### 6.1 Analytics Feedback (Fast)

**File:** `pressbox-analytics-feedback.py` (186 lines)
**Trigger:** Daily at 23:00 WIB
**Output:** `analytics_feedback.json`

**Process:**
```
1. Fetch last 20 posts from Threads API
2. For each post, fetch engagement: likes, replies, reposts, views, quotes
3. Calculate score: likes×1 + replies×3 + reposts×2 + quotes×2
4. Classify each post by topic type (world_cup, transfer, controversy, etc.)
5. Classify posting hour (WIB)
6. Generate:
   ├── topic_boosts: {topic: multiplier} (e.g., "transfer_rumor": 1.5)
   ├── skip_topics: [{pattern, avg_score, instances}]
   ├── best_hours: [top 3 hours by avg score]
   └── worst_hours: [hours with ≥70% dead posts]
```

**Topic Classification:**
```python
"world_cup": ["world cup", "fifa", "qualifier", "2026"]
"transfer": ["transfer", "signing", "deal", "bid", "join"]
"controversy": ["controversy", "scandal", "banned", "fined", "racism"]
"match_result": ["win", "lose", "defeat", "victory", "beat"]
"injury": ["injury", "injured", "sidelined"]
"team_profile": ["guide", "profile", "squad", "lineup"]
"gossip": ["rumour", "reportedly", "linked"]
"young_talent": ["young", "academy", "debut"]
"record": ["record", "history", "milestone"]
```

**Boost Logic:**
```python
# Per topic type:
ratio = topic_avg_score / overall_avg_score
if ratio >= 1.5:
    boosts[topic] = min(ratio, 3.0)    # Cap at 3x
elif ratio < 0.5:
    boosts[topic] = 0.3                 # Penalize low performers
```

### 6.2 Analytics LLM (Deep)

**File:** `pressbox-analytics-llm.py` (589 lines)
**Trigger:** Daily at 23:00 WIB (with feedback)
**Output:** `analytics_recommendations.json`
**Model:** `deepseek-v4-flash`

**Process:**
```
1. Fetch posts (last 24h daily, last 168h weekly)
2. For each post:
   ├── Fetch engagement metrics
   ├── Fetch last reply text (traverse reply chain, slide 8 CTA)
   ├── Classify hook type (negative_hook, credibility_result, contrarian, etc.)
   ├── Classify topic type
   └── Check if has CTA (question mark in last line or last reply)
3. Build aggregates:
   ├── Hook distribution (count, avg replies, avg score per hook type)
   ├── Topic distribution (count, avg replies, avg score per topic)
   ├── Hourly performance (posts, replies, views per hour)
   ├── CTA analysis (with CTA vs without CTA avg replies)
   └── Dead posts (<100 views, 0 replies)
4. Send to LLM for deep analysis
5. Save recommendations
```

**LLM Output Structure:**
```json
{
  "summary": {"engagement_rate", "top_performer_insight", "biggest_gap"},
  "topic_analysis": {"best_topic_types", "worst_topic_types", "topic_recommendation"},
  "hook_analysis": {"best_hook", "hook_performance", "hook_recommendation"},
  "cta_analysis": {"cta_effectiveness", "best_cta_pattern", "cta_recommendation"},
  "timing_analysis": {"best_hours", "worst_hours", "timing_recommendation"},
  "content_gaps": {"missing_topics", "missed_opportunities", "gap_recommendation"},
  "ab_testing": {"test_to_run", "hypothesis", "success_metric"},
  "research_tweaks": {"topic_priority_shift", "source_tweaks", "keyword_additions", "keyword_removals"},
  "generate_tweaks": {"preferred_hooks", "hook_assignments", "slide_count", "tone_adjustment", "cta_pattern", "length_adjustment"},
  "experiments": ["1 specific experiment to try next period"],
  "action_items": ["3-5 concrete actions sorted by impact"]
}
```

### 6.3 How Feedback Flows Into Pipeline

```
analytics_feedback.json
├── topic_boosts → score_topic() multiplier (line 193-195 in pipeline)
├── skip_topics → filter out low-performing topic types (line 420-421)
├── best_hours → available but NOT yet used by pipeline (stale check >48h)
├── worst_hours → used by pressbox-post.py to skip bad hours (line 79-90)
└── generated_at → stale check: >48h old → use defaults

analytics_recommendations.json
├── research_tweaks.keyword_additions → +10 pts per keyword hit (line 423-427)
├── research_tweaks.keyword_removals → NOT yet implemented
├── generate_tweaks.tone_adjustment → injected into LLM prompt (line 567)
├── generate_tweaks.cta_pattern → injected into LLM prompt (line 563)
├── generate_tweaks.preferred_hooks → NOT yet implemented
└── analysis → full LLM analysis available for manual review
```

---

## 7. File Structure & Data Flow

```
~/.hermes/
├── scripts/
│   ├── pressbox-pipeline-v7.py          # Main pipeline (778 lines)
│   ├── pressbox-research.py             # RSS scraper module (326 lines)
│   ├── pressbox-check-staging.py        # Staging validator (71 lines)
│   ├── pressbox-post.py                 # Post orchestrator (243 lines)
│   ├── pressbox-direct-post.py          # Threads API client (290 lines)
│   ├── pressbox-analytics-feedback.py   # Fast analytics (186 lines)
│   ├── pressbox-analytics-llm.py        # LLM deep analysis (589 lines)
│   └── verify-last-slide.py             # Post verification
│
├── pressbox/
│   ├── staging.json                     # Current content to post (atomic write)
│   ├── staging.json.tmp                 # In-progress write (renamed to staging.json)
│   ├── staging-v3.json                  # Legacy staging (checked first)
│   ├── posted_topics.json               # All posted topics (dedup database)
│   ├── analytics_feedback.json          # Topic boosts, best/worst hours
│   ├── analytics_recommendations.json   # LLM-generated recommendations
│   ├── analytics_report.md              # Human-readable analytics report
│   ├── scrape_cache.json                # 30-min cache for scraped topics
│   ├── pipeline_errors.log              # Error log
│   └── draft-*.txt                      # Draft content (legacy)
│
├── content-pipeline/drafts/football/
│   └── latest.md                        # Current content being posted
│
├── threads_token.json                   # Threads API OAuth token
└── .env                                 # API keys (OPENCODE_GO_API_KEY, etc.)
```

**Data Flow Per Cycle:**
```
:00 Pipeline
  ├── reads: posted_topics.json, scrape_cache.json, analytics_feedback.json, analytics_recommendations.json
  ├── writes: staging.json (atomic via .tmp)
  └── side effects: pipeline_errors.log

:15 Check Staging
  ├── reads: staging.json
  └── writes: staging.json (if re-runs pipeline)

:30 Post
  ├── reads: staging.json, posted_topics.json, analytics_feedback.json
  ├── writes: posted_topics.json (add new entry), staging.json (clear), latest.md (write then clear)
  └── side effects: Threads API calls, Telegram notification

23:00 Analytics
  ├── reads: threads_token.json, posted_topics.json
  ├── writes: analytics_feedback.json, analytics_recommendations.json, analytics_report.md
  └── side effects: Threads API calls (fetch posts + engagement)
```

---

## 8. LLM Configuration

### Pipeline LLM (Slide Generation)

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Model** | `deepseek-v4-flash` | Via OpenCode API |
| **API** | `opencode.ai/zen/go/v1/chat/completions` | |
| **Max tokens** | 6000 | Must fit 8 slides of 250-450 chars each |
| **Temperature** | 0.7 | Balance creativity/consistency |
| **Reasoning effort** | `low` | Reduces reasoning token usage |
| **Timeout** | 180s | Per attempt |
| **Max retries** | 3 | On JSON parse failure or char count fail |

**Known Issue: deepseek-v4-flash Reasoning Problem**
- deepseek-v4-flash sometimes puts ALL output in `reasoning_content` field
- `content` field comes back empty
- Pipeline has fallback: extract JSON from `reasoning_content` via brace counting
- This works but wastes ~4000 tokens on reasoning vs direct output
- Alternative models tested: qwen3-32b via 9router (fallback)

### Analytics LLM (Deep Analysis)

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Model** | `deepseek-v4-flash` | Same model |
| **Max tokens** | 16000 | More complex output |
| **Temperature** | 0.5 | More analytical |
| **Timeout** | 120s | Single attempt |

---

## 9. Image Handling

### 3-Level Fallback Chain

```
Level 1: og:image from article HTML
├── Regex extract from <meta property="og:image"> (4 patterns)
├── HEAD check (curl -sIL) → must return HTTP 200
└── validate_image_quality() → width ≥ 400px, ratio 0.5-2.5

Level 2: First <img> from article body
├── HTMLParser: find <article>/<main>/<div class="article|story|content|post">
├── Extract first <img src> inside article container
├── Skip patterns: icon, logo, avatar, pixel, spacer, 1x1, badge
├── HEAD check → HTTP 200
└── validate_image_quality()

Level 3: RSS image_url from research module
├── Image from media:content in RSS feed
├── HEAD check → HTTP 200
└── validate_image_quality()
```

**Image Quality Gate** (`validate_image_quality`):
```python
# Downloads first 8KB of image (enough for header)
# Parses dimensions from binary header:
#   PNG: IHDR chunk at bytes 16-24 (width × height, big-endian uint32)
#   JPEG: SOF0/SOF1/SOF2 marker (width × height, big-endian uint16)

# Validation:
width >= 400px
AND height > 0
AND 0.5 <= (width/height) <= 2.5   # Reject tall/narrow images
```

**Image Sources by Publisher:**
| Source | og:image | Body img | RSS img | Notes |
|--------|----------|----------|---------|-------|
| Guardian | ✅ | ✅ | ✅ | Blocks some hotlinking but most work |
| Mirror | ✅ (via 308 redirect) | ✅ | N/A | Per-article scrape |
| Sky Sports | ✅ | ✅ | ✅ | RSS media:content |

**Image in Post:**
- Attached only to root slide (slide 1) via `media_type: "IMAGE"`
- If image fails → falls back to TEXT (no image)
- Staging stores: `image_url`, `image_width`, `image_height`

---

## 10. Error Handling & Guards

### Pipeline Guards

```
1. STAGING GUARD (line 262-276)
   if staging.json exists AND has topic + content AND status != "error":
       → exit(2), don't overwrite unposted content

2. SCRAPE FAILURE
   if all sources fail → exit(1)
   if individual source fails → log warning, continue with others

3. FILTER FAILURE
   if no topics after filter → exit(1)

4. ARTICLE EXTRACTION FAILURE
   if curl fails → exit(1)
   if article_text < 100 chars → exit(1)

5. LLM FAILURE
   if HTTP != 200 → exit(1)
   if no JSON found after 3 retries → exit(1)
   if char count fails after 3 retries → exit(1)

6. STAGING WRITE FAILURE
   if write fails → log_error() + exit(1)
```

### Post Guards

```
1. BAD HOUR CHECK
   if current_hour in worst_hours from analytics → skip post

2. FREQUENCY CHECK
   if posted < 30 min ago → skip post

3. STAGING EMPTY
   if staging.json has no topic/content → exit(0), silent

4. PARTIAL POST SAFETY
   if < 4 slides posted → auto-delete the thread

5. POST FAILURE
   if no root_id returned → send Telegram alert, clear staging
```

### Error Logging

```python
# pipeline_errors.log — append-only
# Format: [2026-06-17 18:00:32] Error message here

# Telegram alert on critical failures
def send_alert(msg):
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": "1022032312", "text": f"⚠️ PRESS BOX ERROR — {msg}"})
```

---

## 11. Quality Controls

### Content Quality

```
1. CHAR COUNT ENFORCEMENT
   ├── Slide 1: 150-300 chars (hook, shorter)
   ├── Slides 2-7: 250-450 chars (story arc)
   └── Slide 8: ≤400 chars (CTA, trimmed at sentence boundary)

2. SLIDE STRUCTURE
   ├── Must have exactly 8 slides
   ├── Each slide must have title + content
   ├── Slide 8 title must contain "?"
   └── Slides joined by "\n---\n" separator

3. CONTENT RULES (in LLM prompt)
   ├── FACTS ONLY from article (no hallucination)
   ├── No em-dash (—)
   ├── No hashtags
   ├── No AI speak ("delve", "landscape", "crucial")
   ├── Conversational English
   └── Blank line between every 2 sentences

4. DEDUP
   ├── URL exact match (posted_topics.json)
   ├── URL recent match (scrape_cache.json, 30-min window)
   └── Title similarity (Jaccard ≥ 0.35)
```

### Posting Quality

```
1. TIMING
   ├── Skip worst_hours from analytics
   ├── Skip if posted < 30 min ago
   └── 0.5s delay between slides for indexing

2. SAFETY
   ├── Auto-delete if < 4 slides posted
   ├── Single attempt (no retry to fit 120s cron)
   └── Telegram alert on failure

3. TRACKING
   ├── Every post recorded in posted_topics.json
   ├── Post ID, timestamp, source, description
   └── Used for dedup in future cycles
```

---

## 12. Known Issues & Improvement Areas

### Critical Issues

| # | Issue | Impact | Status |
|---|-------|--------|--------|
| 1 | **Check Staging references v2 script** | May re-run wrong pipeline version | Bug — needs fix |
| 2 | **deepseek-v4-flash reasoning waste** | ~4000 tokens wasted on reasoning content | Workaround: extract JSON from reasoning_content |
| 3 | **No retry on post failure** | Single attempt, if Threads API fails → content lost | By design (120s cron limit) |

### Improvement Opportunities

| # | Area | Current State | Suggested Improvement |
|---|------|---------------|----------------------|
| 1 | **best_hours not used by pipeline** | Analytics generates best_hours but pipeline ignores it | Make :30 Post time-aware, shift post window |
| 2 | **topic_boosts too coarse** | Only multiplies "general" by 0.3x | Per-topic granularity (transfer: 1.5x, controversy: 1.8x) |
| 3 | **keyword_removals not implemented** | Analytics generates removals, pipeline ignores | Add negative scoring for removed keywords |
| 4 | **preferred_hooks not implemented** | LLM recommends hooks, pipeline doesn't use them | Inject hook formulas into LLM prompt per topic type |
| 5 | **No image quality tracking** | Can't measure fallback frequency | Log which fallback level succeeded per post |
| 6 | **Stale analytics ignored silently** | >48h old analytics → defaults, no alert | Send Telegram alert when using stale defaults |
| 7 | **No content performance correlation** | Don't know which slide structures perform best | Track slide content patterns vs engagement |
| 8 | **Single LLM model** | deepseek-v4-flash only | Could fallback to qwen3-32b via 9router |
| 9 | **No A/B testing framework** | LLM suggests experiments, no way to run them | Implement hook/tone variation tracking |
| 10 | **Duplicate log_error function** | Defined twice in pipeline-v7.py (lines 78-86 and 257-260) | Remove duplicate |

### Architecture Limitations

```
1. STAGING AS SINGLE HANDOFF POINT
   └── If pipeline crashes mid-write, staging could be corrupt
       → Atomic write (.tmp + os.replace) mitigates this
       → But no schema validation on read

2. NO CONTENT CACHING
   └── Each pipeline run scrapes fresh, even if same article is top
       → scrape_cache.json only covers 30 min, not cross-hour

3. LINEAR POSTING (no parallelism)
   └── 8 slides posted sequentially (0.5s each = 4s total)
       → Could parallelize root + first reply

4. NO ENGAGEMENT PREDICTION
   └── Pipeline can't predict which topic will perform best
       → Only uses historical topic boosts, not real-time signals

5. NO CROSS-POST ANALYTICS
   └── Don't know which slides drive engagement
       → Could track per-slide metrics via reply chain traversal
```

---

## Appendix: Cron Job IDs

| Job | ID | Schedule | Status |
|-----|-----|----------|--------|
| PRESS BOX — Pipeline | `947200b793a7` | `0 * * * *` | ✅ active |
| PRESS BOX — Check Staging | `7023479917e8` | `15 * * * *` | ✅ active |
| PRESS BOX — Post | `783c6bf97144` | `30 * * * *` | ✅ active |
| Press Box Analytics Feedback | `b341c2a287b9` | `0 23 * * *` | ✅ active |
| PRESS BOX — Daily Analytics LLM | `3a8e8174e9b6` | `0 23 * * *` | ✅ active |
| PRESS BOX — Weekly Analytics LLM | `ec5cab5397b9` | `0 23 * * 0` | ✅ active |
| Clean Cache Daily | `68ecedfb5073` | `0 5 * * *` | ✅ active |

---

*Document generated: 2026-06-17 18:35 WIB*
*Pipeline version: v7 (pressbox-pipeline-v7.py, 778 lines)*
*GitHub: hadijayyy/pressbox-pipeline*
