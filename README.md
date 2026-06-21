# 📰 Press Box — Automated Football Content Pipeline

**Auto-publish football news threads to Threads (@parkthebus.football)**

Press Box is a fully automated content pipeline that scrapes football news (Mirror, Sky Sports, Goal.com), generates 6-slide threads via LLM (Mistral primary → MiniMax-M3 fallback via tokenrouter), and posts them to Threads with relevant images — completely unattended.

## ⚡ Performance

| Metric | v6.0 | v7.2 | v7.3 | Improvement (v7.3 vs v6.0) |
|--------|------|------|------|---------------------------|
| LLM time | 67.7s | 30.9s | **21.0s** | **69% faster** |
| Total time | 73.1s | 39.9s | **~30s** | **59% faster** |
| Tokens | 8,635 | 4,400 | **3,300-5,500** | **~50% reduction** |
| Completion tokens | 7,025 | 2,793 | **~2,500-3,500** | **~55% reduction** |
| Prompt tokens | 1,610 | 1,607 | **~700** | **57% reduction** |
| First-try pass rate | — | 100% | **100% (3/3 dry-runs)** | — |
| Hallucinations detected | — | minor slips | **0** | — |

**Key changes:** Per-slide MIN sentence tags (anti under-write), strict GROUNDING rules with verbatim article-only fact extraction, REJECTION JSON for insufficient articles, model chain cycling (Mistral primary → MiniMax-M3 fallback).

## ✨ Features

- **Automated research** — scrapes Mirror RSS + Sky Sports RSS + Goal.com for fresh football news
- **Smart dedup** — URL-based, title Jaccard similarity (35% threshold), 30-min article cache
- **LLM-generated threads** — 6 slides with sentence-count blueprints and strict grounding rules
- **Sentence-count validation** — replaces char-count, ensures consistent slide density
- **3-level image fallback** — og:image → article body `<img>` → RSS image
- **Image validation** — HEAD request checks every image URL before use
- **Content filtering** — skips women's football (title, description, URL)
- **Strategy 2 JSON extraction** — score-based fallback for reasoning-heavy responses
- **Analytics feedback** — daily engagement analysis tunes posting hours
- **Atomic staging** — tmp + os.replace prevents corruption
- **Auto-recovery** — `:15` check-staging runs pipeline if staging is empty
- **Cron notifications** — all 3 cron jobs notify chat on success and failure

## 🏗 Architecture

```
:00 Pipeline ──► scrape (Mirror + Sky Sports + Goal.com)
                  ├── filter (dedup, similarity, freshness, women's football)
                  ├── score (WC boost +50, viral +25)
                  ├── extract (article text + 3-level image fallback)
                  ├── LLM generate (Mistral primary → MiniMax-M3 fallback, v7.3 anti-hallucination prompt)
                  ├── validate (sentence count per slide, auto-trim if over)
                  └── stage (~/.hermes/pressbox/staging.json)

:15 Check Staging ──► if staging empty → auto-run pipeline
                       └── notify chat (success or failure)

:30 Post ──► read staging → post to Threads (slide-by-slide)
              ├── verify ≥ 4 slides posted (auto-delete partial)
              ├── fetch alphanumeric permalink via /me/threads API
              └── notify chat with link + title
```

### Cron Schedule

| Time | Script | Deliver | Purpose |
|------|--------|---------|---------|
| `:00` | `pressbox-pipeline-v7.py` | chat | Pipeline generate |
| `:15` | `pressbox-check-staging.py` | chat | Verify/recover staging |
| `:30` | `pressbox-post.py` | chat | Post to Threads |
| `23:00` | `pressbox-analytics-feedback.py` | local | Daily analytics |
| `23:00` | `pressbox-analytics-llm.py` | local | LLM deep analysis |

### Flow per hour

```
:00  Pipeline runs → staging.json written (6 slides + image)
:15  Check staging → if empty, auto-run pipeline as recovery
:30  Post reads staging → posts 6-slide thread → staging cleared
     → chat receives: ✅ Title\n   https://threads.com/.../DZxxx
```

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- Threads API access token (Meta Graph API)
- Mistral API key (primary) + TokenRouter API key (fallback via custom provider)
  - Or any OpenAI-compatible LLM endpoint (Mistral large recommended)

### Installation

```bash
git clone https://github.com/hadijayyy/pressbox-pipeline.git
cd pressbox-pipeline
pip install httpx requests feedparser
```

### Configuration

Copy `.env.example` to `~/.hermes/.env`:

```bash
cp .env.example ~/.hermes/.env
```

Edit with your credentials:

```env
MISTRAL_API_KEY=your_mistral_api_key
MISTRAL_BASE_URL=https://api.mistral.ai/v1
TOKENROUTER_API_KEY=your_tokenrouter_api_key
TOKENROUTER_BASE_URL=https://api.tokenrouter.com/v1
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
THREADS_ACCESS_TOKEN=your_threads_access_token
THREADS_USER_ID=your_threads_user_id
```

### Threads API Token Setup

1. Create a Meta App with Threads API enabled
2. Generate a long-lived access token
3. Save to `~/.hermes/threads_token.json`:

```json
{
  "access_token": "***",
  "user_id": "your_numeric_user_id"
}
```

## 📁 Scripts

| Script | Description |
|--------|-------------|
| `pressbox-auto-adjust.py` | Pure rule-based auto-adjustment (no LLM, zero failure rate) |
| `pressbox-research.py` | RSS scraper — Mirror, Sky Sports, Goal.com |
| `pressbox-pipeline-v7.py` | **Main pipeline** — scrape → filter → score → extract → LLM → stage |
| `pressbox-check-staging.py` | Recovery job — runs pipeline if staging is empty |
| `pressbox-post.py` | Post manager — reads staging, calls direct-post, updates tracking |
| `pressbox-direct-post.py` | Low-level Threads Graph API client — IMAGE/TEXT container creation |
| `pressbox-analytics-feedback.py` | Daily analytics — topic boosts, best/worst hours |
| `pressbox-analytics-llm.py` | LLM deep analysis — hooks, CTA, topic recommendations |

### Pipeline Flow

```
pressbox-pipeline-v7.py:
  1. Parallel scrape Mirror RSS + Sky Sports RSS + Goal.com (5-10s)
  2. Filter candidates (URL dedup, Jaccard similarity 35%, 30-min cache, women's football)
  3. Score with WC boost (+50), viral keywords (+25)
  4. Pick best candidate
  5. Extract article text via curl (with 30-min cache)
  6. Extract article image:
     a. og:image meta tag → HEAD validate
     b. Article body <img> (first content image) → HEAD validate
     c. RSS image URL → HEAD validate
  7. LLM generate 6-slide JSON (Mistral primary → MiniMax-M3 fallback, v7.3 anti-hallucination prompt)
     - {url} injected into system prompt via .replace() before LLM call
     - Auto-trim over-sentence slides (cuts to SENTENCE_COUNTS max, no reject)
     - Post-parse URL append on slide 6 (bulletproof, regardless of model behavior)
  8. Validate sentence count per slide (auto-trim → sentence cap 500 chars)
  9. Save to staging.json (atomic write)
```

### Post Flow

```
pressbox-post.py:
  1. Read staging.json (v2 or v3)
  2. Duplicate check via posted_topics.json
  3. Write content to latest.md
  4. Call pressbox-direct-post.py (timeout 100s)
  5. Extract post IDs from output (digit lines + "→ {pid}" pattern)
  6. Safety: if < 4 slides posted → auto-delete thread
  7. Fetch alphanumeric permalink via GET /me/threads API
  8. Update posted_topics.json
  9. Clear staging
  10. Print: ✅ Title\n   permalink
```

### LLM Prompt (v7.3)

Anti-hallucination strict grounding with per-slide fallbacks:

```
[SOURCE HANDLING]
Use only the article body — actual reported content. Ignore nav, related links, ads, bylines, boilerplate.

[SLIDES — every slide MUST hit the MINIMUM sentence count, no exceptions]
1. HOOK (1-3 sentences, MIN 1): Most controversial/surprising/paradoxical fact, quote, or stat.
2. WHAT (3-4 sentences, MIN 3): What happened, concretely, why it matters. No filler.
3. TENSION (2-4 sentences, MIN 2): Conflict/disagreement/competing stakes.
   One-sided article: "Article only covers [X]'s perspective."
4. HUMAN (2-4 sentences, MIN 2): One person, their words/feelings.
   No quote: "No direct quote from [Name] in this report" + what is known.
5. UNRESOLVED (2-3 sentences, MIN 2): What the article leaves open.
6. CTA (2-4 sentences, MIN 2): Sharp opinion + debatable yes/no question.
   Last line: {url}

[GROUNDING — STRICT]
- Facts, names, quotes, scores, dates: VERBATIM from the article. NO outside knowledge.
- Missing detail? OMIT. Never invent, assume, or paraphrase. Brevity beats fabrication.
- Slides 5-6: opinion allowed, but derived from article facts — not general football wisdom.
- If article cannot fill 6 slides honestly: {"error":"insufficient_source","slides_produced":N,"reason":"..."}

[STYLE]
- Conversational English. Every sentence followed by \\n\\n. New fact per slide. No repetition.
- BANNED phrases: "fans were left in shock", "the beautiful game", "at the end of the day", "only time will tell", "stunning", "incredible journey", and anything in that register.
- No em-dash (—), hashtags, bullet points, ALL CAPS, AI throat-clearing.
- Indonesian articles: keep names original, prose in English.
```

Key rules:
- Per-slide MIN sentence tags (prevents under-write flakiness)
- GROUNDING is strict: article-only verbatim facts, no outside knowledge
- REJECTION JSON emitted if article insufficient (no padding)
- 6 slides (not 8) — streamlined per empirical testing
- `{url}` in slide_6 replaced before LLM call via `system_prompt.replace("{url}", url)`
- Post-parse URL append as bulletproof backstop

See [`prompts/pressbox-prompt-v7.3.md`](prompts/pressbox-prompt-v7.3.md) for the full system prompt.

## 🖼️ Image Support

The pipeline automatically attaches the article's main image to the **first slide** of every thread.

### 3-Level Fallback Chain

```
1. og:image meta tag → HEAD validate (check HTTP 200)
2. Article body <img> → HEAD validate (first content image, skip icons/logos)
3. RSS image URL → HEAD validate
```

### Supported Sources

| Source | og:image | Body img | RSS img | Status |
|--------|----------|----------|---------|--------|
| Mirror | ✅ | ✅ | ✅ | Works |
| Sky Sports | ✅ | ✅ | ✅ | Works |
| Goal.com | ✅ | ✅ | ✅ | Works |
| Guardian | ❌ CDN blocked | — | — | Skipped |

## 📊 Analytics Feedback Loop

### Daily (23:00 WIB)

**analytics-feedback.py:**
- Fetches last 20 posts via Threads API
- Calculates avg engagement per topic
- Generates topic boosts (1.5x-3x high, 0.3x low)
- Saves to `analytics_feedback.json`

**analytics-llm.py:**
- LLM deep analysis of hooks, CTA effectiveness, topic performance
- Traverses nested reply chains to detect CTA (slide 8)
- Saves recommendations to `analytics_recommendations.json`
- Generates markdown report

### Analytics Data Flow

```
analytics_feedback.json
  └──► pressbox-pipeline-v7.py (scoring boost/skip)

analytics_recommendations.json
  └──► LLM analysis → report (human review)
```

## 🔧 Data Files

| Path | Purpose |
|------|---------|
| `~/.hermes/pressbox/staging.json` | Pipeline output → Post input |
| `~/.hermes/pressbox/posted_topics.json` | URL + title dedup tracking |
| `~/.hermes/pressbox/analytics_feedback.json` | Topic boosts from analytics |
| `~/.hermes/pressbox/analytics_recommendations.json` | LLM analysis results |
| `~/.hermes/pressbox/metrics.jsonl` | Per-run metrics (tokens, timing, slides) |
| `~/.hermes/threads_token.json` | Threads API token |
| `~/.hermes/.env` | API keys |

## 🛡️ Error Handling

| Issue | Handling |
|-------|----------|
| URL fails | Skip candidate, try next |
| LLM timeout | Retry up to 3x |
| Sentence count fail | Retry with stricter prompt |
| JSON parse failure | Strategy 1: slide markers → Strategy 2: score-based fallback |
| `{url}` in slide_8 | Injected via `system_prompt.replace()` before LLM call |
| Image og:image fails | Fallback to body `<img>` → RSS |
| Image HEAD check fails | Skip image, proceed text-only |
| Staging empty at :15 | Auto-run pipeline (recovery) |
| Partial post (< 4 slides) | Auto-delete thread, notify chat |
| Post ID extraction | Dual method: digit lines + `→ {pid}` pattern |
| Permalink format | Fetched via `GET /me/threads` (alphanumeric, not numeric container ID) |
| All candidates fail | Exit code 1 → check-staging recovery |

## 📝 Changelog

### v7.4 — Anti-Hallucination Prompt + Mistral Chain (2026-06-21)
- **Model chain**: Mistral `mistral-large-latest` (primary) → `MiniMax-M3` via tokenrouter (fallback)
- Per-model provider registry: `PROVIDERS` dict + `get_provider_for_model()` for clean URL/key routing
- **v7.3 anti-hallucination prompt** (501 words, ~700 tokens):
  - `[SOURCE HANDLING]` — explicit anti-pollution (ignore nav/ads/related)
  - Per-slide MIN sentence tags (prevents under-write flakiness)
  - `[REJECTION]` JSON `{"error":"insufficient_source",...}` for insufficient articles (no padding)
  - `[GROUNDING — STRICT]` — verbatim article-only fact extraction
  - Complete JSON FORMAT example (reduces format errors)
  - `[STYLE]` generalized banned-phrase rule + AI throat-clearing list
- **Auto-trim** replaces reject on over-sentence slides (cuts to SENTENCE_COUNTS max)
- **Think-tag strip** in content extraction (handles MiniMax-M3's `<think>...</think>` wrapper)
- **Post-parse URL append** on slide 6 (bulletproof backstop, regardless of model behavior)
- 8 → 6 slide format (streamlined per empirical testing)
- Verified: 3/3 dry-runs pass, 0 hallucinations on Mirror WC article
- See [`prompts/pressbox-prompt-v7.3.md`](prompts/pressbox-prompt-v7.3.md) for full prompt

### v7.3 — Post Reliability (2026-06-19)
- Fixed `{url}` not injected into slide_8 (`system_prompt.replace("{url}", url)`)
- Fixed partial post false-positive: dual post ID extraction (digit lines + `→` pattern)
- Fixed permalink format: use `/me/threads` API (alphanumeric `DZxxx`) not container ID
- Fixed `pressbox-check-staging.py` typo: `result.resultcode` → `result.returncode`
- All 3 cron jobs now deliver to chat (was: pipeline + check-staging were silent local)
- Pipeline prints to stdout for cron capture (was: `log()` stderr-only → always "silent")
- Auto-recovery: `:15` check-staging runs pipeline if staging empty

### v7.2 — Sentence Counts + Speed (2026-06-18)
- **54% faster LLM** (67.7s → 30.9s) via prompt optimization
- Sentence-count validation (replaced char-count 200-450)
- Per-slide sentence blueprints (2-5 sentences each)
- Removed Step 1 fact extraction (saves tokens + reasoning)
- Capped max_tokens at 6K (was 10K)
- Added women's football filter (title, description, URL)
- slide_6 explicitly exempt from grounding rules

### v7.1 — Data Extraction Agent (2026-06-17)
- 9x faster LLM (120s → 13s) via prompt engineering
- Rewrote prompt as "Data Extraction Agent" role
- Added Strategy 2 JSON extraction (score-based)
- Added article cache (30-min TTL)

### v7.0 — Initial Release
- 3-source scraper (Mirror + Sky Sports + Goal.com)
- LLM-generated 8-slide threads
- 3-level image fallback
- Analytics feedback loop

## 🤝 Contributing

PRs welcome! Focus areas:
- Additional sources (BBC, The Athletic)
- Multi-platform support (X/Twitter)
- A/B testing module

## 📄 License

MIT

---

*Built with ❤️ for @parkthebus.football*
