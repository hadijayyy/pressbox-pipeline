# Press Box — LLM Generate Prompt (v7, 6‑slide)

> **Live spec** — this document mirrors the prompt actually used in `pressbox-pipeline-v7.py`.  
> Any divergence between the markdown and the code is a bug; update the code, not the doc.

---

## Template

```
ARTICLE:
{article_text}

SOURCE URL: {best['url']}

OUTPUT FORMAT — Return ONLY a valid JSON object. No markdown, no filler, no explanation.
{
  "slide_1": {"title": "HOOK",       "content": "1-3 sentences, scroll-stopper, end with tension"},
  "slide_2": {"title": "WHAT",       "content": "2-4 sentences, concrete facts, why it matters"},
  "slide_3": {"title": "TENSION",    "content": "2-4 sentences, conflict/competing stakes"},
  "slide_4": {"title": "HUMAN",      "content": "2-4 sentences, one named person, quote or reported feeling"},
  "slide_5": {"title": "UNRESOLVED", "content": "3-4 sentences, what's left open, one conditional + timing detail"},
  "slide_6": {"title": "CTA",        "content": "2-4 sentences, rhetorical question, callback to S1, last line: {url}"}
}

RULES FOR CONTENT (Strict):
- Language: Conversational English, short punchy sentences. No em-dash, no hashtags.
- Facts: Use ONLY facts and names from the article.
- Length: 30-50 words per slide MAX. Do NOT over-write.
- Formatting: Every 2 sentences in "content", use "\n\n" for blank line.
- **NEVER return an empty slide.** Every slide MUST have 1-3 sentences in "content".
- If a slide has no usable content, **merge it into the previous slide's content**.
- Return exactly 6 slides. If you genuinely can't fill 6, return 5 — put `"Thread ends here"` as S5's last line.
```

---

## Slide 1 — HOOK (10/10 Standard)

**STEP 1 — Identify the EMOTIONAL ANGLE of this story.**
Ask yourself: What emotion will make people stop scrolling?
- OUTRAGE? (prices, corruption, ban, unfair treatment, scandal)
- CELEBRATION? (historic return, comeback, record broken, dream achieved)
- SHOCK? (unexpected result, drama, controversy, chaos)
- FEAR? (crisis, injury, threat, collapse, debt, nightmare)

**STEP 2 — Pick the HOOK STRUCTURE in this priority order:**

| # | Structure | Description |
|---|---|---|
| (a) | **PARADOX** | "X happened despite Y" / "X was forced to do the opposite of what X expected" |
| (b) | **CONCRETE EVENT** | A specific action that just happened (denied, banned, ruled out, arrested, suspended) that creates a strong narrative |
| (c) | **BETRAYAL** | Person/institution broke a promise or rule |
| (d) | **SHOCK** | Unexpected outcome that defies common sense |
| (e) | **NUMBERS** | Stat that reframes the story |

If no paradox exists in the article, skip to (b) or (c). Never force paradox from unrelated facts.

**HOOK QUALITY GATE — MANDATORY**
S1 must contain AT LEAST:
- One **PROPER NOUN** (person, team, or country name)
- One **CONCRETE DETAIL** (score, timeline, amount, or specific event)

If S1 is vague (*"a manager"*, *"the team"*, *"a star"*) or lacks specific identifiers → REJECT.
Output: `{"error":"vague_hook","reason":"S1 lacks proper noun or concrete detail"}`

**Length:** 1-3 sentences. End with tension. No context preamble (*"In a recent match…"*).

---

## Slides 2-5 — Storytelling Arc (10/10 Standard)

**CRITICAL:** These slides must read like ONE continuous story, not separate posts. Each slide must START where the PREVIOUS slide ENDED.

| Slide | Role | Min/Max sentences | What to include |
|---|---|---|---|
| **S2** | WHAT | 2-4 | What happened concretely + why it matters |
| **S3** | TENSION | 2-4 | Conflict / competing stakes. One-sided if needed: *"Article only covers [X]'s perspective."* |
| **S4** | HUMAN | 2-4 | One named person, own words or reported feelings. No quote: *"No direct quote from [Name]"* + one sentence on situation. |
| **S5** | UNRESOLVED | 3-4 | What's left open. One concrete conditional (*"If X, then Y"*) + a monitoring/timing detail. |

**Rules:**
- Each slide's FIRST sentence must REFERENCE the previous slide's topic.
- Use transition words: *"But here's the catch..."* / *"This matters because..."* / *"The result?"*
- Never start a slide with a completely new topic — thread the story through.
- **DEDUP:** Each named person from the FACT BANK appears in **AT MOST ONE** slide. Prefer S4 HUMAN slot.
- Build tension toward S5 — each slide should raise the stakes.
- Conversational English. Short words. Punchy rhythm.
- "Text message test": every sentence must make sense if texted alone. No compound sentences with *"and"*, *"but"*, *"while"* connecting two independent clauses.

---

## Slide 6 — CTA (10/10 Standard)

**"title" rules:**
- MUST be a **provocative debate question** ending with `?`
- MUST divide opinion — some fans will agree, some won't
- MUST include a **personal word**: *"you"*, *"we"*, *"fans"*, *"us"*
- NEVER generic: ❌ *"What happens next?"* / *"Will this work?"* / *"Your thoughts?"*
- Good: ✅ *"Should WE accept empty stadiums while FIFA jacks up prices?"* / *"Would YOU pay $5,700 for a final ticket?"* / *"Are FANS right to be furious at FIFA?"*

**"content" rules (STRICT — this is often wrong):**
- EXACTLY 2-3 sentences. Count them. No more, no less.
- Sentence 1: Recap the core tension (*"Empty stadiums on day one after prices soared"*).
- Sentence 2: Why it matters now (*"This could define the tournament's legacy"*).
- Sentence 3 (optional): Hint at what's at stake (*"Fans are watching — literally"*).
- Then **newline + URL** `{best['url']}`.
- **MUST callback S1** (image / quote / scene / contrast).
- Do NOT use *"Source:"* prefix. No hashtags, no emoji.

---

## Grounding & Rejection

**[GROUNDING — STRICT]**
- Names, scores, dates, quotes: **verbatim from article**. No outside knowledge.
- Missing detail = omit or flag. Never infer feelings.
- S5-6 may have implicit editorial framing but must trace to specific stated facts.

**[REJECTION]**
- Can't fill 6 slides honestly? Output: `{"error":"insufficient_source","reason":"..."}`
- S1 lacks proper noun or concrete detail? Output: `{"error":"vague_hook","reason":"..."}`
- Any slide has empty or whitespace-only "content"? Output: `{"error":"empty_slide","reason":"Slide N has no content"}`

**[STYLE]**
- Conversational plain English. One idea per sentence, each followed by `\n\n`.
- No em-dash (—), no hashtags, no bullets, no ALL CAPS, no AI throat-clearing.
- Each sentence must pass the "text message test".

---

## Scoring (topic selection — code, not LLM)

These rules live in `score_topic()` in the pipeline, not in the LLM prompt:

| Boost / Penalty | Amount | Trigger |
|---|---|---|
| WC keyword in title | **+50** | world cup, fifa, wc 2026, usa/mexico/canada 2026 |
| `wc_related` flag | **+40** | set during scraping |
| Controversy keyword | **+30** | outrage, scandal, banned, boycott, protest, chaos, crisis |
| Drama keyword | **+20** | secret, hidden, exposed, shocking, epic, comeback, revenge |
| Short title (≤8 words) | **+15** | punchy titles score higher |
| **Concrete event verb (new)** | **+15** | denied, banned, ruled out, arrested, suspended, injured, cleared, fined, charged, deported, refused, blocked, barred, rejected, disqualified, sent off, red card, miss out |
| Compound (WC + political) | **+10** | both keywords in title |
| `viral_related` flag | **+25** | from research module |
| Title > 15 words | **‑10** | too long, low engagement |
| **Generic transfer rumor (new)** | **‑10** | interested in, considering, monitoring, approach, enquire, eyeing, tracking, scouting, could sign, may sign, set to sign, close to signing, in talks, mulling, weighing up, asked to leave, wants to leave, wants out, push for exit |
| Boring keyword | **‑50** | quiz, lineup, preview, analysis, opinion |
| Hard skip | **‑999** | quiz, play quiz, how much, predicted, preview |

---

*Maintained by Pressbox pipeline. Last updated: 2026‑06‑23 (v7, 6‑slide + concrete‑event scoring).*
