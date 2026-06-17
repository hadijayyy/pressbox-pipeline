# Threads Slide Prompt v3.0 — Optimized

## System Prompt

```
You generate 8-slide Threads posts from football articles. Output valid JSON only. No markdown, no explanation. Start with { immediately.
```

---

## User Prompt

```
Generate 8 slides for this article:

ARTICLE:
{article_text}

SOURCE: {url}

RULES:
- Slide 1: HOOK. 150-300 chars. Shocking stat, contrarian take, or unexpected question. Start mid-action.
- Slides 2-7: Story arc. 250-450 chars each. Build tension → emotional peak → unresolved stakes.
- Slide 8: Hot take question (250-450 chars). NOT "What do you think?" — pick a side. End with: 3 sentences + blank line + {url}.

BLANK LINE between every 2 sentences in each slide.
No em-dash. No hashtags. No emoji in slides 1-7. Max 1 emoji in slide 8.
Conversational English. Facts ONLY from article. Each slide stands alone.

BANNED (AI tells — instant skip):
"In a stunning turn" / "It's safe to say" / "Time will tell" / "Football is a funny old game" / "The beautiful game" / "What a time to be alive" / "At the end of the day" / "Absolute" (max 1x) / "Utterly" / "Truly" / "Undeniably"

OUTPUT:
{
  "slide_1": {"title": "HOOK", "content": "150-300 chars", "image_url": "HD image URL"},
  "slide_2": {"title": "THE PROBLEM", "content": "250-450 chars"},
  "slide_3": {"title": "THE CONTEXT", "content": "250-450 chars"},
  "slide_4": {"title": "THE COMPARISON", "content": "250-450 chars"},
  "slide_5": {"title": "HUMAN ANGLE", "content": "250-450 chars"},
  "slide_6": {"title": "BIGGER PICTURE", "content": "250-450 chars"},
  "slide_7": {"title": "THE STAKES", "content": "250-450 chars"},
  "slide_8": {"title": "PROVOCATIVE QUESTION?", "content": "250-450 chars + blank line + {url}"}
}
```

---

## What Changed from v2.0

| v2.0 | v3.0 |
|------|------|
| System prompt: 4 rules | System prompt: 1 rule |
| User prompt: ~2000 chars | User prompt: ~900 chars |
| 5 variables | 2 variables (article_text, url) |
| Slide definitions: 3-4 bullets each | Slide definitions: 1 sentence each |
| Emotional arc section | Removed (compressed into slide 2-7 rule) |
| Content type hooks | Removed (model can infer from article) |
| Example hooks | Removed (model can generate better from context) |
| Formatting rules: 7 bullets | Formatting rules: 3 lines |

---

## Token Comparison

| Metric | v2.0 | v3.0 | Savings |
|--------|------|------|---------|
| System prompt tokens | ~80 | ~25 | 69% |
| User prompt tokens | ~600 | ~280 | 53% |
| Total input tokens | ~680 | ~305 | 55% |
| Cost per call (est.) | $0.002 | $0.001 | 50% |

---

## Rules Kept (High Impact)

1. ✅ Banned phrases — prevent AI tells
2. ✅ Char count — validate slides
3. ✅ Formatting — blank lines, no em-dash
4. ✅ Slide 8 CTA — pick a side, not generic

## Rules Removed (Low Impact / Redundant)

1. ❌ Emotional arc — model infers from "build tension" rule
2. ❌ Content type hooks — model can categorize from article
3. ❌ Example hooks — model generates better from context
4. ❌ "Never start with ALL CAPS names" — too specific, model ignores
5. ❌ Image URL instruction — pipeline handles separately
