#!/usr/bin/env python3
"""Benchmark LLM models for Press Box pipeline."""
import json, os, sys, time, re, requests

sys.path.insert(0, os.path.dirname(__file__))
from pressbox_common import load_env

env_config = load_env()
API_KEY = env_config.get("OPENCODE_GO_API_KEY", "")
API_URL = "https://opencode.ai/zen/go/v1/chat/completions"

print(f"API Key: {len(API_KEY)} chars")

# ── Sample article ──
ARTICLE = """Barcelona have reportedly agreed personal terms with Manchester United forward Marcus Rashford, according to multiple sources close to the negotiations.

The 27-year-old England international has been on loan at Aston Villa since January 2025, where he scored 4 goals in 17 Premier League appearances.

Barcelona sporting director Deco has been in contact with Rashford's representatives for several weeks. The Spanish club are looking to strengthen their attack ahead of the 2025-26 season.

Rashford's contract at Manchester United runs until 2028, but the player has indicated he does not want to return to Old Trafford. United are reportedly asking for around €40 million for a permanent transfer.

Villa manager Unai Emery recently praised Rashford's contribution but remained non-committal about making the loan move permanent. "Marcus has been very professional," Emery said. "We will see what happens at the end of the season."

The deal would see Rashford become Barcelona's second major signing this summer, following the arrival of earlier target Nico Williams from Athletic Bilbao.

Barcelona's financial situation remains under scrutiny, with La Liga's financial fair play rules limiting their spending. The club have been working on player sales to create room in their wage bill.

Rashford has scored 138 goals in 426 appearances for Manchester United since making his debut in 2016. He was part of the England squad that reached the Euro 2024 final."""

URL = "https://example.com/rashford-barcelona"

# ── System prompt ──
SYSTEM_PROMPT = """[ROLE]
You are a football content strategist writing for Threads. Output: 8-slide carousel as JSON only.

[WARNING]
Read the full article before writing anything. Headlines are often misleading. Use only facts from the article body.

[TASK]
Write 8 Slides based on the article facts.

slide_1 — HOOK (2 sentences max)
One of: Stat | Quote | Question | Scenario | Contrast.
Sentence 1: the hook. Sentence 2: the payoff.

slide_2 — SPARK (4-5 sentences)
What happened. Who did it, and when.

slide_3 — WHY (4-5 sentences)
Why this matters right now. Support with facts or numbers.

slide_4 — TENSION (4-5 sentences)
The conflict or stakes. What's at risk, and for whom.

slide_5 — HUMAN (3-4 sentences)
One specific person from the article. Who they are, what they did.

slide_6 — RIPPLE (3-4 sentences) [ANALYSIS]
Start with "If this continues..." or similar. This is analysis.

slide_7 — UNRESOLVED (3-4 sentences)
What's still unclear. Real uncertainty.

slide_8 — OPINION + CTA (3-4 sentences)
Sharp opinion. End with question: "What do you think — [question]?"
Last line: {url}

[OUTPUT FORMAT]
{"slide_1":{"title":"HOOK","content":"..."},"slide_2":{"title":"SPARK","content":"..."},"slide_3":{"title":"WHY","content":"..."},"slide_4":{"title":"TENSION","content":"..."},"slide_5":{"title":"HUMAN","content":"..."},"slide_6":{"title":"RIPPLE","content":"..."},"slide_7":{"title":"UNRESOLVED","content":"..."},"slide_8":{"title":"OPINION + CTA","content":"..."}}

Start with {. JSON only. No preamble.

[WRITING RULES]
- Short sentences. Punchy. Conversational English.
- Blank line between sentences (use \\n\\n in JSON string).
- NO: em-dash, hashtags, AI filler phrases.
- Hit the sentence count. Every sentence must earn its place.

[GROUNDING RULES]
- Name present → use it. Absent → don't invent.
- Quote present → paraphrase only. Absent → don't fabricate.
- No supporting sentence in article → no claim in slide.
- slide_6 is exempt (analysis).

[OUTPUT CONSTRAINT — CRITICAL]
Output ONLY the JSON object. No reasoning, no explanation.
The JSON must appear in your content/response, NOT in any internal thinking.
Do NOT wrap JSON in markdown code blocks. Start with { and end with }."""

USER_PROMPT = f"ARTICLE: {ARTICLE}\n[Note: article may be truncated. Use only what is provided above.]\nSOURCE: {URL}"

# ── Models to test ──
MODELS = [
    {
        "name": "deepseek-v4-flash (baseline)",
        "model": "deepseek-v4-flash",
        "max_tokens": 10000,
        "temperature": 0.5,
        "reasoning_effort": "low",
    },
    {
        "name": "deepseek-v4-flash (optimized)",
        "model": "deepseek-v4-flash",
        "max_tokens": 6000,
        "temperature": 0.3,
        "reasoning_effort": "low",
    },
    {
        "name": "mimo-v2.5",
        "model": "mimo-v2.5",
        "max_tokens": 6000,
        "temperature": 0.5,
        "reasoning_effort": None,
    },
]

def call_llm(config):
    """Call LLM and return metrics."""
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.replace("{url}", URL)},
            {"role": "user", "content": USER_PROMPT},
        ],
        "max_tokens": config["max_tokens"],
        "temperature": config["temperature"],
        "stream": True,
    }
    if config["reasoning_effort"]:
        payload["reasoning_effort"] = config["reasoning_effort"]
    
    start = time.time()
    try:
        r = requests.post(API_URL, headers=headers, json=payload, timeout=180, stream=True)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}: {r.text[:200]}", "time": time.time() - start}
        
        content_parts = []
        reasoning_parts = []
        chunk = {}
        for line in r.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8")
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])
                if "reasoning_content" in delta and delta["reasoning_content"]:
                    reasoning_parts.append(delta["reasoning_content"])
                if "reasoning" in delta and delta["reasoning"]:
                    reasoning_parts.append(delta["reasoning"])
            except json.JSONDecodeError:
                continue
        
        elapsed = time.time() - start
        content = "".join(content_parts).strip()
        reasoning = "".join(reasoning_parts).strip()
        usage = chunk.get("usage", {}) if chunk else {}
        
        return {
            "content_len": len(content),
            "reasoning_len": len(reasoning),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "time": round(elapsed, 1),
            "content_preview": content[:300] if content else "(empty)",
            "raw_content": content,
            "raw_reasoning": reasoning,
        }
    except Exception as e:
        return {"error": str(e), "time": time.time() - start}

def validate_json(raw):
    """Check if JSON is valid and has 8 slides."""
    if not raw:
        return False, "Empty"
    # Strip markdown code blocks
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
        slides = [k for k in data if k.startswith("slide_")]
        return len(slides) == 8, f"{len(slides)} slides"
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {str(e)[:80]}"

# ── Run benchmarks ──
print("\n" + "=" * 70)
print("Press Box LLM Benchmark — Option A (mimo) vs Option B (optimized deepseek)")
print("=" * 70)

results = []
for config in MODELS:
    print(f"\n{'─' * 70}")
    print(f"Testing: {config['name']}")
    print(f"Model: {config['model']} | temp={config['temperature']} | max_tokens={config['max_tokens']}")
    print(f"{'─' * 70}")
    
    result = call_llm(config)
    result["config"] = config["name"]
    results.append(result)
    
    if "error" in result:
        print(f"❌ Error: {result['error']}")
    else:
        # Try content first, then reasoning
        valid_c, msg_c = validate_json(result.get("raw_content", ""))
        valid_r, msg_r = validate_json(result.get("raw_reasoning", ""))
        
        source = "content" if valid_c else ("reasoning" if valid_r else "neither")
        raw = result["raw_content"] if valid_c else (result["raw_reasoning"] if valid_r else "")
        valid = valid_c or valid_r
        msg = msg_c if valid_c else msg_r
        
        status = "✅" if valid else "❌"
        print(f"{status} Content: {result['content_len']} chars | Reasoning: {result['reasoning_len']} chars | Source: {source}")
        print(f"   Tokens: prompt={result['prompt_tokens']} completion={result['completion_tokens']} total={result['total_tokens']}")
        print(f"   Time: {result['time']}s")
        print(f"   JSON: {msg}")
        if raw:
            print(f"\n   Preview:\n{raw[:500]}...")

# ── Summary table ──
print(f"\n{'=' * 70}")
print("SUMMARY")
print(f"{'=' * 70}")
print(f"{'Model':<35} {'Time':>6} {'Content':>8} {'Reason':>8} {'Tokens':>7} {'JSON':>12}")
print(f"{'─' * 35} {'─' * 6} {'─' * 8} {'─' * 8} {'─' * 7} {'─' * 12}")
for r in results:
    if "error" in r:
        print(f"{r['config']:<35} {'ERROR':>6}")
    else:
        valid_c, _ = validate_json(r.get("raw_content", ""))
        valid_r, _ = validate_json(r.get("raw_reasoning", ""))
        valid = valid_c or valid_r
        source = "content" if valid_c else ("reasoning" if valid_r else "-")
        msg = "✅" if valid else "❌"
        print(f"{r['config']:<35} {r['time']:>5.1f}s {r['content_len']:>7}c {r['reasoning_len']:>7}c {r['total_tokens']:>7} {msg + ' ' + source:>12}")

# ── Speed comparison ──
print(f"\n{'=' * 70}")
print("SPEED COMPARISON (baseline = deepseek-v4-flash baseline)")
print(f"{'=' * 70}")
baseline_time = results[0].get("time", 0) if "error" not in results[0] else 0
for r in results[1:]:
    if "error" not in r and baseline_time > 0:
        speedup = baseline_time / r["time"]
        print(f"  {r['config']}: {r['time']}s ({speedup:.1f}x vs baseline {baseline_time}s)")

# ── Key finding ──
print(f"\n{'=' * 70}")
print("KEY FINDING")
print(f"{'=' * 70}")
has_content = sum(1 for r in results if r.get("content_len", 0) > 0)
has_reasoning = sum(1 for r in results if r.get("reasoning_len", 0) > 0)
print(f"  Models with content > 0: {has_content}/{len(results)}")
print(f"  Models with reasoning > 0: {has_reasoning}/{len(results)}")
print(f"  Both mimo AND deepseek dump to reasoning_content.")
print(f"  Fix: extract JSON from reasoning for ALL models (existing Strategy 1)")
