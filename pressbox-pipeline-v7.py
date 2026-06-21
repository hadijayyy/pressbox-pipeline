#!/usr/bin/env python3
"""Press Box Pipeline v7 — fast, clean, ~300 lines."""
import html as html_mod, json, os, sys, re, time, subprocess, importlib.util, struct
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from pressbox_common import WIB, HOME, SCRIPTS, STAGING, POSTED, load_env, log
from pressbox_common import clean_words, is_similar, classify_topic_type
from pressbox_common import STOPWORDS, REPLACEMENTS

import requests

# ── Flags ──────────────────────────────────────────────────────────
DRY_RUN = "--dry-run" in sys.argv

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
    scrape_goal = _mod.scrape_goal
except Exception as e:
    print(f"[FATAL] Cannot load pressbox-research.py: {e}", file=sys.stderr)
    sys.exit(1)

# ── Load env ────────────────────────────────────────────────────────
env_config = load_env()
# Mistral (primary, mistral-large-latest) — custom:Mistral
MISTRAL_API_KEY = env_config.get("MISTRAL_API_KEY", "")
# Tokenrouter (fallback, MiniMax-M3) — custom:tokenrouter; opencode-go kept as final fallback
MINIMAX_API_KEY = env_config.get("MINIMAX_API_KEY") or env_config.get("OPENCODE_GO_API_KEY", "")
# Legacy globals (some legacy callers may still reference these)
API_KEY = MINIMAX_API_KEY
API_URL = "https://api.tokenrouter.com/v1/chat/completions"
MODEL = "mistral-large-latest"

# ── Provider registry (per-model URL + key) ──────────────────────────────
PROVIDERS = {
    "mistral-large-latest": {
        "base_url": "https://api.mistral.ai/v1/chat/completions",
        "api_key":  MISTRAL_API_KEY,
    },
    "MiniMax-M3": {
        "base_url": "https://api.tokenrouter.com/v1/chat/completions",
        "api_key":  MINIMAX_API_KEY,
    },
}

def get_provider_for_model(model_name):
    """Look up provider config for a model. Falls back to tokenrouter globals."""
    p = PROVIDERS.get(model_name)
    if p and p["api_key"]:
        return p
    return {"base_url": API_URL, "api_key": API_KEY}

# ── Model routing by article type ──────────────────────────────────
def get_model_config(topic_type):
    """Model chain with fallback order.
    Primary: mistral-large-latest (Mistral API)
    Fallback: MiniMax-M3 (tokenrouter)
    """
    return [
        {"model": "mistral-large-latest", "max_tokens": 8000, "reasoning_effort": None},  # Mistral-large supports up to 8192; bumped from 5000 after truncation observed on 6-slide carousels
        {"model": "MiniMax-M3",           "max_tokens": 8000, "reasoning_effort": None},
    ]


def extract_body_image(raw_html):
    """Extract best <img> from article body (fallback when og:image fails).
    Priority: srcSet largest > data-src > src (skip tiny icons/logos)."""
    from html.parser import HTMLParser
    class ImgExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.best_url = ""
            self.best_width = 0
            self.in_article = False
        def handle_starttag(self, tag, attrs):
            if tag in ("article", "main", "div"):
                for name, val in attrs:
                    if name == "class" and val and any(c in val for c in ["article", "story", "content", "post"]):
                        self.in_article = True
            if tag == "img" and self.in_article:
                attrs_dict = dict(attrs)
                skip_patterns = ["icon", "logo", "avatar", "pixel", "spacer", "1x1", "badge"]
                # Strategy 1: srcSet — pick largest width (adapted from goal_scraper.py)
                srcset = attrs_dict.get("srcset") or attrs_dict.get("srcSet") or ""
                if srcset:
                    for part in srcset.split(","):
                        tokens = part.strip().split()
                        if len(tokens) == 2 and tokens[1].endswith("w"):
                            try:
                                w = int(tokens[1][:-1])
                                url = tokens[0]
                                if w > self.best_width and not any(p in url.lower() for p in skip_patterns):
                                    self.best_width = w
                                    self.best_url = url
                            except ValueError:
                                pass
                # Strategy 2: data-src (lazy loading)
                if not self.best_url:
                    data_src = attrs_dict.get("data-src", "")
                    if data_src and not any(p in data_src.lower() for p in skip_patterns):
                        self.best_url = data_src
                # Strategy 3: src (direct)
                if not self.best_url:
                    src = attrs_dict.get("src", "")
                    if src and not src.startswith("data:") and not any(p in src.lower() for p in skip_patterns):
                        self.best_url = src
    try:
        parser = ImgExtractor()
        parser.feed(raw_html[:50000])
        url = parser.best_url or ""
        # Handle protocol-relative URLs (//example.com → https://example.com)
        if url.startswith("//"):
            url = "https:" + url
        return url
    except Exception as e:
        log(f"   ⚠️ extract_og_image failed: {e}")
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

# ── Image accessibility check ───────────────────────────────────────
def check_image_accessible(url):
    """Check if image URL returns HTTP 200 via HEAD request.
    Returns (accessible, status_code). On error, returns (False, 0)."""
    try:
        hr = subprocess.run(
            ["curl", "-sIL", "--max-time", "5", url],
            capture_output=True, text=True, timeout=8)
        # Parse LAST status code from response headers
        last_status = 0
        for line in hr.stdout.split("\n"):
            line = line.strip()
            if line.startswith("HTTP/") and " " in line:
                try:
                    last_status = int(line.split(" ", 2)[1])
                except (ValueError, IndexError):
                    pass
        return (last_status == 200, last_status)
    except Exception:
        return (False, 0)

# ── Image quality gate ─────────────────────────────────────────────
def validate_image_quality(url):
    """Download first 8KB and parse image dimensions from header bytes.
    Returns (is_valid, width, height). On any error, returns (False, 0, 0)."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-r", "0-8191", "--max-time", "5", url],
            capture_output=True, timeout=8
        )
        data = result.stdout
        if len(data) < 12:
            return (False, 0, 0)

        w = h = 0
        # PNG: signature starts with b'\x89PNG'
        if data[:4] == b'\x89PNG':
            if len(data) >= 24 and data[12:16] == b'IHDR':
                w = struct.unpack('>I', data[16:20])[0]
                h = struct.unpack('>I', data[20:24])[0]
            else:
                return (False, 0, 0)

        # JPEG: starts with 0xFFD8
        elif data[:2] == b'\xff\xd8':
            i = 2
            while i < len(data) - 1:
                if data[i] != 0xFF:
                    break
                marker = data[i + 1]
                # SOF0 (0xC0), SOF1 (0xC1), SOF2 (0xC2)
                if marker in (0xC0, 0xC1, 0xC2):
                    if i + 10 <= len(data):
                        h = struct.unpack('>H', data[i+5:i+7])[0]
                        w = struct.unpack('>H', data[i+7:i+9])[0]
                    break
                i += 2
                if i + 1 < len(data):
                    seg_len = struct.unpack('>H', data[i:i+2])[0]
                    i += seg_len
                else:
                    break
        else:
            return (False, 0, 0)

        if w >= 400 and h > 0:
            ratio = w / h
            if 0.5 <= ratio <= 2.5:
                return (True, w, h)

        return (False, w, h)
    except Exception as e:
        log(f"   ⚠️ validate_image_quality failed: {e}")
        return (False, 0, 0)

# ── Guard ───────────────────────────────────────────────────────────
ERROR_LOG = f"{HOME}/.hermes/pressbox/pipeline_errors.log"

def log_error(msg):
    """Append error message to pipeline_errors.log."""
    ts = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(ERROR_LOG, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass  # Don't let error logging fail the pipeline

if os.path.exists(STAGING["v2"]) and not DRY_RUN:
    try:
        with open(STAGING["v2"]) as f:
            existing = json.load(f)
        # Validate schema
        if not existing.get("topic") or not existing.get("content"):
            log("⚠️ Staging invalid (missing topic/content) — overwriting")
        elif existing.get("status") == "error":
            log("⚠️ Staging has error status — overwriting")
        else:
            log("⏸️ Staging unposted — skip")
            sys.exit(0)
    except Exception as e:
        log_error(f"Guard read error: {e}")
        log("⚠️ Staging corrupt — overwriting")

START = time.time()
t_scrape = t0 = 0
prompt_tok = completion_tok = total_tok = 0
content = ""
reasoning = ""
article_cache = {}

# ── 1. SCRAPE ─────────────────────────────────────────────────────
log("Scraping Mirror + Sky Sports + Goal.com...")
t0 = time.time()
with ThreadPoolExecutor(max_workers=3) as ex:
    fut_mirror = ex.submit(scrape_mirror)
    fut_sky = ex.submit(scrape_rss, "https://www.skysports.com/rss/11095", "skysports", 12)
    fut_goal = ex.submit(scrape_goal)

    all_topics = []
    for fut, name in [(fut_mirror, "mirror"), (fut_sky, "skysports"), (fut_goal, "goal")]:
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
    print("❌ Pipeline failed: No topics scraped from RSS", flush=True)
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
        with open(CACHE_FILE) as _cf:
            cache_data = json.load(_cf)
        cache_ts = cache_data.get("cached_at", 0)
        if time.time() - cache_ts < 1800:  # 30 min
            for item in (cache_data.get("results") or []):
                u = (item.get("url") or "").strip()
                if u:
                    cache_urls.add(u)
    except Exception:
        pass

ALLOWED_SOURCES = {"mirror", "skysports", "goal"}

# ── Load analytics feedback ──────────────────────────────────────
ANALYTICS_FEEDBACK = f"{HOME}/.hermes/pressbox/analytics_feedback.json"
ANALYTICS_RECOMMENDATIONS = f"{HOME}/.hermes/pressbox/analytics_recommendations.json"
topic_boosts = {}
skip_topics = []
research_keywords_add = []
research_keywords_remove = []
preferred_hooks = []
cta_pattern = ""
tone_adjustment = "Conversational English. Bold numbers. High-impact words."
analytics_fresh = False
try:
    with open(ANALYTICS_FEEDBACK) as f:
        fb = json.load(f)
    # Stale check: ignore if >48h old
    generated_at = fb.get("generated_at", "")
    if generated_at:
        try:
            gen_dt = datetime.fromisoformat(generated_at)
            if gen_dt.tzinfo is None:
                gen_dt = gen_dt.replace(tzinfo=WIB)
            if datetime.now(WIB) - gen_dt > timedelta(hours=48):
                log(f"   ⚠️ Analytics feedback >48h old — using defaults")
            else:
                topic_boosts = fb.get("topic_boosts", {})
                skip_topics = [s.get("pattern", "") for s in fb.get("skip_topics", [])]
                analytics_fresh = True
                log(f"   📊 Analytics loaded: {len(topic_boosts)} boosts, {len(skip_topics)} skip")
        except (ValueError, TypeError):
            log(f"   ⚠️ Invalid generated_at — using defaults")
    else:
        # Backward compat: no generated_at, use as-is
        topic_boosts = fb.get("topic_boosts", {})
        skip_topics = [s.get("pattern", "") for s in fb.get("skip_topics", [])]
        analytics_fresh = True
        if topic_boosts or skip_topics:
            log(f"   📊 Analytics loaded (no timestamp): {len(topic_boosts)} boosts, {len(skip_topics)} skip")
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
    # Skip women's football
    title_lower = title.lower()
    desc_lower = (t.get("description") or "").lower()
    url_lower = url.lower()
    women_kw = ["women", "women's", "womens", "female", "lionaesses", "shebelieves", "nwsl", "wsl"]
    if any(kw in title_lower or kw in desc_lower or kw in url_lower for kw in women_kw):
        continue
    # Skip TV guides / jadwal siaran
    tv_guide_kw = ["tv channel", "live stream", "kick-off time", "kickoff time", "how to watch",
                   "where to watch", "what channel", "kick off time", "start time", "stream online"]
    if any(kw in title_lower for kw in tv_guide_kw):
        continue
    # Skip sensitive content (legal/sexual/crime/SARA)
    sensitive_kw = [
        # Sexual / body-part sexualization (tabloid clickbait)
        "breasts", "boobs", "nipple", "topless", "nude", "naked",
        "strip", "stripped", "stripped naked",
        "wardrobe malfunction", "see-through", "see through",
        "underwear", "lingerie", "bikini", "thong",
        # Sexual
        "rape", "sexual assault", "sexual abuse", "stand trial",
        "pedo", "child abuse",
        # Crime
        "charged with", "convicted of", "guilty of",
        "domestic violence", "murder charge", "stabbing", "shooting",
        # SARA (Suku, Agama, Ras, Antargolongan)
        "racist", "racism", "racial", "racial abuse", "racial slur",
        "hate crime", "hate speech", "xenophobia", "xenophobic",
        "islamophobia", "islamophobic", "antisemitic", "antisemitism",
        "anti-semitic", "anti-islam", "anti-muslim", "anti-christian",
        "ethnic cleansing", "genocide", "ethnic violence",
        "sectarian", "communal violence", "religious conflict",
        "blasphemy", "apostasy", "heresy",
        "far-right", "neo-nazi", "white supremacist", "extremist",
        "terrorism", "terrorist", "bombing",
        # Discrimination
        "discrimination", "slavery", "slave trade", "apartheid",
        "ethnic tension", "tribal clash", "clan violence",
    ]
    if any(kw in title_lower or kw in desc_lower for kw in sensitive_kw):
        log(f"   🚫 Sensitive content skipped: {title[:50]}")
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
        kw_hits = sum(1 for kw in research_keywords_add if kw.lower() in title_lower)
        if kw_hits > 0:
            t["_kw_boost"] = kw_hits * 10
    filtered.append(t)

log(f"   After filter: {len(filtered)} topics")
if not filtered:
    log("❌ No topics after filter — exit")
    print(f"❌ Pipeline failed: All {len(all_topics)} topics filtered out", flush=True)
    sys.exit(1)

# ── 3. SCORE — pick best ──────────────────────────────────────────
for t in filtered:
    t["_score"] = score_topic(t)
    t["_topic_type"] = classify_topic_type(t["title"])

filtered.sort(key=lambda x: -x["_score"])
best = filtered[0]
log(f"   🏆 Best: {best['title']} (score={best['_score']})")

# ── 4. EXTRACT (curl + og:image) ──────────────────────────────────
t0 = time.time()
url = best["url"]

# ARTICLE CACHE — avoid re-fetching same URL within 30 min
ARTICLE_CACHE = f"{HOME}/.hermes/pressbox/article_cache.json"
article_cache = {}
if os.path.exists(ARTICLE_CACHE):
    try:
        with open(ARTICLE_CACHE) as f:
            article_cache = json.load(f)
    except Exception:
        article_cache = {}

_was_cached = url in article_cache and time.time() - article_cache[url].get("ts", 0) < 1800
if _was_cached:
    article_text = article_cache[url]["text"]
    image_url = article_cache[url].get("image", "")
    image_width = article_cache[url].get("w", 0)
    image_height = article_cache[url].get("h", 0)
    log(f"   📦 Cache hit ({len(article_text)}c)")
else:
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

    # Strip HTML tags, CSS, scripts for clean article text
    article_text = re.sub(r"<style[^>]*>.*?</style>", " ", raw_html, flags=re.DOTALL|re.IGNORECASE)
    article_text = re.sub(r"<script[^>]*>.*?</script>", " ", article_text, flags=re.DOTALL|re.IGNORECASE)
    article_text = re.sub(r"<[^>]+>", " ", article_text)
    article_text = re.sub(r"\s+", " ", article_text).strip()
    article_text = html_mod.unescape(article_text)

    # Extract og:image
    image_url = ""
    image_width = 0
    image_height = 0

    def is_threads_compatible(url):
        blocked = ["guim.co.uk", "guardian.co.uk"]
        return not any(b in url.lower() for b in blocked)

    for pattern in [
        r'<meta\s+property="og:image"\s+content="([^"]+)"',
        r'<meta\s+name="og:image"\s+content="([^"]+)"',
        r'<meta\s+property="twitter:image"\s+content="([^"]+)"',
        r'<meta\s+name="twitter:image"\s+content="([^"]+)"',
    ]:
        m = re.search(pattern, raw_html, re.IGNORECASE)
        if m:
            candidate = m.group(1)
            if not is_threads_compatible(candidate):
                continue
            try:
                accessible, status = check_image_accessible(candidate)
                if accessible:
                    is_valid, w, h = validate_image_quality(candidate)
                    if is_valid:
                        image_url = candidate
                        image_width = w
                        image_height = h
                        log(f"   ✅ OG image: {w}x{h}")
                        break
            except Exception as e:
                log(f"   ⚠️ Image check failed: {e}")

    # Fallback 1: body image
    if not image_url:
        body_img = extract_body_image(raw_html)
        if body_img and is_threads_compatible(body_img):
            try:
                accessible, status = check_image_accessible(body_img)
                if accessible:
                    is_valid, w, h = validate_image_quality(body_img)
                    if is_valid:
                        image_url = body_img
                        image_width = w
                        image_height = h
                        log(f"   ✅ Body image: {w}x{h}")
            except Exception as e:
                log(f"   ⚠️ Image check failed: {e}")

    # Fallback 2: RSS image
    if not image_url:
        candidate = best.get("image_url", "") or ""
        if candidate and is_threads_compatible(candidate):
            try:
                accessible, status = check_image_accessible(candidate)
                if accessible:
                    is_valid, w, h = validate_image_quality(candidate)
                    if is_valid:
                        image_url = candidate
                        image_width = w
                        image_height = h
                        log(f"   ✅ RSS image: {w}x{h}")
            except Exception as e:
                log(f"   ⚠️ Image check failed: {e}")

    # Cache article for next run
    article_cache[url] = {"text": article_text, "image": image_url, "w": image_width, "h": image_height, "ts": time.time()}
    # Evict old entries (keep last 100)
    if len(article_cache) > 100:
        sorted_urls = sorted(article_cache.keys(), key=lambda u: article_cache[u].get("ts", 0))
        for old_url in sorted_urls[:len(article_cache) - 100]:
            del article_cache[old_url]
    try:
        with open(ARTICLE_CACHE, "w") as f:
            json.dump(article_cache, f)
    except Exception:
        pass

log(f"   Article: {len(article_text)} chars, image: {'yes' if image_url else 'no'}")

if not article_text or len(article_text) < 100:
    log("❌ Article text too short — exit")
    sys.exit(1)

# ── 5. LLM call ───────────────────────────────────────────────────
t0 = time.time()

# ── Model routing by article type ────────────────────────────────
topic_type = best.get("_topic_type", "other")
MODEL_CHAIN = get_model_config(topic_type)
ACTIVE_MODEL = MODEL_CHAIN[0]["model"]
ACTIVE_MAX_TOKENS = MODEL_CHAIN[0]["max_tokens"]
ACTIVE_REASONING = MODEL_CHAIN[0]["reasoning_effort"]
log(f"   📦 Topic type: {topic_type} → Chain: {' → '.join(m['model'] for m in MODEL_CHAIN)}")

# ── PROMPT v7.4: Hybrid (anti-hallucination + 6-beat narrative spine) ──
# Combines v7.3's strict grounding layers with new prompt's structural improvements:
# - [PROCESS]: fact-bank first → 6-beat narrative spine → setup-payoff flow
# - S6 must callback to S1's hook (image/quote/scene/contrast) — drives comment loop
# - Per-slide MIN sentence tags (prevents under-write flakiness)
# - Complete JSON FORMAT example (reduces format errors)
# - GROUNDING — STRICT: verbatim from article, no outside knowledge
# - REJECTION: emits {"error":"insufficient_source",...} if can't fill 6 honestly
# - STYLE: generalized banned-phrase rule + AI throat-clearing list
# Verified: 3/3 dry-runs pass on different articles, 0 hallucinations, S1↔S6 callback fires
# Build dynamic sections from analytics
_dynamic_hooks = ""
if preferred_hooks:
    _dynamic_hooks = f"\n- PREFERRED HOOKS (from analytics): {', '.join(preferred_hooks[:3])}. Prioritize these."
_dynamic_cta = ""
if cta_pattern:
    _dynamic_cta = f"\n- CTA PATTERN (from analytics): {cta_pattern}"
_dynamic_tone = ""
if tone_adjustment and tone_adjustment != "Conversational English. Bold numbers. High-impact words.":
    _dynamic_tone = f"\n- TONE: {tone_adjustment}"

system_prompt = f"""Football content strategist for Threads. Output EXACTLY 6-slide JSON thread from the article provided.

[STRATEGY]
6-post chained thread (Threads native "Add to thread" pattern). Each slide replies to the previous via reply_to_id, NOT siblings of root.
- S1 (root): HOOK — 1-3 sentences. End with tension.
- S2 (replies to S1): WHAT — 3-4 sentences.
- S3 (replies to S2): TENSION — 2-4 sentences.
- S4 (replies to S3): HUMAN — 2-4 sentences.
- S5 (replies to S4): UNRESOLVED — 2-3 sentences.
- S6 (replies to S5): CTA — 2-4 sentences. Last line: {{url}}

[PROCESS — internal only]
1. Read article. FACT BANK: names, scores, minutes, quotes, stats, dates, venue.
2. NARRATIVE SPINE: HOOK → WHAT → TENSION → HUMAN → UNRESOLVED → CTA.
3. Last sentence of slide N sets up first sentence of slide N+1.
4. S6 callbacks S1's hook.

[SOURCE HANDLING]
Use only article body. Ignore nav, related links, ads, bylines, boilerplate.

[SLIDES — MIN sentence counts]
1. HOOK (1-3, MIN 1): Most controversial/surprising/paradoxical fact. End with tension.
2. WHAT (3-4, MIN 3): What happened concretely + why it matters.
3. TENSION (2-4, MIN 2): Conflict/competing stakes. One-sided: "Article only covers [X]'s perspective."
4. HUMAN (2-4, MIN 2): One named person, own words or reported feelings. No quote: "No direct quote from [Name]" + one sentence on situation.
5. UNRESOLVED (2-3, MIN 2): What's left open.
6. CTA (2-4, MIN 2): Sharp opinion + debatable yes/no question. MUST callback S1. Last line: """ + url + """

[FORMAT — JSON only, no fences]
{{"slide_1":{{"title":"HOOK","content":"..."}},"slide_2":{{"title":"WHAT","content":"..."}},"slide_3":{{"title":"TENSION","content":"..."}},"slide_4":{{"title":"HUMAN","content":"..."}},"slide_5":{{"title":"UNRESOLVED","content":"..."}},"slide_6":{{"title":"CTA","content":"..."}}}}

[GROUNDING — STRICT]
- Names, scores, dates, quotes: verbatim from article. No outside knowledge.
- Missing detail = omit or flag. Never infer feelings.
- S5-6 may have opinion but trace to specific stated facts.

[REJECTION]
Can't fill 6 slides honestly? Output: {{"error":"insufficient_source","reason":"..."}}

[STYLE]
- Conversational plain English. One idea per sentence, each followed by \\n\\n.
- No em-dash (—), no hashtags, no bullets, no ALL CAPS, no AI throat-clearing.
- Indonesian articles: keep names original, write content in English."""

user_prompt = f"ARTICLE: {article_text}\n[Note: article may be truncated. Use only what is provided above.]\nSOURCE: {url}"

log(f"   Calling LLM ({ACTIVE_MODEL})...")
llm_t0 = time.time()

# Note: per-model headers + URL are set INSIDE the loop (see below)
# so each chain entry uses its own provider's credentials.

# ── LLM call with streaming + retry ────────────────────────────
MAX_RETRIES = 3
# Sentence count targets per slide (min, max) — 6-slide format
SENTENCE_COUNTS = {
    1: (1, 3),   # Hook: 1-3 sentences (sharp single sentence OK)
    2: (3, 4),   # What: 3-4 sentences
    3: (2, 4),   # Tension: 2-4 sentences
    4: (2, 4),   # Human: 2-4 sentences
    5: (2, 3),   # Unresolved: 2-3 sentences
    6: (2, 4),   # CTA: 2-4 sentences
}
# Hard char cap per slide (Threads API limit = 500 chars/slide).
# Auto-trim cuts by sentence; if a single sentence is too long, char-trim fires here.
MAX_CHARS_PER_SLIDE = 500

def _count_sentences(text: str) -> int:
    """Count sentences by splitting on sentence-ending punctuation."""
    sents = re.split(r'(?<=[.!?])\s+', text.strip())
    return len([s for s in sents if len(s.strip()) > 5])
raw_json = ""

for attempt in range(1, MAX_RETRIES + 1):
    # Cycle through model chain
    model_idx = (attempt - 1) % len(MODEL_CHAIN)
    chain_entry = MODEL_CHAIN[model_idx]
    ACTIVE_MODEL = chain_entry["model"]
    ACTIVE_MAX_TOKENS=chain_entry["max_tokens"]
    ACTIVE_REASONING = chain_entry["reasoning_effort"]
    _provider = get_provider_for_model(ACTIVE_MODEL)
    ACTIVE_URL = _provider["base_url"]
    ACTIVE_KEY = _provider["api_key"]

    if not ACTIVE_KEY:
        log(f"   ⚠️ No API key for {ACTIVE_MODEL} — skipping to next chain entry")
        continue

    log(f"   LLM attempt {attempt}/{MAX_RETRIES} ({ACTIVE_MODEL} via {ACTIVE_URL.split('/')[2]})...")
    try:
        payload = {
            "model": ACTIVE_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt.replace("{url}", url)},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": ACTIVE_MAX_TOKENS,
            "temperature": 0.5,
            "stream": True,
        }
        if ACTIVE_REASONING:
            payload["reasoning_effort"] = ACTIVE_REASONING
        headers = {"Content-Type": "application/json"}
        if ACTIVE_KEY:
            headers["Authorization"] = f"Bearer {ACTIVE_KEY}"
        r = requests.post(
            ACTIVE_URL,
            headers=headers,
            json=payload,
            timeout=180,
            stream=True,
        )
        if r.status_code != 200:
            log(f"❌ API error: HTTP {r.status_code} {r.text[:200]}")
            # Always try next model in chain (Mistral 4xx/5xx → fall through to MiniMax)
            # Only sys.exit after ALL chain entries exhausted.
            if attempt < MAX_RETRIES:
                log(f"   Trying next model in chain ({attempt}/{MAX_RETRIES})...")
                time.sleep(2 + attempt)
                continue
            print(f"❌ Pipeline failed: LLM API error HTTP {r.status_code} after {MAX_RETRIES} attempts", flush=True)
            sys.exit(1)
        
        # Process SSE stream
        content_parts = []
        reasoning_parts = []
        chunk = {}  # Initialize for usage extraction
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
                    continue  # Skip chunks with no choices (minimax sends empty initial chunks)
                delta = choices[0].get("delta", {})
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])
                if "reasoning_content" in delta and delta["reasoning_content"]:
                    reasoning_parts.append(delta["reasoning_content"])
                if "reasoning" in delta and delta["reasoning"]:
                    reasoning_parts.append(delta["reasoning"])
            except (json.JSONDecodeError, IndexError, KeyError):
                continue
        
        content = "".join(content_parts).strip()
        reasoning = "".join(reasoning_parts).strip()
        usage = chunk.get("usage", {}) if chunk else {}
        prompt_tok = usage.get("prompt_tokens", 0)
        completion_tok = usage.get("completion_tokens", 0)
        total_tok = usage.get("total_tokens", 0)

        log(f"   Response: content={len(content)} chars, reasoning={len(reasoning)} chars")
        log(f"   Tokens: prompt={prompt_tok} completion={completion_tok} total={total_tok}")

        # Extract JSON — content first, then reasoning (deepseek puts JSON there)
        candidate_json = ""
        if content:
            # Strip <think>...</think> block (MiniMax-M3 / reasoning models wrap output)
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            candidate_json = re.sub(r"^```(?:json)?\s*", "", content)
            candidate_json = re.sub(r"\s*```$", "", candidate_json)
            candidate_json = candidate_json.strip()

        if not candidate_json and reasoning:
            log("   Content empty, extracting from reasoning...")
            log(f"   Reasoning preview: {reasoning[:300]}...")

            # Strategy 1: Find JSON with slide markers (search full reasoning)
            for marker in ["slide_1", "slides"]:
                idx = 0
                while idx < len(reasoning):
                    start = reasoning.find('{', idx)
                    if start == -1:
                        break
                    depth = 0
                    end = -1
                    for i in range(start, len(reasoning)):
                        if reasoning[i] == '{': depth += 1
                        elif reasoning[i] == '}': depth -= 1
                        if depth == 0:
                            end = i
                            break
                    if end == -1:
                        break
                    try:
                        obj = json.loads(reasoning[start:end+1])
                        if isinstance(obj, dict) and marker in obj:
                            # Validate: check content length
                            sample = ""
                            for k in ["slide_1", "slide_2", "slides"]:
                                if k in obj:
                                    v = obj[k]
                                    if isinstance(v, dict):
                                        sample = v.get("content", "")
                                    elif isinstance(v, list) and len(v) > 0:
                                        sample = v[0] if isinstance(v[0], str) else str(v[0])
                                    break
                            if len(sample) > 50:  # Real content, not placeholder
                                candidate_json = reasoning[start:end+1]
                                log(f"   Found JSON in reasoning ({len(candidate_json)}c, key={marker}, sample={len(sample)}c)")
                                break
                            else:
                                log(f"   Skipping JSON ({len(sample)}c sample too short)")
                    except json.JSONDecodeError:
                        pass
                    idx = end + 1
                if candidate_json:
                    break

            # Strategy 2: Find LAST valid JSON with real content (fallback)
            if not candidate_json:
                log("   Strategy 2: scanning for last valid JSON with content...")
                best_json = ""
                best_score = 0
                # Scan from end, find valid JSON with 8+ keys
                # Optimization: break inner loop after first valid JSON (it's the largest from this })
                for i in range(len(reasoning) - 1, max(len(reasoning) - 50000, -1), -1):
                    if reasoning[i] == '}':
                        for j in range(i, max(i - 15000, -1), -1):
                            if reasoning[j] == '{':
                                try:
                                    obj = json.loads(reasoning[j:i+1])
                                    if isinstance(obj, dict) and len(obj) >= 8:
                                        total_content = sum(len(v.get("content", "")) for v in obj.values() if isinstance(v, dict))
                                        if total_content > best_score:
                                            best_score = total_content
                                            best_json = reasoning[j:i+1]
                                        break  # First valid JSON = largest from this }
                                except json.JSONDecodeError:
                                    pass
                        if best_json:
                            break

                if best_json and best_score > 500:
                    candidate_json = best_json
                    log(f"   Strategy 2 found JSON ({len(candidate_json)}c, score={best_score})")
                elif best_json:
                    log(f"   Strategy 2 found JSON but low score ({best_score}), trying anyway...")
                    candidate_json = best_json

        if not candidate_json:
            log("   ❌ No JSON found, retrying...")
            continue

        # Fix: Mistral often wraps JSON in prose like "Here's the JSON:\n```json\n{...}\n```\n".
        # The fence-strip regex above only catches fences at line start, but Mistral
        # adds prose BEFORE the fence. Find first { and last } to extract just the JSON.
        first_brace = candidate_json.find('{')
        last_brace = candidate_json.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            stripped = candidate_json[first_brace:last_brace + 1]
            if stripped != candidate_json:
                log(f"   ✂️  Extracted JSON from prose wrapper ({len(candidate_json)}c → {len(stripped)}c)")
                candidate_json = stripped

        # Fix: Handle truncated JSON from minimax models (missing closing braces)
        # Always check brace count, even if ends with }
        open_braces = candidate_json.count('{')
        close_braces = candidate_json.count('}')
        missing = open_braces - close_braces
        if missing > 0:
            candidate_json += '}' * missing
            log(f"   🔧 Fixed truncated JSON (added {missing} closing brace(s)")
        elif candidate_json and not candidate_json.endswith("}"):
            # Edge case: doesn't end with } at all
            candidate_json += '}'
            log("   🔧 Fixed truncated JSON (added closing brace)")

        # Parse and validate sentence count (with auto-trim for over-max slides)
        slides_data = json.loads(candidate_json)
        sentence_issues = []
        trimmed_count = 0
        # Handle both formats
        if "slides" in slides_data and isinstance(slides_data["slides"], list):
            slide_list = slides_data["slides"]
        else:
            slide_list = [slides_data.get(f"slide_{i}", {}) for i in range(1, 7)]

        for i, s in enumerate(slide_list[:6]):  # slides 1-6
            if not isinstance(s, dict):
                sentence_issues.append(f"s{i+1}: not a dict")
                continue
            body = s.get("content") or ""
            n = _count_sentences(body)
            min_s, max_s = SENTENCE_COUNTS.get(i + 1, (3, 4))
            if n > max_s:
                # Auto-trim: keep first max_s sentences (don't reject — just clip)
                parts = re.split(r'(?<=[.!?])\s+', body.strip())
                trimmed_parts = [p for p in parts if len(p.strip()) > 5][:max_s]
                if trimmed_parts:
                    s["content"] = " ".join(trimmed_parts)
                    trimmed_count += 1
            elif n < min_s:
                sentence_issues.append(f"s{i+1}: {n}s < {min_s}")

            # Char-cap safety net: Threads API hard limit is 500 chars/slide.
            # Cuts to last sentence-ending within the limit, or just truncates if no boundary.
            if len(s.get("content", "")) > MAX_CHARS_PER_SLIDE:
                text = s["content"][:MAX_CHARS_PER_SLIDE]
                # Try to keep last full sentence within the cap
                last_period = max(text.rfind(". "), text.rfind("! "), text.rfind("? "))
                if last_period > 50:  # keep at least some content
                    s["content"] = text[:last_period + 1]
                else:
                    s["content"] = text.rstrip() + "…"
                log(f"   ✂️ Char-trimmed s{i+1} to {len(s['content'])} chars")

        if not sentence_issues:
            if trimmed_count:
                log(f"   ✂️ Auto-trimmed {trimmed_count} slide(s) to SENTENCE_COUNTS max")
            log(f"   ✅ All slides pass sentence count")
            # Re-serialize the (possibly trimmed) dict back to JSON
            raw_json = json.dumps(slides_data, ensure_ascii=False)
            break
        else:
            log(f"   ⚠️ Sentence count fail: {', '.join(sentence_issues)} — retrying...")

    except Exception as e:
        log(f"❌ LLM exception: {e}")
        continue

if not raw_json:
    log(f"❌ Failed to get valid slides after {MAX_RETRIES} retries")
    sys.exit(1)

# ── Parse & validate slides ──────────────────────────────────────

# SINGLE VALIDATION FUNCTION (sentence-count based)
def validate_and_fix(slides: list) -> tuple:
    """Validate slides by sentence count, return (ok, errors)."""
    errors = []
    for i, s in enumerate(slides):
        c = s["content"]
        n = _count_sentences(c)
        min_s, max_s = SENTENCE_COUNTS.get(i + 1, (3, 5))
        if n < min_s:
            errors.append(f"s{i+1}: {n}s < {min_s}")
        elif n > max_s + 2:  # +2 tolerance for final validation (retry already caught +1)
            # Auto-trim: keep first max_s sentences
            parts = re.split(r'(?<=[.!?])\s+', c.strip())
            trimmed_parts = [p for p in parts if len(p.strip()) > 5][:max_s]
            s["content"] = " ".join(trimmed_parts)
            errors.append(f"s{i+1}: trimmed from {n}s to {max_s}s")
    return len(errors) == 0, errors

try:
    slides_data = json.loads(raw_json)
except json.JSONDecodeError as e:
    log(f"❌ JSON parse error: {e}")
    sys.exit(1)

slides = []
MAX_SLIDES = 6

# Handle both formats: {"slide_1": {...}} and {"slides": [...]}
if "slides" in slides_data and isinstance(slides_data["slides"], list):
    for i, s in enumerate(slides_data["slides"]):
        if isinstance(s, str):
            titles = ['HOOK', 'WHAT', 'TENSION', 'HUMAN', 'UNRESOLVED', 'CTA']
            slides.append({"title": titles[i] if i < len(titles) else f"Slide {i+1}", "content": s.strip()})
        elif isinstance(s, dict):
            title = (s.get("title") or "").strip()
            slide_content = (s.get("content") or "").strip()
            if not slide_content:
                log(f"❌ slides[{i}] empty content")
                sys.exit(1)
            slides.append({"title": title or f"Slide {i+1}", "content": slide_content})
        else:
            log(f"❌ slides[{i}] unexpected type: {type(s)}")
            sys.exit(1)
else:
    for i in range(1, 7):
        key = f"slide_{i}"
        if key not in slides_data:
            alt_key = f"Slide {i}"
            if alt_key in slides_data:
                key = alt_key
            else:
                log(f"❌ Missing {key}. Keys: {list(slides_data.keys())}")
                sys.exit(1)
        slide = slides_data[key]
        if not isinstance(slide, dict):
            log(f"❌ {key} not a dict")
            sys.exit(1)
        title = (slide.get("title") or "").strip()
        slide_content = (slide.get("content") or "").strip()
        if not title or not slide_content:
            log(f"❌ {key} missing title or content")
            sys.exit(1)
        slides.append({"title": title, "content": slide_content})

# Truncate to MAX_SLIDES (model sometimes generates extra empty slides)
if len(slides) > MAX_SLIDES:
    log(f"   Truncating {len(slides)} slides → {MAX_SLIDES}")
    slides = slides[:MAX_SLIDES]

# Single validation pass
ok, errors = validate_and_fix(slides)
if not ok:
    log(f"⚠️ Validation fail: {', '.join(errors)}")
    # Don't exit — slides may still be usable
    if len(errors) > 4:
        sys.exit(1)

# Build joined content (no titles, just content)
# Post-process: replace em-dashes and en-dashes
for s in slides:
    s["content"] = s["content"].replace("—", " - ").replace("–", " - ")
    # Clean up double spaces around replaced dashes
    s["content"] = re.sub(r"  +", " ", s["content"])
    s["content"] = re.sub(r" ,", ",", s["content"])
    s["content"] = re.sub(r" \.", ".", s["content"])
    # Enforce blank line after every sentence (if missing)
    s["content"] = re.sub(r'([.!?])(\s+)([A-Z"])', r'\1\n\n\3', s["content"])

# Guarantee source URL on last slide (CTA) — bulletproof, doesn't rely on model
if slides and url:
    last = slides[-1]
    if url not in last["content"]:
        # Strip trailing whitespace/newlines, then append URL as final line
        last["content"] = last["content"].rstrip() + "\n\n" + url

joined = "\n===\n".join(s["content"] for s in slides)

# ── 6. STAGE or DRY RUN ──────────────────────────────────────────
staging_obj = {
    "schema_version": 1,
    "status": "ready",
    "topic": best,
    "content": joined,
    "written_at": datetime.now(WIB).isoformat(),
    "is_wc": bool(best.get("wc_related") or best.get("wc_boost")),
    "is_transfer": bool(best.get("transfer_related")),
    "mode": "thread",
    "slides": len(slides),
    "image_url": image_url,
    "image_width": image_width,
    "image_height": image_height,
}

if DRY_RUN:
    # Print JSON to stdout, skip staging
    print(json.dumps(staging_obj, indent=2))
    log(f"🔍 DRY RUN — {best['title']} ({len(slides)} slides, no staging)")
else:
    try:
        tmp = STAGING["v2"] + ".tmp"
        with open(tmp, "w") as f:
            json.dump(staging_obj, f, indent=2)
        os.replace(tmp, STAGING["v2"])
        log(f"✅ {best['title']}  ({len(slides)} slides) [{'WC' if staging_obj['is_wc'] else 'Transfer' if staging_obj['is_transfer'] else 'General'}]")
    except Exception as e:
        log_error(f"Staging write failed: {e}")
        log(f"❌ Staging write failed: {e}")
        print(f"❌ Pipeline failed: Staging write error — {e}", flush=True)
        sys.exit(1)

total = time.time() - START
llm_time = time.time() - t0

# Metrics logging
metrics = {
    "ts": datetime.now(WIB).isoformat(),
    "topic": best.get("title", "")[:60],
    "url": url,
    "scrape_s": round(t_scrape, 1),
    "llm_s": round(time.time() - llm_t0, 1),
    "total_s": round(total, 1),
    "slides": len(slides),
    "prompt_tok": prompt_tok,
    "completion_tok": completion_tok,
    "total_tok": total_tok,
    "reasoning_c": len(reasoning),
    "content_c": len(content),
    "image": bool(image_url),
    "cached": _was_cached,
}
METRICS_LOG = f"{HOME}/.hermes/pressbox/metrics.jsonl"
try:
    with open(METRICS_LOG, "a") as f:
        f.write(json.dumps(metrics) + "\n")
except Exception:
    pass

log(f"⏱️ Scrape:{t_scrape:.1f}s  LLM:{metrics['llm_s']}s  Total:{total:.1f}s  Tokens:{metrics['total_tok']} (prompt:{metrics['prompt_tok']} + completion:{metrics['completion_tok']})  Reasoning:{metrics['reasoning_c']}c")

# stdout for cron capture (log() goes to stderr only)
print(f"✅ Pipeline done: {best.get('title','?')[:60]} ({len(slides)} slides, {metrics['total_tok']} tokens, {total:.0f}s)", flush=True)
