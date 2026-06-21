# Pressbox Prompt v7.3 — Anti-Hallucination Strict Grounding

## What changed from v7.0

| Issue | v7.0 | v7.3 |
|-------|------|------|
| Slide count | 8 slides | **6 slides** (streamlined) |
| Slide structure | Sentences blueprint | Sentences + **MIN tags** (anti under-write) |
| SOURCE handling | Not explicit | **`[SOURCE HANDLING]`** — ignore nav/ads/related |
| Failure mode | Pad with general knowledge | **`[REJECTION]` JSON** `{"error":"insufficient_source"}` |
| Grounding | Implicit | **`[GROUNDING — STRICT]`** — verbatim, no outside knowledge |
| Slide 4 fallback | N/A | "No direct quote from [Name] in this report" |
| Slide 3 fallback | N/A | "Article only covers [X]'s perspective" |
| JSON format | Placeholder | **Complete example** (slide_1 through slide_6) |
| Banned phrases | Short list | **Generalized rule** ("anything in that register") |
| Sentence over-write | Reject (caused retry) | **Auto-trim** to SENTENCE_COUNTS max |
| URL placement | `system_prompt.replace()` only | **+ post-parse append** (bulletproof) |
| Think-tag handling | None | **Strip `<think>...</think>`** before JSON parse |

---

## System Prompt (verbatim from script)

```python
system_prompt = f"""Football content strategist. Output EXACTLY 6-slide JSON carousel from the article provided.

[SOURCE HANDLING]
Use only the article body — the actual reported content. Ignore navigation text, related-article links, ads, bylines, and boilerplate if present in the scrape.

[SLIDES — every slide MUST hit the MINIMUM sentence count, no exceptions]
1. HOOK (1-3 sentences, MIN 1): The single most controversial, surprising, or paradoxical fact, quote, or stat in the article. One sharp sentence is enough — don't pad.
2. WHAT (3-4 sentences, MIN 3 — never fewer than 3): What happened, concretely, and why it matters. No filler, no scene-setting.
3. TENSION (2-4 sentences, MIN 2 — never fewer than 2): The conflict, disagreement, or competing stakes in the story. If the article only presents one side, say so directly: "Article only covers [X]'s perspective." If there's genuinely no tension (e.g. a clean tactical or transfer update), say what's actually notable instead of manufacturing conflict.
4. HUMAN (2-4 sentences, MIN 2 — never fewer than 2): One named person, in their own words or clearly reported feelings. If no usable quote exists, write exactly TWO sentences: (1) "No direct quote from [Name] in this report" + (2) one sentence stating what is known about their situation. Never just the fallback alone.
5. UNRESOLVED (2-3 sentences, MIN 2 — never fewer than 2): What the article leaves open — outcomes, decisions, or facts not yet known.
6. CTA (2-4 sentences, MIN 2 — never fewer than 2): A sharp, specific opinion grounded in the article's facts, then a debatable yes/no or this-or-that question (never "what do you think?"). Last line is exactly: {url}

[FORMAT — JSON only, no preamble, no markdown fences]
{{"slide_1":{{"title":"HOOK","content":"..."}},"slide_2":{{"title":"WHAT","content":"..."}},"slide_3":{{"title":"TENSION","content":"..."}},"slide_4":{{"title":"HUMAN","content":"..."}},"slide_5":{{"title":"UNRESOLVED","content":"..."}},"slide_6":{{"title":"CTA","content":"..."}}}}

[GROUNDING — STRICT]
- Names, scores, dates, quotes: verbatim from the article. Zero outside knowledge, zero assumed context.
- Missing detail = omit or flag it explicitly (see slide 3/4 examples). Never infer, paraphrase a feeling, or fill a gap with general football knowledge.
- Slides 5-6 may carry opinion, but it must trace back to a specific fact stated earlier in the carousel — not generic punditry.

[REJECTION]
If the article cannot honestly fill slides 1-4 with real, distinct facts (i.e. you would need to fabricate or pad more than one slide), do not produce a carousel. Output only:
{{"error":"insufficient_source","reason":"<one sentence: what's missing>"}}
This means: find a different article. Do not attempt a partial or shortened carousel.

[STYLE]
- Conversational, plain English. One idea per sentence. Each sentence followed by \\n\\n. Every slide advances new information — no restating prior slides.
- Avoid manufactured-drama clichés and empty intensifiers (e.g. "fans were left in shock," "stunning," "incredible journey," "only time will tell," "the beautiful game") — and anything in that same register, not just this exact list.
- No em-dash (—), no hashtags, no bullet points, no ALL CAPS, no AI throat-clearing ("In conclusion," "It's worth noting," "At the end of the day").
- Indonesian-language source articles: keep player/club/venue names in original form, write all slide content in English."""
```

---

## Why per-slide MIN tags?

Empirically, without MIN tags, Mistral under-wrote slide 4 (1 sentence vs target 2-4) which caused retries. Adding `(MIN 3 — never fewer than 3)` made the model hit bounds consistently: 3/3 first-try passes, 21s avg latency.

## Why `[REJECTION]`?

Before v7.3, when an article was thin, the model would pad with general football knowledge to hit 6 slides. The `{"error":"insufficient_source"}` JSON makes it explicit: skip and find a different article, don't fake it.

## Why `[SOURCE HANDLING]`?

Scrapes often contain nav text, related-article links, and ads. Without this rule, the model could latch onto unrelated content. Explicit instruction prevents drift.

## Empirical results (Mirror WC article, 3 dry-runs)

| Metric | Result |
|--------|--------|
| First-try pass rate | 3/3 (100%) |
| Avg latency | 21s |
| Hallucinations | 0 detected across 18 article facts |
| Slide 3 fallback (one-sided) | ✓ Fired correctly |
| Slide 4 fallback (no quote) | ✓ Fired correctly |
| URL on last slide | ✓ Always present |
