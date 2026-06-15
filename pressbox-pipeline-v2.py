#!/usr/bin/env python3
"""
PRESS BOX PIPELINE v3 — Hybrid Version
Uses newspaper3k for clean extraction + Guardian RSS + JSON output.
With Smart Title Similarity Filter to prevent duplicate topics.
"""
import json, os, sys, re, time, html as html_mod, random
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dateutil import parser as date_parser

# Pindahkan requests ke top-level agar tidak ada overhead inline import
try:
    import requests
except ImportError:
    print("Error: 'requests' library is required. Please install it.", file=sys.stderr)
    sys.exit(1)

try:
    from newspaper import Article
    NEWSPAPER_AVAILABLE = True
except ImportError:
    NEWSPAPER_AVAILABLE = False

HOME = os.path.expanduser("~")
STAGING_FILE = f"{HOME}/.hermes/pressbox/staging.json"
STAGING_V3 = f"{HOME}/.hermes/pressbox/staging-v3.json"
POSTED_JSON = f"{HOME}/.hermes/pressbox/posted_topics.json"
CACHE_FILE = f"{HOME}/.hermes/pressbox/scrape_cache.json"
WIB = timezone(timedelta(hours=7))

# ===== OPTIMASI 1: SINGLE-PASS .ENV PARSER =====
def load_env():
    env_vars = {}
    _env_path = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(_env_path):
        with open(_env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip().strip('"').strip("'")
    return env_vars

env_config = load_env()
API_KEY = env_config.get("OPENCODE_GO_API_KEY", "")
BOT_TOKEN = env_config.get("TELEGRAM_BOT_TOKEN")
API_URL = "https://opencode.ai/zen/go/v1/chat/completions"
MODEL = "mimo-v2.5"
LLM_TIMEOUT = 90

os.makedirs(f"{HOME}/.hermes/pressbox", exist_ok=True)
ALERT_CHAT = "1022032312"

def send_alert(msg):
    if not BOT_TOKEN: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ALERT_CHAT, "text": f"⚠️ PRESS BOX — {msg}"}, timeout=10)
    except: pass

def log(msg):
    ts = datetime.now(WIB).strftime("%H:%M WIB")
    print(f"[{ts}] [PIPELINE] {msg}", flush=True, file=sys.stderr)

# ===== SCRAPE (imported from pressbox-research.py) =====
import importlib.util
_spec = importlib.util.spec_from_file_location("pressbox_research", f"{HOME}/.hermes/scripts/pressbox-research.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
scrape_rss = _mod.scrape_rss
scrape_mirror = _mod.scrape_mirror

# ===== SCRAPE CACHE =====
def load_scrape_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_scrape_cache(cache):
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f)

# ===== FRESHNESS CHECK =====
def is_fresh(t, hours=12):
    for field in ["published", "pubDate", "date", "timestamp"]:
        val = t.get(field)
        if not val:
            continue
        try:
            if isinstance(val, datetime):
                dt = val
            else:
                dt = date_parser.parse(str(val))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            diff = datetime.now(timezone.utc) - dt
            return timedelta(0) <= diff <= timedelta(hours=hours)
        except:
            continue
    return True

# ===== OPTIMASI 2: TEXT NORMALIZATION & RE-BUILT SIMILARITY FILTER =====
def clean_and_normalize_text(text):
    """Membersihkan teks dari karakter aneh dan menormalisasi entitas sepak bola yang umum."""
    if not text:
        return ""
    text = text.lower()
    # Normalisasi klub/entitas sepak bola populer agar Jaccard lebih akurat
    replacements = {
        "manchester city": "man city",
        "manchester united": "man utd",
        "real madrid": "madrid",
        "piala dunia": "world cup",
        "cristiano ronaldo": "ronaldo",
        # Normalisasi ranking/angka untuk dedup berita serupa
        "no.1": "number one",
        "no. 1": "number one",
        "#1": "number one",
        "number 1": "number one",
        "top spot": "number one",
        "ranked first": "number one",
        "return to the top": "back to number one",
        "comeback": "return",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Hapus simbol non-alfabet
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    return text

def get_word_set(text):
    """Extract meaningful words dari teks yang sudah dinormalisasi."""
    stopwords = {'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'as', 'is', 'was', 'are', 'were', 'be', 'been', 'has', 'have', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'can', 'shall', 'from', 'into', 'about', 'between', 'through', 'during', 'before', 'after', 'above', 'below', 'up', 'down', 'out', 'off', 'over', 'under', 'again', 'further', 'then', 'once', 'and', 'but', 'or', 'nor', 'not', 'so', 'very', 'just', 'than', 'too', 'also', 'its', 'it', 'he', 'she', 'they', 'we', 'you', 'i', 'my', 'your', 'his', 'her', 'our', 'their', 'this', 'that', 'these', 'those', 'who', 'whom', 'which', 'what', 'breaking', 'news', 'update', 'report'}
    cleaned = clean_and_normalize_text(text)
    words = set(re.findall(r'\b[a-z]{3,}\b', cleaned))
    return words - stopwords

def get_similarity_score(str1, str2):
    """Jaccard similarity on meaningful words."""
    words1 = get_word_set(str1)
    words2 = get_word_set(str2)
    if not words1 or not words2:
        return 0.0
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    return len(intersection) / len(union) if union else 0.0

def is_too_similar(new_title, posted_titles_list, threshold=0.35):
    """Check jika judul mirip. Threshold diturunkan sedikit (0.4 -> 0.35) agar lebih sensitif."""
    for posted_title in posted_titles_list:
        score = get_similarity_score(new_title, posted_title)
        if score >= threshold:
            return True, posted_title, score
    return False, None, 0.0

def has_topic_overlap(new_title, posted_titles_list):
    """Check overlap subjek krusial."""
    subject_combos = [
        ['trump', 'world cup'],
        ['trump', 'football'],
        ['infantino', 'scandal'],
        ['visa', 'denied'],
        ['omar', 'artan'],
    ]
    new_lower = clean_and_normalize_text(new_title)
    for combo in subject_combos:
        if all(word in new_lower for word in combo):
            for posted in posted_titles_list:
                posted_lower = clean_and_normalize_text(posted)
                if all(word in posted_lower for word in combo):
                    return True, posted, combo
    return False, None, None

def is_description_too_similar(new_desc, posted_descriptions_list, threshold=0.5):
    """Check if description mirip dengan artikel yang sudah dipost (Jaccard)."""
    if not new_desc or not posted_descriptions_list:
        return False, None, 0.0
    new_clean = clean_and_normalize_text(new_desc)
    for posted_desc in posted_descriptions_list:
        if not posted_desc:
            continue
        posted_clean = clean_and_normalize_text(posted_desc)
        score = get_similarity_score(new_clean, posted_clean)
        if score >= threshold:
            return True, posted_desc[:60], score
    return False, None, 0.0

# ===== HIGH ENGAGEMENT KEYWORDS =====
HIGH_ENGAGEMENT = [
    "furious", "outrage", "ban", "banned", "denied", "withheld",
    "chaos", "crisis", "scandal", "corruption", "boycott", "protest",
    "war", "conflict", "visa", "political", "geopolitical",
    "official statement", "hits back", "blasts",
    "emergency", "sabotage", "travel chaos", "fan ban",
]

ANALYTICS_FEEDBACK = f"{HOME}/.hermes/pressbox/analytics_feedback.json"
_feedback = {}
if os.path.exists(ANALYTICS_FEEDBACK):
    try:
        with open(ANALYTICS_FEEDBACK) as _f:
            _feedback = json.load(_f)
        log(f"📊 Loaded analytics feedback (boosts: {len(_feedback.get('topic_boosts', {}))} topics)")
    except: pass

def extract_topic_type(text):
    text = text.lower()
    topic_map = {
        "world_cup": ["world cup", "fifa", "qatar", "2026", "qualifier"],
        "transfer": ["transfer", "signing", "deal", "bid", "move to", "join"],
        "controversy": ["controversy", "scandal", "banned", "fined", "racism", " var"],
        "match_result": ["win", "lose", "defeat", "victory", "beat", "thrash"],
        "injury": ["injury", "injured", "out for", "sidelined"],
        "team_profile": ["guide", "profile", "squad", "predicted lineup"],
        "gossip": ["rumour", "rumor", "reportedly", "linked"],
        "young_talent": ["young", "academy", "debut", "breakthrough"],
        "record": ["record", "history", "first time", "milestone"],
    }
    for topic, patterns in topic_map.items():
        for pat in patterns:
            if pat in text:
                return topic
    return "general"

def apply_analytics_boost(score, title, description=""):
    if not _feedback:
        return score
    text = (title + " " + description[:300]).lower()
    topic_type = extract_topic_type(text)
    boosts = _feedback.get("topic_boosts", {})
    if topic_type in boosts:
        mult = boosts[topic_type]
        score = int(score * mult)
        log(f"  📈 Boost {topic_type}: {mult}x → score {score}")
    skip_topics = _feedback.get("skip_topics", [])
    for skip in skip_topics:
        pattern = skip.get("pattern", "")
        if pattern in text:
            score = int(score * 0.3)
            log(f"  ⏩ Skip pattern '{pattern}' → score {score}")
            break
    return score

def score_candidate(t):
    s = t.get("score", 0)
    text = (t.get("title", "") + " " + t.get("description", "")[:500]).lower()
    
    # ===== IMPROVEMENT #1: CONTROVERSY > NEWS (70/30 MIX) =====
    # Heavy boost for controversy/drama (what actually gets 50+ likes)
    controversy_keywords = [
        "outrage", "furious", "scandal", "banned", "boycott", "protest",
        "controversy", "chaos", "crisis", "collapse", "nightmare",
        "rip off", "steal", "corrupt", "liar", "lie", "fake",
        "destroy", "ruin", "collapse", "disaster", "embarrass",
    ]
    drama_keywords = [
        "secret", "hidden", "exposed", "revealed", "shocking",
        "unbelievable", "insane", "crazy", "wild", "epic",
        "historic", "legend", "hero", "villain", "underdog",
        "rivalry", "revenge", "comeback", "redemption", "last chance",
    ]
    
    controversy_score = sum(30 for kw in controversy_keywords if kw in text)
    drama_score = sum(20 for kw in drama_keywords if kw in text)
    
    # Penalize boring/generic news
    boring_keywords = [
        "team guide", "squad list", "lineup", "preview",
        "live updates", "live blog", "as it happens",
        "report", "article", "analysis", "opinion",
    ]
    boring_penalty = sum(-15 for kw in boring_keywords if kw in text)
    
    s += controversy_score + drama_score + boring_penalty
    
    # Boost if title is SHORT and PUNCHY (good hook indicator)
    title_len = len(t.get("title", "").split())
    if title_len <= 8:
        s += 15  # Short titles = better hooks
    elif title_len > 15:
        s -= 10  # Long titles = weak hooks
    
    wc_keywords = ["world cup", "piala dunia", "fifa", "qualifier", "wc 2026"]
    for kw in wc_keywords:
        if kw in text:
            s += 50
    if t.get("wc_related"):
        s += 40
    for kw in HIGH_ENGAGEMENT:
        if kw in text:
            s += 15
    viral_kw = ["goes viral", "fans react", "fans rage", "drama", "shock", "stunning"]
    for kw in viral_kw:
        if kw in text:
            s += 10
    if t.get("source", "") != "mirror":
        s += 3
    
    # R3: Viral factor boost (from analytics feedback loop)
    viral_factors = {
        "outrage_money": ["price", "cost", "debt", "money", "pay", "ticket", "$", "£"],
        "celebration": ["return", "finally", "historic", "first time", "comeback", "dream"],
        "human_story": ["fan", "player", "family", "nightmare", "journey", "fans", "crowd"],
        "controversy": ["ban", "denied", "furious", "outrage", "scandal", "protest", "boycott"],
        "record_milestone": ["record", "history", "milestone", "first ever", "made history"],
    }
    matched_factors = 0
    for factor_name, keywords in viral_factors.items():
        if any(kw in text for kw in keywords):
            s += 30
            matched_factors += 1
    # Stories with 3+ viral factors get extra boost
    if matched_factors >= 3:
        s += 50
    
    s = apply_analytics_boost(s, t.get("title", ""), t.get("description", ""))
    
    # R4: Time-based boost (best_hours from analytics)
    now_hour = datetime.now(WIB).hour
    if _feedback:
        best_h = _feedback.get("best_hours", [])
        worst_h = _feedback.get("worst_hours", [])
        if now_hour in best_h:
            s += 20  # Boost during peak hours
        elif now_hour in worst_h:
            s -= 10  # Reduce during low-engagement hours
    
    return s

# ===== ARTICLE EXTRACTION =====
def extract_article_newspaper(url):
    if not NEWSPAPER_AVAILABLE:
        return None
    try:
        article = Article(url)
        article.download()
        article.parse()
        if article.text and len(article.text) > 100:
            return article.text[:5000]
    except Exception as e:
        log(f"  newspaper3k failed: {e}")
    return None

def extract_article_curl(url):
    try:
        import subprocess
        r = subprocess.run(["curl", "-sL", "--max-time", "10",
            "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            url], capture_output=True, text=True, timeout=15)
        raw = r.stdout
        raw = html_mod.unescape(raw)
        raw = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', ' ', raw, flags=re.IGNORECASE)
        raw = re.sub(r'<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>', ' ', raw, flags=re.IGNORECASE)
        raw = re.sub(r'<[^>]+>', ' ', raw)
        raw = re.sub(r'\s+', ' ', raw).strip()
        if len(raw) > 100:
            return raw[:5000]
    except:
        pass
    return None

def extract_article(url):
    text = extract_article_newspaper(url)
    if text:
        log(f"  newspaper3k: {len(text)} chars")
        return text
    text = extract_article_curl(url)
    if text:
        log(f"  curl fallback: {len(text)} chars")
        return text
    return None

def extract_og_image(url):
    """Extract og:image URL from article HTML meta tags."""
    try:
        r = subprocess.run(["curl", "-sL", "--max-time", "8",
            "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            url], capture_output=True, text=True, timeout=12)
        html = r.stdout
        # og:image
        m = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html, re.IGNORECASE)
        if m:
            return m.group(1)
        # twitter:image fallback
        m = re.search(r'<meta\s+(?:name|property)="twitter:image"\s+content="([^"]+)"', html, re.IGNORECASE)
        if m:
            return m.group(1)
        # og:image:secure_url fallback
        m = re.search(r'<meta\s+property="og:image:secure_url"\s+content="([^"]+)"', html, re.IGNORECASE)
        if m:
            return m.group(1)
    except:
        pass
    return None

def is_garbage(text):
    if not text or len(text) < 100:
        return True
    indicators = ['@font-face', 'font-family', 'Page Not Found', 'src: url(']
    count = sum(1 for g in indicators if g in text[:500])
    if count >= 2:
        return True
    # Catch cookie consent / privacy pages that aren't real articles
    cookie_indicators = ['cookie', 'privacy policy', 'your privacy', 'accept all cookies',
                         'consent', 'data tracking', 'track your device', 'we use cookies',
                         'personalise your experience', 'manage cookies', 'reject all']
    cookie_count = sum(1 for c in cookie_indicators if c in text[:1000].lower())
    return cookie_count >= 3

# ===== LLM CALL =====
def call_llm(prompt):
    styles = [
        "Write like a passionate football fan who is FURIOUS about something. Lead with outrage.",
        "Write like a football insider breaking EXCLUSIVE news that will SHOCK people.",
        "Write like a fan who just discovered a HIDDEN TRUTH the media won't tell you.",
        "Write like a football journalist exposing a SCANDAL that affects every fan.",
        "Write like a fan sharing a story that will make people ANGRY or EXCITED.",
    ]
    style = random.choice(styles)
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    try:
        r = requests.post(API_URL, headers=headers, json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": f"You are an expert football thread writer for Threads (@parkthebus.football). {style} Write exactly 8 slides based ON THE PROVIDED ARTICLE."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 6000,
            "temperature": 0.7
        }, timeout=LLM_TIMEOUT)
        
        if r.status_code != 200:
            log(f"  API error: HTTP {r.status_code}")
            return None
            
        data = r.json()
        choices = data.get("choices", [])
        msg = choices[0].get("message", {}) if choices else {}
        
        content = (msg.get("content") or "").strip()
        if content:
            return content
            
        reasoning = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
        if reasoning:
            match = re.search(r'\{[\s\S]*\}', reasoning)
            if match:
                return match.group(0)
            return reasoning
        return None
    except Exception as e:
        log(f"  LLM error: {e}")
        return None

# ===== SLIDE HELPERS =====
def add_spacing(text):
    sents = re.split(r'(?<=[.!?])\s+', text.strip())
    if len(sents) < 3:
        return text
    lines = []
    for i in range(0, len(sents), 2):
        lines.append(' '.join(sents[i:i+2]))
    return '\n\n'.join(lines)

def parse_json_slides(raw):
    raw = re.sub(r'^```json\s*', '', raw.strip())
    raw = re.sub(r'^```\s*', '', raw.strip())
    raw = re.sub(r'\s*`$', '', raw.strip())
    
    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            candidate = match.group(0)
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                open_b = candidate.count('{') - candidate.count('}')
                open_sq = candidate.count('[') - candidate.count(']')
                if open_b > 0 or open_sq > 0:
                    candidate = re.sub(r',\s*"[^"]*":?\s*$', '', candidate)
                    candidate += ']' * open_sq + '}' * open_b
                    try:
                        data = json.loads(candidate)
                    except:
                        return None
                else:
                    return None
        else:
            return None
    if not data:
        return None
    
    slides = []
    for i in range(1, 9):
        key = f"slide_{i}"
        if key not in data:
            return None
        slide = data[key]
        title = slide.get("title", "").strip()
        content = slide.get("content", "").strip()
        if title and content:
            text = f"{title}\n\n{content}"
        elif content:
            text = content
        elif title:
            text = title
        else:
            return None
        slides.append(text)
    return slides if len(slides) == 8 else None

def validate_slides(slides, source_url):
    cleaned = []
    for i, s in enumerate(slides):
        s = s.strip()
        if len(s) < 80:
            log(f"  ⚠️ Slide {i+1} terlalu pendek ({len(s)} chars)")
            return None
        if len(s) > 400:
            trimmed = s[:400]
            last_bound = max(trimmed.rfind('. '), trimmed.rfind('? '), trimmed.rfind('! '))
            if last_bound > 150:
                s = trimmed[:last_bound + 1]
            else:
                s = trimmed
        s = add_spacing(s) if i < 7 else s
        cleaned.append(s)
    if len(cleaned) != 8:
        return None
    last = cleaned[-1]
    last_lines = last.split('\n')
    slide8_title = last_lines[0].strip() if last_lines else last
    
    if '?' not in slide8_title:
        log(f"  ❌ Slide 8 title missing CTA question: '{slide8_title[:50]}...'")
        return None
    
    q_pos = slide8_title.find('?')
    slide8_title = slide8_title[:q_pos + 1].strip()
    
    if len(last_lines) > 1:
        slide8_content = '\n'.join(last_lines[1:]).strip()
    else:
        slide8_content = ""
    
    # CTA content must be at least 2 sentences (not counting URL)
    if slide8_content:
        # Remove any URL from content before counting sentences
        clean_content = re.sub(r'https?://\S+', '', slide8_content).strip()
        sentences = [s.strip() for s in re.split(r'[.!?]+', clean_content) if s.strip()]
        if len(sentences) < 2:
            log(f"  ❌ Slide 8 content too short ({len(sentences)} sentence) — need 2+")
            return None
    
    cleaned[-1] = f"{slide8_title}\n\n{slide8_content}" if slide8_content else slide8_title
    
    last = cleaned[-1]
    last = re.sub(r'https?://\S+', '', last).strip()
    last = re.sub(r'[Ss]ource:.*$', '', last, flags=re.MULTILINE).strip()
    last = re.sub(r'\n{3,}', '\n\n', last).strip()
    last = add_spacing(last) if not slide8_title else last
    last += f'\n\n{source_url}'
    cleaned[-1] = last
    return cleaned

# ===== MAIN =====
log("=== PRESS BOX PIPELINE ===\n")

# 0. Guards
if os.path.exists(STAGING_FILE):
    try:
        with open(STAGING_FILE) as f:
            existing = json.load(f)
        if existing.get("topic") and existing.get("content"):
            log("⏸️ Staging unposted — skip")
            sys.exit(2)
    except: pass

# 1. Paralel scrape
cache = load_scrape_cache()
cache_ttl = 1800

log("Scraping articles (paralel)...")
start_scrape = time.time()

with ThreadPoolExecutor(max_workers=3) as ex:
    futures = {
        ex.submit(scrape_mirror): "mirror",
        ex.submit(scrape_rss, "https://www.theguardian.com/football/rss", "guardian", 14): "guardian",
    }
    all_topics = []
    for fut in as_completed(futures):
        src = futures[fut]
        try:
            topics = fut.result()
            for t in topics:
                t["url_verified"] = True
            all_topics.extend(topics)
            log(f"  {src}: {len(topics)} topics")
        except Exception as e:
            log(f"  {src}: ERROR {e}")

scrape_time = time.time() - start_scrape
log(f"  Total: {len(all_topics)} raw topics ({scrape_time:.1f}s)")

# 2. Filter (URL + title dedup + Smart Similarity + freshness)
posted_urls = set()
posted_titles_list = []
posted_descriptions_list = []

# OPTIMASI 3: Mengambil histori data postingan lama & hasil rewrite-nya (jika ada) untuk dedup yang maksimal
try:
    with open(POSTED_JSON) as f:
        data = json.load(f)
        for t in data.get("topics", []):
            if t.get("url"):
                posted_urls.add(t["url"])
            if t.get("title"):
                posted_titles_list.append(t["title"])
            # Kunci Duplikasi Teratasi: Kumpulkan juga judul alternatif/topik hasil rewrite AI jika tersimpan
            if t.get("topic_headline"):
                posted_titles_list.append(t["topic_headline"])
            # Kumpulkan deskripsi untuk dedup konten mirip
            if t.get("description"):
                posted_descriptions_list.append(t["description"])
except: pass

bad_sources = ['straitstimes', 'worldsoccertalk']
approved_sources = ['guardian', 'mirror']
candidates = []
skipped_similar = 0

for t in all_topics:
    url = t.get("url", "")
    title = t.get("title", "").strip()
    
    # Exact match check
    if url in posted_urls or not t.get("url_verified"):
        continue
    
    # Smart similarity filter (Menggunakan normalisasi teks baru)
    too_similar, matched_title, sim_score = is_too_similar(title, posted_titles_list, threshold=0.35)
    if too_similar:
        skipped_similar += 1
        log(f"  ⏩ Skip similar ({sim_score:.0%}): '{title[:40]}...' ≈ '{matched_title[:40]}...'")
        continue
    
    # Topic overlap filter
    topic_overlap, matched_topic, combo = has_topic_overlap(title, posted_titles_list)
    if topic_overlap:
        skipped_similar += 1
        log(f"  ⏩ Skip topic overlap ({'+'.join(combo)}): '{title[:40]}...' ≈ '{matched_topic[:40]}...'")
        continue
    
    # Description similarity filter (same event, different source)
    desc_too_similar, matched_desc, desc_score = is_description_too_similar(
        t.get("description", ""), posted_descriptions_list, threshold=0.5)
    if desc_too_similar:
        skipped_similar += 1
        log(f"  ⏩ Skip description similar ({desc_score:.0%}): '{title[:40]}...' ≈ '{matched_desc[:40]}...'")
        continue
    
    if t.get("source", "") in bad_sources or t.get("source", "") not in approved_sources:
        continue
    if not is_fresh(t, hours=12):
        continue
    if url in cache:
        try:
            cached_time = datetime.fromisoformat(cache[url])
            if (datetime.now(timezone.utc) - cached_time).total_seconds() < cache_ttl:
                continue
        except: pass
    candidates.append(t)

log(f"  {len(candidates)} candidates ({skipped_similar} similar filtered out)")

if not candidates:
    log("No candidates. [SILENT]")
    sys.exit(2)

# 3. Score + Sort
scored = sorted(candidates, key=score_candidate, reverse=True)
scores = [score_candidate(t) for t in scored]

top_n = min(5, len(scored))
top_candidates = scored[:top_n]
top_scores = scores[:top_n]

min_score = min(top_scores) if top_scores else 1
weights = [max(1, (s - min_score + 10) ** 2) for s in top_scores]

# ===== OPTIMASI 4: FALLBACK LOOP CANDIDATE SYSTEM =====
# Jika peringkat 1 gagal di urusan curl/ekstraksi/LLM, script akan otomatis mencoba urutan ke-2, dst.
success = False
staging_data = {}
cleaned = None
best = None
llm_time = 0

for pick_idx in range(len(top_candidates)):
    # Lakukan random pick tertimbang dari sisa top_candidates
    best = random.choices(top_candidates, weights=weights, k=1)[0]
    
    # Hapus dari list agar tidak terpilih lagi di iterasi fallback berikutnya jika gagal
    idx = top_candidates.index(best)
    top_candidates.pop(idx)
    weights.pop(idx)
    
    log(f"  Attempting Pick: {best['title'][:60]} [{best['source']}] (score: {score_candidate(best)})")
    
    # 4. Verify URL
    log(f"  Verifying URL: {best['url'][:60]}...")
    try:
        import subprocess
        vout = subprocess.run(
            ["curl", "-sI", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5", best["url"]],
            capture_output=True, text=True, timeout=7
        )
        http_code = vout.stdout.strip()
        if http_code not in ("200", "301", "302", "303", "307", "308"):
            log(f"  ❌ HTTP {http_code} - Skipping Candidate")
            cache[best["url"]] = datetime.now(timezone.utc).isoformat()
            save_scrape_cache(cache)
            continue
        log(f"  ✅ URL OK")
    except:
        log(f"  ⚠️ URL check failed — proceeding anyway")

    # 5. Extract article
    log("  Extracting article...")
    article_text = extract_article(best['url'])

    if not article_text or is_garbage(article_text):
        log(f"  ❌ Extraction failed / Garbage content — Skipping Candidate")
        cache[best["url"]] = datetime.now(timezone.utc).isoformat()
        save_scrape_cache(cache)
        continue

    log(f"  Article: {len(article_text)} chars")

    # 5b. Extract image for slide 1 (try RSS media:content first, then og:image fallback)
    image_url = best.get('image_url', '') or extract_og_image(best['url'])
    if image_url:
        log(f"  📷 Image: {image_url[:60]}...")
    else:
        log(f"  No image found")

    # 6. LLM Generate
    log("  LLM generate (mimo-v2.5)...")
    prompt = f"""ARTICLE:
{article_text}

SOURCE URL: {best['url']}

OUTPUT FORMAT — Return ONLY a valid JSON object. No markdown, no filler, no explanation.
{{
  "slide_1": {{"title": "[HOOK with NUMBER + DRAMA + CONTRAST]", "content": "[1-2 sentences teaser that sets up the story]"}},
  "slide_2": {{"title": "[THE PROBLEM]", "content": "[What happened? Start where S1 ended]"}},
  "slide_3": {{"title": "[THE CONTEXT]", "content": "[Why it matters — build from S2]"}},
  "slide_4": {{"title": "[THE COMPARISON]", "content": "[Similar past — connect to S3]"}},
  "slide_5": {{"title": "[HUMAN ANGLE]", "content": "[Quotes/emotion — flow from S4]"}},
  "slide_6": {{"title": "[BIGGER PICTURE]", "content": "[Implications — expand from S5]"}},
  "slide_7": {{"title": "[THE STAKES]", "content": "[Why care now — climax before CTA]"}},
  "slide_8": {{"title": "[PROVOCATIVE QUESTION with YOU/FANS?]", "content": "[3 sentence story summary]\\n\\n{best['url']}"}}
}}

RULES FOR CONTENT (Strict):
- Language: Conversational English, short punchy sentences. No em-dash, no hashtags.
- Facts: Use ONLY facts and names from the article.
- Length: 50-70 words per slide MAX. Do NOT over-write.
- Formatting: Every 2 sentences in "content", use "\\n\\n" for blank line.

====== SLIDE 1 — HOOK (10/10 STANDARD) ======

STEP 1 — ALWAYS lead with OUTRAGE or SHOCK.
Every hook MUST make people feel ANGRY or SURPRISED.
  - OUTRAGE: prices, corruption, ban, unfair treatment, scandal, rip off
  - SHOCK: unexpected result, drama, controversy, chaos, hidden truth
  - NEVER lead with celebration or neutral news — those don't go viral.
  - NEVER lead with a person's name or event name.

STEP 2 — Write the hook as EXACTLY TWO FRAGMENTS separated by period.
FORMAT: [Big NUMBER] [Context]. [Number/Year] [EMOTIONAL/Drama Word].

MANDATORY rules:
  - EXACTLY TWO fragments. NO full sentences. NO verbs.
  - Fragment 1 = NUMBER + CONTEXT (e.g. "$500 Empty Seats")
  - Fragment 2 = NUMBER/YEAR + DRAMA WORD (e.g. "180,000 Tickets")
  - Each fragment MUST contain a number with a CLEAR UNIT ($, million, %, years, fans, seats, etc.)
  - MAX 8 words TOTAL. Every word must earn its place.
  - NEVER a question. NEVER starts with a person's name.
  - NEVER use ambiguous numbers without unit: "59" is BAD, "59th minute" or "59 million" is GOOD

  "content" = 1-2 sentences teaser that makes reader NEED to scroll to S2

  10/10 examples (TWO FRAGMENTS — notice NO verbs):
  ✅ "$500 Empty Seats. 180,000 Tickets." [outrage + money + contrast]
  ✅ "40,000 Scots in Boston. 28-Year Nightmare Ends." [celebration + human story]
  ✅ "180-Second Scandal. Day One." [shock + urgency + drama]
  ✅ "3 Players Banned. 1 Hour Ago." [controversy + urgency]
  
  3/10 examples (DO NOT do this):
  ❌ Full sentence: "FIFA release laughable statement as empty seats..." [sentence, not fragments]
  ❌ Starts with name: "Gianni Infantino faces backlash..." [no emotion lead]
  ❌ Ambiguous: "Son's Nightmare at 59" [59 what? minutes? age?]

====== SLIDES 2-7 — STORYTELLING ARC (10/10 STANDARD) ======
CRITICAL: These slides must read like ONE continuous story, not separate posts.
 Each slide must START where the PREVIOUS slide ENDED.

Structure:
  Slide 2: THE PROBLEM — the specific event (start from S1's teaser)
  Slide 3: THE CONTEXT — background rules/history (build from S2)
  Slide 4: THE COMPARISON — similar past event (connect to S3)
  Slide 5: THE HUMAN ANGLE — quotes, emotion (flow from S4)
  Slide 6: THE BIGGER PICTURE — implications (expand from S5)
  Slide 7: THE STAKES — climax before CTA (build from S6)

Rules:
  - Each slide's FIRST sentence must REFERENCE the previous slide's topic
  - Use transition words: But here's the catch... / This matters because... / The result?
  - Never start a slide with a completely new topic — thread the story through
  - title: max 5 words, punchy label
  - content: 3-4 sentences, 50-70 words, ONE fact per slide
  - Build tension toward S7 — each slide should raise the stakes
   - Conversational English. Short words. Punchy rhythm.

====== SLIDE 8 — CTA QUESTION (10/10 STANDARD) ======

"title" rules:
  - MUST be a provocative DEBATE QUESTION ending with "?"
  - MUST divide opinion — some fans will agree, some won't
  - MUST include PERSONAL word: "you", "we", "fans", "us"
  - NEVER generic. Avoid: "What happens next?" "Will this work?" "Your thoughts?"
  - Instead: "Should WE accept..." "Would YOU pay..." "Are FANS right to..."
  - BEST: Questions that make people choose a SIDE (for/against)
  - EXAMPLES:
    ✅ "Is FIFA deliberately killing football for profit?"
    ✅ "Should FANS boycott the World Cup over these prices?"
    ✅ "Are WE being ripped off by greedy football bosses?"

  10/10 examples:
  ✅ "Should WE accept empty stadiums while FIFA jacks up prices?"
  ✅ "Would YOU pay $5,700 for a final ticket?"
  ✅ "Are FANS right to be furious at FIFA?"
  
  3/10 examples (DO NOT do this):
  ❌ "What happens next?" [generic]
  ❌ "Should FIFA cap prices to fill stadiums?" [no personal word]
  ❌ "Your thoughts?" [lazy]

"content" rules (STRICT — this is often wrong):
  - EXACTLY 3 sentences. Count them. No more, no less.
  - Sentence 1: Recap the core tension ("Empty stadiums on day one after prices soared")
  - Sentence 2: Why it matters now ("This could define the tournament's legacy")
  - Sentence 3: Hint at what's at stake ("Fans are watching — literally")
  - Then newline + URL {best['url']}
  - Do NOT use "Source:" prefix
  - Do NOT add hashtags or emoji in summary
"""
    start_llm = time.time()
    
    for attempt in range(2):
        result = call_llm(prompt)
        if result:
            if len(result) < 200:
                time.sleep(3)
                continue
            slides = parse_json_slides(result)
            if slides:
                cleaned = validate_slides(slides, best['url'])
                if cleaned:
                    log(f"  ✅ LLM success ({len(cleaned)} slides)")
                    success = True
                    break
        if attempt == 0:
            time.sleep(3)
            
    llm_time = time.time() - start_llm
    
    if success:
        break
    else:
        log(f"  ❌ LLM failed for this candidate, trying next one if available...")
        cache[best["url"]] = datetime.now(timezone.utc).isoformat()
        save_scrape_cache(cache)

if not success or not cleaned:
    log("❌ All top candidates failed to process after fallback attempts.")
    send_alert(f"Pipeline gagal memproses semua top candidates.")
    sys.exit(1)

# 7. Update cache
cache[best["url"]] = datetime.now(timezone.utc).isoformat()
save_scrape_cache(cache)

# 8. Save to staging
content = '\n---\n'.join(cleaned)
staging_data = {
    "topic": best,
    "content": content,
    "written_at": datetime.now(WIB).isoformat(),
    "is_wc": best.get("wc_related", False) or any(k in best.get("title", "").lower() for k in ["world cup", "piala dunia", "fifa"]),
    "is_transfer": best.get("transfer_related", False),
    "is_goldmine": False,
    "mode": "thread",
    "slides": len(cleaned),
    "image_url": image_url
}
with open(STAGING_FILE, 'w') as f:
    json.dump(staging_data, f, indent=2)

# 9. Log stats
total_time = time.time() - start_scrape
for i, s in enumerate(cleaned):
    log(f"  Slide {i+1}: {len(s)} chars")

wc_flag = "🏆 WC" if staging_data["is_wc"] else ("🔄 Transfer" if best.get("transfer_related") else "⚽ General")
print(f"✅ Generated: {best['title'][:50]} ({len(cleaned)} slides) [{wc_flag}] [{MODEL}]")
print(f"⏱️ Scrape: {scrape_time:.1f}s | LLM: {llm_time:.1f}s | Total: {total_time:.1f}s")
sys.exit(0)