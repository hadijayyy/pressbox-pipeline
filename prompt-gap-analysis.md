# Park The Bus — Prompt Gap Analysis

Current (pressbox-mvp.py) vs v3 Production

## What v3 has that we don't

| v3 Feature | Current Status | Impact |
|------------|---------------|--------|
| **Instruction Priority** (accuracy > style > engagement) | ❌ Missing — rules compete implicitly | LLM picks wrong priority under pressure |
| **Input Contract** (typed fields, anti-injection) | ❌ Missing — title+body concatenated raw | Injection surface via article text |
| **Source Validation** (check story matches title, body isn't empty) | ❌ Missing — only length/commercial checks | Weak articles get through |
| **Hybrid Research Policy** (when/how to web search) | ❌ No research capability | Entirely article-dependent |
| **Evidence Rules** (preserve uncertainty, frame analysis as opinion) | ⚠️ Partial — GR1-11 cover some | Misses: "analysis must be framed as interpretation" |
| **Output Contract** (strict JSON schema with status/sources/warnings) | ❌ Missing — returns slides without metadata | No audit trail, no warnings |
| **Deterministic Checks Outside LLM** (character count, sentence count, JSON) | ❌ Missing — only post-process formatting | Silent quality violations |
| **Story Angle** (explicit single sentence) | ❌ Missing | No clarity on what angle was chosen |
| **Forbidden Phrases** (complete list with context) | ✓ Have some | v3 list is more comprehensive |
| **Sensitive Topic Handling** (reflective Q for injuries/abuse) | ❌ Missing | S6 divisive rule inappropriate for sensitive stories |
| **Failure Schema** (needs_more_source with reason+missing) | ❌ Missing — just sys.exit(1) | Silent failures, hard to debug |

## What we have that v3 doesn't

| Our Feature | v3 Status | Keep? |
|-------------|-----------|-------|
| **5 Pattern Arc Templates** (A/C/D/E/F) | ❌ No patterns — single flexible arc | Yes — patterns proven to drive variety |
| **VIRAL CRITERIA** (≥2 per slide) | ❌ No viral forcing | Yes — drives engagement |
| **NUMBER TRUTH + Hallucination History** | ❌ No explicit hallucination examples | Yes — prevents repeated errors |
| **GR15 Sentence Length Cap** | Soft max 18 words | Ours: 15 (tighter = better) |
| **Realtime Engagement Ring (scoring adjustment)** | ❌ Not in prompt (app-level) | Keep in app, not prompt |
| **S1 EXACTLY 2 sentences ≤25 words** | 30 words | Ours tighter ✅ |

## Verdict

v3 is architecturally superior. Our prompt has better **specificity** (templates, examples, viral forcing). 

**Best path:** merge v3's structure (priority ladder, input contract, output contract, validation checks, hybrid research) with our proven arc templates + viral criteria + number truth.

