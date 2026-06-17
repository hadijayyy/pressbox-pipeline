#!/usr/bin/env python3
"""Press Box Pipeline v7 — fast, clean, ~300 lines."""
import json, os, sys, re, time, subprocess, html, importlib.util
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ── Paths ───────────────────────────────────────────────────────────
HOME = os.path.expanduser("~")
SCRIPTS = f"{HOME}/.hermes/scripts"
STAGING = f"{HOME}/.hermes/pressbox/staging.json"
POSTED = f"{HOME}/.hermes/pressbox/posted_topics.json"
WIB = timezone(timedelta(hours=7))

os.makedirs(f"{HOME}/.hermes/pressbox", exist_ok=True)

# ── Dynamic import of research module ───────────────────────────────
try:
    _spec = importlib.util.spec_from_file_location(
        "pressbox_research", f"{SCRIPTS}/pressbox-research.py"
    )
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    scrape_rss = _mod.scrape_rss
    scrape_mirror = _mod.scrape_mirror
except Exception as e:
    print(f"[FATAL] Cannot load pressbox-research.py: {e}", file=sys.stderr)
    sys.exit(1)

# ── Load env ────────────────────────────────────────────────────────
def load_env():
    env = {}
    env_path = f"{HOME}/.hermes/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip("\"'")
    return env

env_config = load_env()
API_KEY = env_config.get("OPENCODE_GO_API_KEY", "")
API_URL = "https://opencode.ai/zen/go/v1/chat/completions"
MODEL = "deepseek-v4-flash"

# ── Helpers ─────────────────────────────────────────────────────────
STOPWORDS = frozenset([
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "with", "by",
    "as", "is", "was", "are", "were", "be", "been", "has", "have", "had",
    "it", "its", "this", "that", "these", "those", "and", "or", "but",
    "not", "no", "if", "from", "up", "down", "out", "off", "over", "under",
    "about", "into", "than", "then", "also", "just", "will", "can", "all",
    "who", "what", "when", "where", "why", "how", "their", "his", "her",
    "our", "your", "my", "we", "he", "she", "they", "do", "does", "did",
])

REPLACEMENTS = {
    "manchester city": "man city",
    "manchester united": "man utd",
    "real madrid": "madrid",
    "barcelona": "barca",
    "tottenham": "spurs",
    "newcastle": "toon",
    "nottingham": "nottm",
    "wolverhampton": "wolves",
    "leicester": "foxes",
    "southampton": "saints",
    "west ham": "westham",
}

def log(msg):
    ts = datetime.now(WIB).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True, file=sys.stderr)

def clean_words(text):
    t = text.lower()
    for old, new in REPLACEMENTS.items():
        t = t.replace(old, new)
    t = re.sub(r"[^\w\s]", " ", t)
    words = t.split()
    return frozenset(w for w in words if w not in STOPWORDS and len(w) > 1)

def is_similar(new_title, posted_ws, threshold=0.35):
    nw = clean_words(new_title)
    if not nw:
        return False
    for pw in posted_ws:
        if not pw:
            continue
        intersection = len(nw & pw)
        union = len(nw | pw)
        if union == 0:
            continue
        if intersection / union >= threshold:
            return True
    return False

def classify_topic_type(text):
    """Classify topic into category (mirrors analytics-llm.py)."""
    if not text:
        return "other"
    lower = text.lower()
    if any(w in lower for w in ["transfer", "signs", "signing", "move to", "bid", "contract"]):
        return "transfer_rumor"
    if any(w in lower for w in ["world cup", "wc", "2026", "tournament"]):
        if any(w in lower for w in ["guide", "preview", "squad", "team guide"]):
            return "WC_team_guide"
        return "tournament_news"
    if any(w in lower for w in ["controversy", "drama", "storms", "backlash", "fans react"]):
        return "controversy"
    if any(w in lower for w in ["analysis", "tactical", "formation", "system"]):
        return "tactical_analysis"
    if any(w in lower for w in ["profile", "career", "who is", "story of"]) or len(text.split()) < 30:
        return "player_profile"
    if any(w in lower for w in ["injury", "out for", "sidelined", "fitness"]):
        return "injury_update"
    return "other"

def extract_body_image(raw_html):
    """Extract first <img> from article body (fallback when og:image fails)."""
    from html.parser import HTMLParser
    class ImgExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.first_img = ""
            self.in_article = False
        def handle_starttag(self, tag, attrs):
            if tag in ("article", "main", "div"):
                for name, val in attrs:
                    if name == "class" and val and any(c in val for c in ["article", "story", "content", "post"]):
                        self.in_article = True
            if tag == "img" and self.in_article:
                for name, val in attrs:
                    if name == "src" and val and not self.first_img:
                        # Skip tiny icons, logos, avatars
                        skip_patterns = ["icon", "logo", "avatar", "pixel", "spacer", "1x1", "badge"]
                        if not any(p in val.lower() for p in skip_patterns):
                            self.first_img = val
    try:
        parser = ImgExtractor()
        parser.feed(raw_html[:50000])
        return parser.first_img or ""
    except:
        return ""

def score_topic(t):
    title = t.get("title", "")
    s = 0
    tl = title.lower()
    # Controversy keywords
    controversy = {"outrage", "scandal", "banned", "boycott", "protest", "chaos", "crisis"}
    if any(kw in tl for kw in controversy):
        s += 30
    # Drama keywords
    drama = {"secret", "hidden", "exposed", "shocking", "epic", "comeback", "revenge"}
    if any(kw in tl for kw in drama):
        s += 20
    # Boring keywords
    boring = {"quiz", "lineup", "live updates", "preview", "analysis", "opinion"}
    if any(kw in tl for kw in boring):
        s -= 15
    # Title length
    wc = len(title.split())
    if wc <= 8:
        s += 15
    if wc > 15:
        s -= 10
    # World Cup
    wc_kw = {"world cup", "fifa", "qualifier", "wc 2026", "usa 2026", "mexico 2026", "canada 2026"}
    if any(kw in tl for kw in wc_kw):
        s += 50
    if t.get("wc_related") or t.get("wc_boost"):
        s += 40
    if t.get("viral_related"):
        s += 25
    # Base score from research module
    s += t.get("score", 0)
    # Analytics topic boost
    topic_type = classify_topic_type(title)
    if topic_type in topic_boosts:
        multiplier = topic_boosts[topic_type]
        s = int(s * multiplier)
    # Keyword boost from recommendations
    s += t.get("_kw_boost", 0)
    return s

# ── Guard ───────────────────────────────────────────────────────────
if os.path.exists(STAGING):
    try:
        with open(STAGING) as f:
            existing = json.load(f)
        if existing.get("topic") and existing.get("content"):
            log("⏸️ Staging unposted — skip (exit 2)")
            sys.exit(2)
    except Exception:
        pass

START = time.time()
t_scrape = t_llm = 0

# ── 1. SCRAPE ─────────────────────────────────────────────────────
log("Scraping Guardian + Mirror...")
t0 = time.time()
with ThreadPoolExecutor(max_workers=3) as ex:
    fut_guardian = ex.submit(scrape_rss, "https://www.theguardian.com/football/rss", "guardian", 14)
    fut_mirror = ex.submit(scrape_mirror)
    fut_sky = ex.submit(scrape_rss, "https://www.skysports.com/rss/11095", "skysports", 12)

    all_topics = []
    for fut, name in [(fut_guardian, "guardian"), (fut_mirror, "mirror"), (fut_sky, "skysports")]:
        try:
            result = fut.result(timeout=15)
            for t in result:
                t["url_verified"] = True
            log(f"   {name}: {len(result)} topics")
            all_topics.extend(result)
        except Exception as e:
            log(f"   ⚠️ {name} error: {e}")

t_scrape = time.time() - t0
log(f"   Total scraped: {len(all_topics)} topics")

if not all_topics:
    log("❌ No topics scraped — exit")
    sys.exit(1)

# ── 2. FILTER ─────────────────────────────────────────────────────
t0 = time.time()

# Load posted topics
posted_urls = set()
posted_titles = []
if os.path.exists(POSTED):
    try:
        with open(POSTED) as f:
            data = json.load(f)
            topics = data if isinstance(data, list) else data.get("topics", [])
            for t in topics:
                u = (t.get("url") or "").strip()
                if u and u.startswith("http"):
                    posted_urls.add(u)
                title = (t.get("title") or "").strip()
                if title:
                    posted_titles.append(clean_words(title))
    except Exception:
        pass

# Load scrape cache for 30-min check
CACHE_FILE = f"{HOME}/.hermes/pressbox/scrape_cache.json"
cache_urls = set()
if os.path.exists(CACHE_FILE):
    try:
        cache_data = json.load(open(CACHE_FILE))
        cache_ts = cache_data.get("cached_at", 0)
        if time.time() - cache_ts < 1800:  # 30 min
            for item in (cache_data.get("results") or []):
                u = (item.get("url") or "").strip()
                if u:
                    cache_urls.add(u)
    except Exception:
        pass

ALLOWED_SOURCES = {"guardian", "mirror", "skysports"}

# ── Load analytics feedback ──────────────────────────────────────
ANALYTICS_FEEDBACK = f"{HOME}/.hermes/pressbox/analytics_feedback.json"
ANALYTICS_RECOMMENDATIONS = f"{HOME}/.hermes/pressbox/analytics_recommendations.json"
topic_boosts = {}
skip_topics = []
best_hours = []
research_keywords_add = []
research_keywords_remove = []
preferred_hooks = []
cta_pattern = ""
tone_adjustment = "Conversational English. Bold numbers. High-impact words."
try:
    with open(ANALYTICS_FEEDBACK) as f:
        fb = json.load(f)
    topic_boosts = fb.get("topic_boosts", {})
    skip_topics = [s.get("pattern", "") for s in fb.get("skip_topics", [])]
    best_hours = fb.get("best_hours", [])
    if topic_boosts or skip_topics:
        log(f"   📊 Analytics loaded: {len(topic_boosts)} boosts, {len(skip_topics)} skip patterns")
except Exception:
    pass

# Load recommendations (research + generate tweaks)
try:
    with open(ANALYTICS_RECOMMENDATIONS) as f:
        recs = json.load(f)
    analysis = recs.get("analysis", {})
    rt = analysis.get("research_tweaks", {})
    gt = analysis.get("generate_tweaks", {})
    research_keywords_add = rt.get("keyword_additions", [])
    research_keywords_remove = rt.get("keyword_removals", [])
    preferred_hooks = gt.get("preferred_hooks", [])
    cta_pattern = gt.get("cta_pattern", "")
    tone_adjustment = gt.get("tone_adjustment", tone_adjustment)
    if research_keywords_add or preferred_hooks:
        log(f"   🧠 Recommendations loaded: {len(research_keywords_add)} keywords, {len(preferred_hooks)} hooks")
except Exception:
    pass

filtered = []
for t in all_topics:
    title = (t.get("title") or "").strip()
    url = (t.get("url") or "").strip()
    source = (t.get("source") or "").strip().lower()
    if not title or not url:
        continue
    if source not in ALLOWED_SOURCES:
        continue
    if url in posted_urls:
        continue
    if url in cache_urls:
        continue
    if is_similar(title, posted_titles, 0.35):
        continue
    # Skip low-performing topics from analytics
    topic_type = classify_topic_type(title)
    if topic_type in skip_topics:
        continue
    # Boost topics matching recommended keywords
    if research_keywords_add:
        title_lower = title.lower()
        kw_hits = sum(1 for kw in research_keywords_add if kw.lower() in title_lower)
        if kw_hits > 0:
            t["_kw_boost"] = kw_hits * 10
    filtered.append(t)

log(f"   After filter: {len(filtered)} topics")
if not filtered:
    log("❌ No topics after filter — exit")
    sys.exit(1)

# ── 3. SCORE — pick best ──────────────────────────────────────────
for t in filtered:
    t["_score"] = score_topic(t)

filtered.sort(key=lambda x: -x["_score"])
best = filtered[0]
log(f"   🏆 Best: {best['title']} (score={best['_score']})")

# ── 4. EXTRACT (curl + og:image) ──────────────────────────────────
t0 = time.time()
url = best["url"]
log(f"   Extracting: {url}")

try:
    result = subprocess.run(
        ["curl", "-sL", "--max-time", "10", "-A",
         "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
         url],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        log(f"❌ curl failed with code {result.returncode}")
        sys.exit(1)
    raw_html = result.stdout
except Exception as e:
    log(f"❌ curl exception: {e}")
    sys.exit(1)

# Strip HTML tags for article text
article_text = re.sub(r"<[^>]+>", " ", raw_html)
article_text = re.sub(r"\s+", " ", article_text).strip()[:2000]

# Extract og:image — but validate it works (Guardian blocks hotlinking)
image_url = ""
for pattern in [
    r'<meta\s+property="og:image"\s+content="([^"]+)"',
    r'<meta\s+name="og:image"\s+content="([^"]+)"',
    r'<meta\s+property="twitter:image"\s+content="([^"]+)"',
    r'<meta\s+name="twitter:image"\s+content="([^"]+)"',
]:
    m = re.search(pattern, raw_html, re.IGNORECASE)
    if m:
        candidate = m.group(1)
        # Quick HEAD check — Guardian blocks most og:image URLs
        try:
            hr = subprocess.run(
                ["curl", "-sIL", "--max-time", "5", candidate],
                capture_output=True, text=True, timeout=8)
            if "200" in hr.stdout:
                image_url = candidate
                break
        except:
            pass

# Fallback 1: Extract first <img> from article body
if not image_url:
    body_img = extract_body_image(raw_html)
    if body_img:
        try:
            hr = subprocess.run(
                ["curl", "-sIL", "--max-time", "5", body_img],
                capture_output=True, text=True, timeout=8)
            if "200" in hr.stdout:
                image_url = body_img
                log(f"   ✅ Body image found: {image_url[:80]}")
        except:
            pass

# Fallback 2: image_url from research module (RSS)
if not image_url:
    candidate = best.get("image_url", "") or ""
    if candidate:
        try:
            hr = subprocess.run(
                ["curl", "-sIL", "--max-time", "5", candidate],
                capture_output=True, text=True, timeout=8)
            if "200" in hr.stdout:
                image_url = candidate
        except:
            pass

log(f"   Article: {len(article_text)} chars, image: {'yes' if image_url else 'no'}")

if not article_text or len(article_text) < 100:
    log("❌ Article text too short — exit")
    sys.exit(1)

# ── 5. LLM call ───────────────────────────────────────────────────
t0 = time.time()

system_prompt = """You are a slide content generator. You think briefly, then output immediately.

RULES:
- Reason for NO MORE than 3-4 sentences total
- Do not explore alternatives or second-guess
- Output ONLY valid JSON, no markdown, no explanation
- Start your response with { immediately after thinking"""

user_prompt = f"""Generate exactly 8 slides for this football article:

ARTICLE:
{article_text}

SOURCE: {url}

SLIDE RULES:
- Slide 1: HOOK — 1-2 punchy sentences. 250-450 chars. Include HD image URL from article if available.
- Slides 2-7: STORY ARC — each continues from previous. 250-450 chars.
- Slide 8: CTA — {cta_pattern if cta_pattern else 'debate question with "?" + personal word (you/we/fans)'}. 3 sentences + Source URL.

CRITICAL: Slides 1-7 MUST be 250-450 chars each. Do NOT write shorter.
FORMATTING: Add a blank line between every 2 sentences in each slide for readability.
TONE: {tone_adjustment}

JSON FORMAT:
{{
  "slide_1": {{"title": "HOOK", "content": "1-2 punchy sentences with context, 250-450 chars", "image_url": "HD image URL from article if available"}},
  "slide_2": {{"title": "THE PROBLEM", "content": "What happened, 250-450 chars"}},
  "slide_3": {{"title": "THE CONTEXT", "content": "Why it matters, 250-450 chars"}},
  "slide_4": {{"title": "THE COMPARISON", "content": "Similar past, 250-450 chars"}},
  "slide_5": {{"title": "HUMAN ANGLE", "content": "Quotes/emotion, 250-450 chars"}},
  "slide_6": {{"title": "BIGGER PICTURE", "content": "Implications, 250-450 chars"}},
  "slide_7": {{"title": "THE STAKES", "content": "Climax before CTA, 250-450 chars"}},
  "slide_8": {{"title": "PROVOCATIVE QUESTION?", "content": "3 sentences + blank line + {url}"}}
}}

FACTS ONLY from article. No em-dash, no hashtags, no AI speak. Conversational English.
8 slides. JSON only."""

log(f"   Calling LLM ({MODEL})...")

headers = {"Content-Type": "application/json"}
if API_KEY:
    headers["Authorization"] = f"Bearer {API_KEY}"

# ── LLM call with retry for word count ──────────────────────────
MAX_RETRIES = 3
MIN_CHARS = 250
MAX_CHARS = 450
raw_json = ""

for attempt in range(1, MAX_RETRIES + 1):
    log(f"   LLM attempt {attempt}/{MAX_RETRIES}...")
    try:
        r = requests.post(
            API_URL,
            headers=headers,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 6000,
                "temperature": 0.7,
                "reasoning_effort": "low",
            },
            timeout=180,
        )
        if r.status_code != 200:
            log(f"❌ API error: HTTP {r.status_code} {r.text[:200]}")
            sys.exit(1)
        data = r.json()
        msg = data.get("choices", [{}])[0].get("message", {})
        content = (msg.get("content") or "").strip()
        reasoning = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()

        log(f"   Response: content={len(content)} chars, reasoning={len(reasoning)} chars")

        # Extract JSON
        candidate_json = ""
        if content:
            candidate_json = re.sub(r"^```(?:json)?\s*", "", content)
            candidate_json = re.sub(r"\s*```$", "", candidate_json)
            candidate_json = candidate_json.strip()

        if not candidate_json and reasoning:
            log("   Content empty, extracting JSON from reasoning...")
            start = reasoning.find('{')
            while start != -1:
                depth = 0
                for i in range(start, len(reasoning)):
                    if reasoning[i] == '{': depth += 1
                    elif reasoning[i] == '}': depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(reasoning[start:i+1])
                            if isinstance(parsed, dict) and len(parsed) >= 3:
                                candidate_json = reasoning[start:i+1]
                                log(f"   Found JSON in reasoning ({len(candidate_json)} chars)")
                                break
                        except json.JSONDecodeError:
                            pass
                        break
                if candidate_json:
                    break
                start = reasoning.find('{', start + 1)

        if not candidate_json:
            log("   ❌ No JSON found, retrying...")
            continue

        # Parse and validate char count (slides 1-7)
        slides_data = json.loads(candidate_json)
        char_issues = []
        for i in range(1, 8):  # slides 1-7
            key = f"slide_{i}"
            if key in slides_data:
                body = slides_data[key].get("content", "")
                chars = len(body)
                if chars < MIN_CHARS:
                    char_issues.append(f"slide_{i}: {chars}c")
                elif chars > MAX_CHARS:
                    char_issues.append(f"slide_{i}: {chars}c(too long)")

        if not char_issues:
            log(f"   ✅ All slides 2-7 pass char count ({MIN_CHARS}-{MAX_CHARS}c)")
            raw_json = candidate_json
            break
        else:
            log(f"   ⚠️ Char count fail: {', '.join(char_issues)} — retrying...")

    except Exception as e:
        log(f"❌ LLM exception: {e}")
        continue

if not raw_json:
    log(f"❌ Failed to get valid slides after {MAX_RETRIES} retries")
    sys.exit(1)

# ── Parse & validate slides ──────────────────────────────────────
try:
    slides_data = json.loads(raw_json)
except json.JSONDecodeError as e:
    log(f"❌ JSON parse error: {e}")
    sys.exit(1)

slides = []
for i in range(1, 9):
    key = f"slide_{i}"
    if key not in slides_data:
        log(f"❌ Missing {key}")
        sys.exit(1)
    slide = slides_data[key]
    if not isinstance(slide, dict):
        log(f"❌ {key} not a dict")
        sys.exit(1)
    title = (slide.get("title") or "").strip()
    content = (slide.get("content") or "").strip()
    if not title or not content:
        log(f"❌ {key} missing title or content")
        sys.exit(1)
    slides.append({"title": title, "content": content})

# Validate slide 8
if "?" not in slides[7]["title"]:
    log("❌ Slide 8 title missing '?'")
    sys.exit(1)

# Validate content length (char count, aligned with retry validation)
for i, s in enumerate(slides):
    c = s["content"]
    chars = len(c)
    # Slide 1 (hook): 250-450 chars
    if i == 0:
        if chars < MIN_CHARS:
            log(f"⚠️ Slide 1 too short ({chars}c, min {MIN_CHARS})")
            sys.exit(1)
        elif chars > MAX_CHARS:
            log(f"⚠️ Slide 1 too long ({chars}c, max {MAX_CHARS})")
            sys.exit(1)
    # Slides 2-7: enforce 250-450 chars
    elif 1 <= i <= 6:
        if chars < MIN_CHARS:
            log(f"⚠️ Slide {i+1} too short ({chars}c, min {MIN_CHARS})")
            sys.exit(1)
        elif chars > MAX_CHARS:
            log(f"⚠️ Slide {i+1} too long ({chars}c, max {MAX_CHARS})")
            sys.exit(1)
    # Slide 8 (CTA): just needs to exist
    if len(c) > 400:
        # Trim at last sentence boundary
        trimmed = c[:400]
        last_bound = max(trimmed.rfind(". "), trimmed.rfind("? "), trimmed.rfind("! "))
        if last_bound > 150:
            s["content"] = trimmed[:last_bound + 1]
        else:
            s["content"] = trimmed

# Build joined content (no titles, just content)
joined = "\n---\n".join(s["content"] for s in slides)

# ── 6. STAGE (atomic write) ──────────────────────────────────────
staging_obj = {
    "topic": best,
    "content": joined,
    "written_at": datetime.now(WIB).isoformat(),
    "is_wc": bool(best.get("wc_related") or best.get("wc_boost")),
    "is_transfer": bool(best.get("transfer_related")),
    "mode": "thread",
    "slides": 8,
    "image_url": image_url,
}

tmp = STAGING + ".tmp"
with open(tmp, "w") as f:
    json.dump(staging_obj, f, indent=2)
os.replace(tmp, STAGING)

log(f"✅ {best['title']}  (8 slides) [{'WC' if staging_obj['is_wc'] else 'Transfer' if staging_obj['is_transfer'] else 'General'}]")

total = time.time() - START
log(f"⏱️ Scrape:{t_scrape:.1f}s  LLM:{t_llm:.1f}s  Total:{total:.1f}s")
