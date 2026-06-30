#!/usr/local/bin/python3
"""Pressbox MVP — scrape, score, generate, post. One script, no staging."""
import subprocess as _sp, sys as _sys
for _p, _m in [("requests","requests"),("httpx","httpx"),("beautifulsoup4","bs4"),("python-dotenv","dotenv")]:
    try: __import__(_m)
    except ImportError: _sp.check_call([_sys.executable,"-m","pip","install","--quiet","--root-user-action=ignore",_p],stdout=_sp.DEVNULL,stderr=_sp.DEVNULL)

import html as html_mod, json, os, re, struct, sys, time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from pressbox_common import WIB, HOME, POSTED, load_env, log, clean_words, is_similar, classify_topic_type
from pressbox_scoring import score_topic as base_score_topic
import requests
from bs4 import BeautifulSoup

# ── Config ──────────────────────────────────────────────────────────
DRY_RUN = "--dry-run" in sys.argv
SOURCES = ["skysports", "goal", "bbc", "fourfourtwo"]
MAX_CHARS = 500  # Threads per-slide limit
SENTENCE_COUNTS = {1:(1,3), 2:(2,4), 3:(2,4), 4:(1,4), 5:(2,4), 6:(2,4)}
os.makedirs(f"{HOME}/.hermes/pressbox", exist_ok=True)

env = load_env()
MISTRAL_KEY = env.get("MISTRAL_API_KEY", "")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ── 1. SCRAPE ───────────────────────────────────────────────────────

def _http(url, timeout=8):
    """Simple HTTP GET with httpx, fallback to requests."""
    try:
        import httpx
        c = httpx.Client(headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True, verify=False)
        r = c.get(url)
        return r.status_code, r.text
    except Exception:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=True)
        return r.status_code, r.text

def scrape_rss(url, source, base_score=9):
    """RSS feed scraper."""
    topics = []
    try:
        import xml.etree.ElementTree as ET
        from email.utils import parsedate_to_datetime
        code, text = _http(url)
        if code != 200: return topics
        root = ET.fromstring(text)
        for item in root.findall('.//item')[:20]:
            te = item.find('title')
            le = item.find('link')
            if te is None or le is None: continue
            title = re.sub(r'^\s*<!\[CDATA\[(.*?)\]\]>\s*$', r'\1', (te.text or "").strip())
            title = html_mod.unescape(title)
            if not title or len(title) < 20: continue
            link = (le.text or "").strip().split("?")[0]
            # Skip live blogs — they're noise, not articles
            if '/live/' in link or '/liveblog/' in link: continue
            # Skip BBC video pages (short content)
            if '/videos/' in link: continue
            de = item.find('description')
            desc = re.sub(r'<[^>]+>', ' ', (de.text or "")).strip()[:500] if de is not None else ""
            desc = html_mod.unescape(desc)
            pe = item.find('pubDate')
            ts = None
            if pe is not None and pe.text:
                try: ts = parsedate_to_datetime(pe.text.strip()).timestamp()
                except: pass
            if ts and (time.time() - ts) > 86400: continue  # 24h freshness
            # Image: media:content first, fallback to enclosure
            img = ""
            for ns in ["http://search.yahoo.com/mrss/", "http://search.yahoo.com/mrss"]:
                for mc in item.findall(f'.//{{{ns}}}content'):
                    w = int(mc.get("width", 0))
                    if w > 0: img = mc.get("url", "")
            if not img:
                enc = item.find('enclosure')
                if enc is not None and 'image' in (enc.get('type', '')):
                    img = enc.get('url', '')
            topics.append(dict(title=title, source=source, url=link, score=base_score,
                               description=desc, published_ts=ts, image_url=img,
                               _needs_image_fallback=not bool(img)))
    except: pass
    return topics


def scrape_goal():
    """Goal.com scraper — direct homepage scrape (RSS broken)."""
    topics = []
    try:
        code, text = _http("https://www.goal.com/en")
        if code != 200: return topics
        soup = BeautifulSoup(text, 'html.parser')
        seen = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            if not re.search(r'/en/(?:news|lists|transfers|features)/', href): continue
            if href in seen: continue
            seen.add(href)
            title = a.get_text(strip=True)
            # Strip time prefix from breaking news ("5 hours agoDeschamps...")
            title = re.sub(r'^\d+\s+hours?\s+ago', '', title).strip()
            if not title or len(title) < 20: continue
            if title.startswith('🎥'): continue  # video-only content
            link = href if href.startswith('http') else "https://www.goal.com" + href
            topics.append(dict(title=title, source="goal", url=link, score=10,
                               description="", published_ts=None, image_url=""))
            if len(topics) >= 20: break
    except: pass
    return topics

def scrape_all():
    """Scrape all sources in parallel."""
    log("Scraping 4 sources...")
    t0 = time.time()
    all_t = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {
            "skysports": ex.submit(scrape_rss, "https://www.skysports.com/rss/11095", "skysports", 12),
            "goal": ex.submit(scrape_goal),
            "bbc": ex.submit(scrape_rss, "https://feeds.bbci.co.uk/sport/football/rss.xml", "bbc", 10),
            "fourfourtwo": ex.submit(scrape_rss, "https://www.fourfourtwo.com/rss", "fourfourtwo", 8),
        }
        for name, f in futs.items():
            try:
                r = f.result(timeout=15)
                log(f"   {name}: {len(r)} topics")
                all_t.extend(r)
            except Exception as e:
                log(f"   ⚠️ {name}: {e}")
    log(f"   Total: {len(all_t)} in {time.time()-t0:.1f}s")
    return all_t

# ── 2. FILTER + SCORE ──────────────────────────────────────────────

def load_posted():
    """Load posted URLs and title word-sets."""
    posted_urls, posted_ws = set(), []
    if os.path.exists(POSTED):
        try:
            with open(POSTED) as f:
                data = json.load(f)
            for t in (data.get("topics", []) if isinstance(data, dict) else data):
                u = (t.get("url") or "").strip()
                if u.startswith("http"): posted_urls.add(u)
                ti = (t.get("title") or "").strip()
                if ti: posted_ws.append(clean_words(ti))
        except: pass
    return posted_urls, posted_ws

def load_analytics():
    """DEPRECATED: static feedback files are dead. Live system (get_analytics_summary) handles all scoring."""
    return {}, [], [], "", ""  # ponytail: all boosts/skicks from get_analytics_summary now

def pull_engagement(poster):
    """Pull metrics for posts > 12h that haven't been tracked yet. Max 10 per run."""
    if not poster:
        return
    try:
        with open(POSTED) as f:
            data = json.load(f)
    except:
        return
    
    cutoff = time.time() - 43200  # 12 hours
    updated = 0
    failed = 0
    processed = 0
    MAX_PER_RUN = 10  # Limit to avoid timeout
    
    for topic in data.get("topics", []):
        if processed >= MAX_PER_RUN:
            break
        # Skip if already has metrics or already failed
        if topic.get("views") is not None or topic.get("metrics_failed"):
            continue
        # Skip if too recent
        posted_at = topic.get("posted_at", "")
        if posted_at:
            try:
                pt = datetime.fromisoformat(posted_at).timestamp()
                if pt > cutoff:
                    continue
            except:
                continue
        # Pull metrics
        post_id = topic.get("post_id")
        if not post_id:
            continue
        metrics = poster.get_metrics(post_id)
        processed += 1
        if metrics:
            topic["views"] = metrics.get("views", 0)
            topic["likes"] = metrics.get("likes", 0)
            topic["replies"] = metrics.get("replies", 0)
            topic["shares"] = metrics.get("shares", 0)
            updated += 1
        else:
            topic["metrics_failed"] = True
            failed += 1
        time.sleep(0.3)  # Rate limit courtesy
    
    if updated or failed:
        with open(POSTED, "w") as f:
            json.dump(data, f, indent=2)
        if updated:
            log(f"📊 Updated metrics for {updated} posts")
        if failed:
            log(f"⚠️ Metrics failed for {failed} posts (marked to skip)")

def get_analytics_summary():
    """Generate analytics summary from posted_topics.json data."""
    try:
        with open(POSTED) as f:
            data = json.load(f)
    except:
        return {}
    
    topics = data.get("topics", [])
    with_metrics = [t for t in topics if t.get("views") is not None]
    
    if len(with_metrics) < 3:
        return {}
    
    # Calculate averages by category
    from collections import defaultdict
    by_hook = defaultdict(list)
    by_topic = defaultdict(list)
    by_source = defaultdict(list)
    
    for t in with_metrics:
        views = t.get("views", 0)
        title = (t.get("title") or "").lower()
        source = (t.get("source") or "").lower()
        
        hook = _classify_hook(title)
        
        topic_type = classify_topic_type(title)
        by_hook[hook].append(views)
        by_topic[topic_type].append(views)
        by_source[source].append(views)
    
    # Calculate averages
    def avg(lst): return sum(lst) / len(lst) if lst else 0
    
    best_hooks = sorted(by_hook.items(), key=lambda x: avg(x[1]), reverse=True)
    best_topics = sorted(by_topic.items(), key=lambda x: avg(x[1]), reverse=True)
    best_sources = sorted(by_source.items(), key=lambda x: avg(x[1]), reverse=True)
    
    # Calculate median for threshold
    all_views = sorted([t.get("views", 0) for t in with_metrics])
    median_views = all_views[len(all_views) // 2] if all_views else 0
    
    summary = {
        "total_posts_with_metrics": len(with_metrics),
        "avg_views": avg([t.get("views", 0) for t in with_metrics]),
        "median_views": median_views,
        "best_hooks": [(h, avg(v)) for h, v in best_hooks[:3]],
        "best_topics": [(t, avg(v)) for t, v in best_topics[:5]],
        "best_sources": [(s, avg(v)) for s, v in best_sources],
        "worst_topics": [(t, avg(v)) for t, v in best_topics[-3:] if avg(v) < median_views * 0.5],
    }
    
    return summary

# Sensitive content filter
_SENSITIVE = [
    "breasts","boobs","topless","nude","naked","wardrobe malfunction",
    "rape","sexual assault","pedophilia","child abuse",
    "charged with","convicted of","guilty of","domestic violence","murder","stabbing","shooting",
    "racist","racism","racial abuse","hate crime","antisemitic","islamophobia",
    "genocide","ethnic cleansing","terrorism","bombing",
]
_TV_GUIDE = ["tv channel","live stream","kick-off time","kickoff time",
             "how to watch","where to watch","what channel","start time","stream online"]
_WOMEN = ["women","women's","womens","female","lionaesses","nwsl","wsl"]


def _classify_hook(title_lower):
    """Classify hook type for analytics boost. Returns: controversy/conflict/curiosity/event/statement."""
    if any(w in title_lower for w in ["slams", "blasts", "hits out", "furious", "outraged", "scandal", "controversy", "row", "rift", "bust-up", "war of words"]):
        return "controversy"
    if any(w in title_lower for w in ["vs", "against", "clash", "rival", "battle", "face off", "showdown"]):
        return "conflict"
    if any(w in title_lower for w in ["?", "how", "why", "what if", "can", "will", "could"]):
        return "curiosity"
    if any(w in title_lower for w in ["just", "dropped", "lost", "won", "banned", "sacked", "arrested", "injured", "denied"]):
        return "event"
    return "statement"

def filter_and_score(topics, posted_urls, posted_ws, boosts, skips, analytics_summary=None):
    """Filter duplicates, sensitive content, score and rank."""
    results = []
    relaxed = len(topics) < 10
    
    # Extract analytics data for dynamic boost
    best_hooks = []
    worst_topics = []
    median_views = 0
    if analytics_summary:
        best_hooks = [h[0] for h in analytics_summary.get("best_hooks", [])]
        worst_topics = [t[0] for t in analytics_summary.get("worst_topics", [])]
        median_views = analytics_summary.get("median_views", 0)
    
    for t in topics:
        title = (t.get("title") or "").strip()
        url = (t.get("url") or "").strip()
        source = (t.get("source") or "").strip().lower()
        desc = (t.get("description") or "").lower()
        if not title or not url or source not in SOURCES: continue
        tl = title.lower()
        # Women's football
        if any(kw in tl or kw in desc for kw in _WOMEN): continue
        # TV guides
        if any(kw in tl for kw in _TV_GUIDE): continue
        # Filter out live commentary pages (skysports.com/.../live/...)
        if '/live/' in url: continue
        # Sensitive content
        if any(kw in tl or kw in desc for kw in _SENSITIVE): continue
        # Dedup
        if url in posted_urls: continue
        threshold = 0.50 if relaxed else 0.35
        if is_similar(title, posted_ws, threshold): continue
        # Skip low-performing topic types from analytics
        tt = classify_topic_type(title)
        if tt in skips and not relaxed: continue
        # Score: base v17 + pipeline bonuses
        s = base_score_topic(t)
        if s == -1: continue  # excluded by keywords
        # Pipeline bonuses
        if t.get("wc_related") or t.get("wc_boost"): s += 40
        if t.get("transfer_related"): s += 10
        # ponytail: legacy topic boost multiplier removed — stale data inflated match_result 3x
        # Dynamic analytics boost (data-driven)
        if analytics_summary and median_views > 0:
            hook = _classify_hook(tl)
            if hook in best_hooks[:2]:
                s += 15
                log(f"   📈 Hook boost: {hook} +15 for '{title[:50]}'")
            
            # Penalize worst-performing topic types
            if tt in worst_topics:
                s -= 20
                log(f"   📉 Topic penalty: {tt} -20 for '{title[:50]}'")
        
        t["_score"] = s
        t["_topic_type"] = tt
        # Image fallback: fetch og:image for RSS topics without image
        if t.get("_needs_image_fallback") and t.get("url"):
            try:
                code, html = _http(t["url"])
                if code == 200:
                    fallback_img = extract_image(html)
                    if fallback_img:
                        t["image_url"] = fallback_img
                        log(f"   🖼️ Image fallback: {fallback_img[:60]}...")
            except: pass
        results.append(t)
    results.sort(key=lambda x: -x["_score"])
    # Cannibalization filter — skip lower-scored duplicate topics
    seen_sigs = set()
    deduped = []
    skip_words = {"the","a","an","in","on","at","to","for","of","and","or","but","is","was","just","not"}
    for t in results:
        words = set(t.get("title","").lower().split()) - skip_words
        sig = " ".join(sorted(words)[:4])
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        deduped.append(t)
    results = deduped
    # Source diversity cap: no single source > 50% of ranked pool
    if results:
        from collections import Counter
        max_per_source = max(1, len(results) // 2)
        source_count = Counter()
        capped = []
        for t in results:
            src = t.get("source", "")
            if source_count[src] < max_per_source:
                capped.append(t)
                source_count[src] += 1
        results = capped
    return results

# ── 3. EXTRACT ARTICLE ─────────────────────────────────────────────

def extract_article(raw_html):
    """Extract clean article text from HTML — only <p> tags from article body."""
    soup = BeautifulSoup(raw_html, 'html.parser')
    # Find article body container
    body = (soup.find('article')
            or soup.find('div', class_='sdc-article-body')
            or next((d for d in soup.find_all('div', class_=True)
                     if any(k in ' '.join(d.get('class',[])).lower()
                            for k in ['article-body','article_content','story-body','ArticleBody_article'])), None))
    if not body:
        text = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', raw_html, flags=re.DOTALL|re.I)
        return html_mod.unescape(re.sub(r'<[^>]+>', ' ', text))
    # Remove noise tags
    for tag in body.find_all(['nav','aside','footer','script','style','form']):
        tag.decompose()
    for div in body.find_all(['div','section'], class_=True):
        try:
            cls = ' '.join(div.get('class',[])).lower()
            if any(p in cls for p in ['ad-','advert','related','recommend','newsletter','subscribe','promo','sponsor',
                                       'caption','share','social','comment','byline','author','timestamp']):
                div.decompose()
        except (AttributeError, TypeError):
            continue
    # Extract only <p> tags — filter short/noise paragraphs
    paragraphs = []
    noise_re = re.compile(r'(?i)(follow\s+our|join\s+our|sign\s+up|subscribe|newsletter|facebook\s+page|amazon\s+prime|betting|odds|stream\s+live|add\s+goal\.com|preferred\s+source)')
    for p in body.find_all('p'):
        txt = p.get_text(separator=' ', strip=True)
        if len(txt) < 20: continue
        if noise_re.search(txt): continue
        paragraphs.append(txt)
    return ' '.join(paragraphs)

def extract_image(raw_html):
    """Extract best og:image from HTML."""
    for pat in [r'<meta\s+property="og:image"\s+content="([^"]+)"',
                r'<meta\s+content="([^"]+)"\s+property="og:image"',
                r'<meta\s+name="twitter:image"\s+content="([^"]+)"',
                r'<meta\s+content="([^"]+)"\s+name="twitter:image"']:
        m = re.search(pat, raw_html, re.I)
        if m:
            url = m.group(1)
            if "guim.co.uk" not in url:  # Guardian CDN blocks VPS
                return url
    return ""

def fetch_article(url):
    """Fetch article page, extract text + image."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=10, allow_redirects=True)
        if r.status_code != 200: return "", ""
        text = extract_article(r.text)
        return text.strip(), extract_image(r.text)
    except: return "", ""

# ── 4. LLM GENERATE ────────────────────────────────────────────────

# Grounding validator — kept from v7
_SKIP_WORDS = frozenset({
    'The','This','That','These','Those','A','An','When','Where','What','Which','While',
    'After','Before','During','Under','Over','Since','Until','Between','Among','Through',
    'Against','Into','Upon','Within','Without','From','With','About','Above','Across',
    'Along','Around','Behind','Below','Beneath','Beside','Beyond','Down','Inside','Near',
    'Off','Onto','Outside','Past','Round','Toward','Towards','In','But','And','Yet','So',
    'For','Nor','Once','Though','Although','Because','Whether','If','Unless','Whereas',
    'Even','Still','Just','Now','Then','Here','There','Only','Already','Never','Always',
    'Also','Perhaps','Both','Either','Neither','Each','Every','Most','Rather','Quite',
    'Very','Too','Enough','Almost','Again','Further','Instead','Indeed','Meanwhile',
    'Nevertheless','Otherwise','Therefore','Can','Could','Would','Should','Will','Must',
    'Make','Get','Take','Give','Find','Keep','Come','Go','Look','Think','Know','See',
    'Expect','Build','Stay','Reach','Kill','Remain','View','Image','Images','Photo',
    'Photos','Getty','Reuters','AP','AFP',
})
_STAGE_CANONICAL = {
    'last-16':'round_of_16','last 16':'round_of_16','round of 16':'round_of_16','r16':'round_of_16',
    'quarter-final':'quarter_final','quarter final':'quarter_final','semi-final':'semi_final',
    'semi final':'semi_final','final':'final','group stage':'group_stage',
}

def _extract_proper_nouns(text):
    names = re.findall(r'([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)', text)
    cleaned = []
    for n in names:
        words = n.split()
        if words[0] in _SKIP_WORDS and len(words) > 2:
            cleaned.append(' '.join(words[1:]))
        elif words[0] not in _SKIP_WORDS:
            cleaned.append(n)
    return set(n for n in cleaned if len(n) > 4)

def _extract_stages(text):
    tl = text.lower()
    return {c for v, c in _STAGE_CANONICAL.items() if re.search(r'\b'+re.escape(v)+r'\b', tl)}

def grounding_check(slides_text, article_text, article_names, article_stages):
    """Check for hallucinated names/stages not in article."""
    warnings = []
    for name in _extract_proper_nouns(slides_text):
        if name not in article_text and len(name) > 4:
            warnings.append(f"HALLUCINATED_NAME: '{name}'")
    for stage in _extract_stages(slides_text):
        if stage not in article_stages:
            warnings.append(f"HALLUCINATED_STAGE: '{stage}'")
    return warnings

def _count_sentences(text):
    return len([s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if len(s.strip()) > 5])

def generate_slides(article_text, url, hooks="", cta_pattern="", tone=""):
    """Call LLM to generate 6-slide thread. Returns parsed slides or None."""
    if not MISTRAL_KEY:
        log("❌ No MISTRAL_API_KEY — cannot generate")
        return None

    # Build dynamic sections
    extra = ""
    if hooks: extra += f"\n- PREFERRED HOOKS: {', '.join(hooks[:3])}"
    if cta_pattern: extra += f"\n- CTA PATTERN: {cta_pattern}"
    if tone: extra += f"\n- TONE: {tone}"

    system = f"""You are an elite Football Content Creator writing Threads carousels. Conversational, witty, deeply relatable to die-hard football fans. Use casual football slang/banter ("cooked", "benched", "baller", "tactical masterclass") to simplify complex jargon. Avoid dry, journalistic language.

6-slide narrative arc. Not a listicle. Not a recap. Tension → revelation → payoff.

[FORMAT]
- Exactly 6 slides
- Max 4 sentences per slide, but vary: 1-sentence slides are powerful — use them
- Prose only, no bullets/lists
- English (conversational, punchy, global)
- Use em-dash (—) for drama and emphasis. Example: "He scored 40 goals — nobody remembers his name."
- Short punchy sentences mixed with longer ones. Never same rhythm every slide.

SLIDE 1 — THE HOOK (1-2 sentences MAX)
Create a "Hot Take", "POV", or controversial/mind-blowing statement that stops the scroll instantly.
Pick ONE style: accusation, hot take, betrayal, verdict, contrast, number, scandal, statement, POV
Priority (context-dependent):
- Big names/events (WC, CL, big clubs): CURIOSITY GAP > CONTROVERSY > CONFLICT
- Niche/unknown topics (lower leagues, retired players, small nations): CONFLICT > CONTROVERSY > hot take
- NEVER use "nobody's talking about" unless the topic involves a name/event that 90% of fans would recognize

WINNING PATTERN (75K views proven):
"X just became the first Y to do Z after [specific stat] — and nobody's talking about [scandal/real reason]."
Key ingredients: (1) specific stat/number (2) "first ever" framing (3) "nobody's talking about" creates reply bait
WARNING: "nobody's talking about" ONLY works with big names (WC, CL, Premier League, etc). For niche topics, use direct conflict or hot take instead.
Hook must provoke REPLIES (opinions, debates) not just views. Questions like "Is this the worst decision?" drive 3x more comments than factual statements.

SLIDE 2 — THE CONTEXT (40-60 words)
Connect hook to the actual news. Explain current situation and why fans should care.

SLIDE 3 — THE CORE FACT/STAT (40-60 words)
Most shocking stat, transfer fee, or tactical change — broken down in simple terms. End on unresolved question.

SLIDE 4 — THE IMPACT (40-60 words)
How this affects the team's lineup, rival clubs, or the upcoming season. The ripple effect.

SLIDE 5 — THE VERDICT (30-50 words)
Sharp, definitive verdict or witty takeaway. NOT philosophical — cinematic. Leaves room for debate.

SLIDE 6 — THE CTA (30-40 words)
Casual, open-ended question that triggers heated debate. Not "What do you think?" — force opinion: "Is this the worst decision?", "Was he right?", "Can they survive this?". Do NOT add any URLs — the source URL is appended automatically.

[GROUNDING — STRICT]
ALL data, stats, transfer rumors: 100% accurate to the provided text. ZERO hallucination.
Football metaphors/fan banter allowed ONLY to simplify jargon or feel organic — never to invent facts.
Names/scores/dates/quotes: verbatim from article only. Never invent quotes or attribute unstated emotions.
REJECT only if article has zero usable facts.

[ANALYTICS OVERRIDES]
PREFERRED_HOOKS, CTA_PATTERN, TONE override style choice in Slide 1 and Slide 6 when present.

Output strict JSON, no markdown fences:
{{"slide_1":"","slide_2":"","slide_3":"","slide_4":"","slide_5":"","slide_6":""}}
{extra}"""



    user = f"ARTICLE: {article_text[:8000]}\nSOURCE: {url}"

    for attempt in range(1, 4):
        log(f"   LLM attempt {attempt}/3...")
        try:
            r = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
                json={"model":"mistral-large-latest","messages":[
                    {"role":"system","content":system},{"role":"user","content":user}],
                    "max_tokens":4000,"temperature":0.3,"stream":True},
                timeout=60, stream=True)

            if r.status_code != 200:
                log(f"   ❌ HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(2 + attempt)
                continue

            parts = []
            for line in r.iter_lines():
                if not line: continue
                line = line.decode("utf-8")
                if not line.startswith("data: ") or line[6:].strip() == "[DONE]": continue
                try:
                    chunk = json.loads(line[6:])
                    delta = chunk.get("choices",[{}])[0].get("delta",{})
                    if delta.get("content"): parts.append(delta["content"])
                except: continue

            content = "".join(parts).strip()
            if not content:
                log("   ❌ Empty response")
                continue

            # Extract JSON
            candidate = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
            candidate = re.sub(r"\s*```$", "", candidate)
            # Mustache braces (Mistral quirk)
            if candidate.startswith("{{"):
                candidate = candidate.replace("{{","{").replace("}}","}")
            # Trailing brace fix
            missing = candidate.count('{') - candidate.count('}')
            if missing > 0: candidate += '}' * missing

            try:
                data = json.JSONDecoder().raw_decode(candidate.lstrip())[0]
            except json.JSONDecodeError:
                log("   ❌ JSON parse failed")
                continue

            if "error" in data:
                log(f"   ⚠️ LLM rejected: {data.get('reason','')}")
                return None

            # Parse slides
            slides = []
            if "slides" in data and isinstance(data["slides"], list):
                for i, s in enumerate(data["slides"][:6]):
                    if isinstance(s, dict):
                        slides.append({"title":s.get("title",f"S{i+1}"), "content":(s.get("content") or "").strip()})
                    elif isinstance(s, str):
                        slides.append({"title":f"S{i+1}", "content":s.strip()})
            else:
                for i in range(1,7):
                    s = data.get(f"slide_{i}", {})
                    if isinstance(s, dict) and s.get("content","").strip():
                        slides.append({"title":s.get("title",f"S{i}"), "content":s["content"].strip()})
                    elif isinstance(s, str) and s.strip():
                        slides.append({"title":f"S{i}", "content":s.strip()})

            if len(slides) < 3:
                log(f"   ❌ Only {len(slides)} usable slides")
                continue

            # Reject slides that are too short (< 30 chars)
            slides = [s for s in slides if len(s["content"]) >= 30]
            if len(slides) < 3:
                log(f"   ❌ Only {len(slides)} slides after min-length filter")
                continue

            # Auto-trim: sentence count + char cap
            for i, s in enumerate(slides[:6]):
                n = _count_sentences(s["content"])
                mn, mx = SENTENCE_COUNTS.get(i+1, (2,4))
                if n > mx:
                    parts = re.split(r'(?<=[.!?])\s+', s["content"].strip())
                    trimmed = [p for p in parts if len(p.strip())>5][:mx]
                    s["content"] = " ".join(trimmed)
                if len(s["content"]) > MAX_CHARS:
                    txt = s["content"][:MAX_CHARS]
                    lp = max(txt.rfind(". "), txt.rfind("! "), txt.rfind("? "))
                    s["content"] = txt[:lp+1] if lp > 50 else txt.rstrip()+"..."

            # Strip Markdown formatting
            for s in slides:
                s["content"] = re.sub(r'\*\*(.+?)\*\*', r'\1', s["content"])
                s["content"] = re.sub(r'\*(.+?)\*', r'\1', s["content"])
                s["content"] = s["content"].replace("—"," - ").replace("–"," - ")
                s["content"] = re.sub(r'  +', ' ', s["content"])
                # Enforce blank line after every sentence
                s["content"] = re.sub(r'([.!?])(\s+)([A-Z"])', r'\1\n\n\3', s["content"])

            # Guarantee source URL on last slide (deduplicate if LLM already included it)
            last = slides[-1]["content"]
            url_base = url.split("?")[0].rstrip("/")  # normalize for fuzzy match
            if url_base not in last and url not in last:
                slides[-1]["content"] = last.rstrip() + "\n\n" + url
            elif last.count(url) > 1:
                # LLM repeated URL — remove duplicates
                slides[-1]["content"] = last.replace(url, "", last.count(url) - 1).strip()

            return slides

        except Exception as e:
            log(f"   ❌ LLM exception: {e}")
            continue

    log("❌ Failed after 3 attempts")
    return None

# ── 5. POST TO THREADS ─────────────────────────────────────────────

def load_threads_token():
    try:
        with open(f"{HOME}/.hermes/threads_token.json") as f:
            d = json.load(f)
        return d.get("access_token"), str(d.get("user_id",""))
    except: return None, None

def post_to_threads(slides, image_url=None):
    """Post slides as chained thread. Returns (root_id, permalink) or (None, None)."""
    token, user_id = load_threads_token()
    if not token or not user_id:
        log("❌ No Threads token")
        return None, None

    from threads_poster import ThreadsPoster
    poster = ThreadsPoster(access_token=token, user_id=user_id)

    parts = [s["content"] for s in slides]
    images = [image_url] + [None]*(len(parts)-1) if image_url else None

    try:
        results = poster.post_thread(parts, image_urls=images, stop_on_error=True)
        if not results:
            log("❌ No posts returned")
            return None, None
        root_id = results[0].post_id
        short_link = poster.get_permalink(root_id)
        permalink = short_link or f"https://www.threads.com/@parkthebus.football/post/{root_id}"
        log(f"   ✅ Posted {len(results)} slides, root={root_id}")
        return root_id, permalink
    except Exception as e:
        log(f"❌ Post failed: {e}")
        return None, None

# ── 6. TRACK ───────────────────────────────────────────────────────

def track_post(title, url, source, root_id, permalink):
    """Append to posted_topics.json."""
    try:
        with open(POSTED) as f:
            data = json.load(f)
    except: data = {"topics":[]}
    if "topics" not in data: data["topics"] = []
    data["topics"].append({
        "title": title, "url": url, "source": source,
        "post_id": root_id, "permalink": permalink,
        "posted_at": datetime.now(WIB).isoformat(),
    })
    # Keep last 200 entries
    data["topics"] = data["topics"][-200:]
    with open(POSTED, "w") as f:
        json.dump(data, f, indent=2)

# ── MAIN ────────────────────────────────────────────────────────────

def check_cooldown(minutes=15):
    """Skip if posted too recently."""
    try:
        with open(POSTED) as f:
            topics = json.load(f).get("topics", [])
        if not topics: return False
        recent = sorted(topics, key=lambda x: x.get("posted_at",""), reverse=True)[:1]
        posted = recent[0].get("posted_at","")
        if posted:
            dt = datetime.fromisoformat(posted)
            if (datetime.now(WIB) - dt).total_seconds() < minutes * 60:
                return True
    except: pass
    return False

def main():
    START = time.time()
    log("=== PRESSBOX MVP ===")

    # Cooldown check (skip dry-run)
    if not DRY_RUN and check_cooldown(15):
        print("⏸️ Skip — baru posting < 15 menit lalu.", flush=True)
        return

    # 0. Init Threads poster (for metrics)
    token, user_id = load_threads_token()
    poster = None
    if token and user_id:
        try:
            from threads_poster import ThreadsPoster
            poster = ThreadsPoster(access_token=token, user_id=user_id)
        except:
            pass

    # 0.5. Pull engagement metrics for old posts (>12h)
    pull_engagement(poster)

    # 0.6. Get analytics summary for scoring boost
    analytics_summary = get_analytics_summary()
    if analytics_summary:
        log(f"📊 Analytics: {analytics_summary['total_posts_with_metrics']} posts, "
            f"avg {analytics_summary['avg_views']:.0f} views, "
            f"best hook: {analytics_summary['best_hooks'][0][0] if analytics_summary['best_hooks'] else 'N/A'}")

    # 1. Scrape
    topics = scrape_all()
    if not topics:
        log("❌ No topics scraped")
        print("❌ Pipeline: no topics scraped", flush=True)
        sys.exit(1)

    # 2. Filter + Score
    posted_urls, posted_ws = load_posted()
    boosts, skips, hooks, cta_pattern, tone = load_analytics()
    ranked = filter_and_score(topics, posted_urls, posted_ws, boosts, skips, analytics_summary)
    if not ranked:
        log("❌ No topics after filter")
        print("❌ Pipeline: all topics filtered out", flush=True)
        sys.exit(1)

    best = ranked[0]
    if best["_score"] < 40:
        log(f"   ⏸️ Best score {best['_score']} < 40 threshold — skipping")
        print(f"⏸️ Skip — best topic score {best['_score']} below threshold", flush=True)
        sys.exit(0)
    log(f"   🏆 Best: {best['title']} (score={best['_score']}, type={best.get('_topic_type','')})")

    # 3. Fetch article — try top 3 topics
    url = best["url"]
    log(f"   Fetching: {url}")
    article_text, image_url = fetch_article(url)
    fetch_tries = 1
    while (not article_text or len(article_text) < 100) and fetch_tries < len(ranked[:3]):
        log(f"   ❌ Article too short on '{best['title']}' — trying next")
        best = ranked[fetch_tries]
        url = best["url"]
        log(f"   Fetching next: {url}")
        article_text, image_url = fetch_article(url)
        fetch_tries += 1
    if not article_text or len(article_text) < 100:
        log("❌ All top articles too short")
        print("❌ Pipeline: all articles too short", flush=True)
        sys.exit(1)
    log(f"   Article: {len(article_text)} chars, image: {'yes' if image_url else 'no'}")

    # 4. Generate slides
    t0 = time.time()
    slides = generate_slides(article_text, url, hooks, cta_pattern, tone)
    if not slides:
        print("❌ Pipeline: LLM generation failed", flush=True)
        sys.exit(1)
    llm_time = time.time() - t0

    # 5. Grounding check — block on hallucinated stages, warn on names
    slides_text = " ".join(s["content"] for s in slides)
    art_names = _extract_proper_nouns(article_text)
    art_stages = _extract_stages(article_text)
    warnings = grounding_check(slides_text, article_text, art_names, art_stages)
    hallucinated_stages = [w for w in warnings if "HALLUCINATED_STAGE" in w]
    hallucinated_names = [w for w in warnings if "HALLUCINATED_NAME" in w]
    if hallucinated_names:
        log(f"   ⚠️ Name warnings (soft): {'; '.join(hallucinated_names)}")
    if hallucinated_stages:
        log(f"   ❌ Stage hallucination: {'; '.join(hallucinated_stages)}")
        print(f"❌ Grounding: {'; '.join(hallucinated_stages)}", flush=True)
        sys.exit(1)

    # 6. DRY RUN or POST
    total = time.time() - START

    if DRY_RUN:
        log(f"🔍 DRY RUN — {best['title']} ({len(slides)} slides)")
        for i, s in enumerate(slides):
            print(f"\n--- Slide {i+1} ({s['title']}) ---\n{s['content']}")
        print(f"\n✅ Dry run done in {total:.1f}s (LLM: {llm_time:.1f}s)")
        return

    # Post
    root_id, permalink = post_to_threads(slides, image_url)
    if not root_id:
        print("❌ Pipeline: post failed", flush=True)
        sys.exit(1)

    # Track
    track_post(best["title"], url, best.get("source",""), root_id, permalink)

    log(f"✅ {best['title']} → {permalink}")
    log(f"⏱️ Total: {total:.1f}s (LLM: {llm_time:.1f}s)")

    # Summary report (stdout → delivered to Telegram topic 20467)
    score = best.get("_score", 0)
    hook_type = best.get("_hook_type", "unknown")
    src = best.get("source", "unknown")
    slide_count = len(slides)
    slide_preview = slides[0]["content"][:120] if slides else "N/A"
    import datetime
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7)))
    wib = now.strftime("%H:%M WIB, %d %b %Y")
    post_count = len(json.load(open(POSTED)).get("topics", []))
    print(f"""📰 **Pressbox MVP — Posted**
━━━━━━━━━━━━━━━━
**Title:** {best['title'][:100]}
**Source:** {src} | **Score:** {score} | **Hook:** {hook_type}
**Slides:** {slide_count}
**Hook preview:** {slide_preview}...
**Link:** {permalink}
━━━━━━━━━━━━━━━━
📊 Posts: {post_count} total | ⏱️ {total:.1f}s (LLM: {llm_time:.1f}s)
⏰ {wib}""", flush=True)

if __name__ == "__main__":
    main()
