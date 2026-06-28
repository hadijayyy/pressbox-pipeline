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
SOURCES = ["mirror", "skysports", "goal"]
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
            de = item.find('description')
            desc = re.sub(r'<[^>]+>', ' ', (de.text or "")).strip()[:500] if de is not None else ""
            desc = html_mod.unescape(desc)
            pe = item.find('pubDate')
            ts = None
            if pe is not None and pe.text:
                try: ts = parsedate_to_datetime(pe.text.strip()).timestamp()
                except: pass
            if ts and (time.time() - ts) > 21600: continue  # 6h freshness
            # Image from media:content
            img = ""
            for ns in ["http://search.yahoo.com/mrss/", "http://search.yahoo.com/mrss"]:
                for mc in item.findall(f'.//{{{ns}}}content'):
                    w = int(mc.get("width", 0))
                    if w > 0: img = mc.get("url", "")
            topics.append(dict(title=title, source=source, url=link, score=base_score,
                               description=desc, published_ts=ts, image_url=img))
    except: pass
    return topics

def scrape_mirror():
    """Mirror scraper."""
    topics = []
    try:
        code, text = _http("https://www.mirror.co.uk/sport/football/news/")
        if code != 200: return topics
        seen = set()
        for link in re.findall(r'href="(https?://www\.mirror\.co\.uk/sport/football/[^"]*-?\d+)"', text)[:12]:
            if link in seen or 'pageNumber' in link: continue
            seen.add(link)
            try:
                c2, t2 = _http(link, timeout=6)
                if c2 != 200: continue
                m = re.search(r'og:title[^>]*content="([^"]*)"', t2)
                if not m: continue
                title = html_mod.unescape(m.group(1))
                if title.lower().startswith(("the mirror", "mirror", "uk news")): continue
                dp = None
                dm = re.search(r'"datePublished"\s*:\s*"([^"]*)"', t2)
                if dm:
                    try: dp = datetime.fromisoformat(dm.group(1).replace("Z","+00:00")).timestamp()
                    except: pass
                if dp and (time.time() - dp) > 21600: continue
                od = re.search(r'og:description[^>]*content="([^"]*)"', t2)
                desc = html_mod.unescape(od.group(1)) if od else ""
                # og:image
                oi = re.search(r'og:image[^>]*content="([^"]*)"', t2)
                img = oi.group(1) if oi else ""
                topics.append(dict(title=title, source="mirror", url=link, score=10,
                                   description=desc[:500], published_ts=dp, image_url=img))
            except: pass
    except: pass
    return topics

def scrape_goal():
    """Goal.com scraper."""
    topics = []
    try:
        code, text = _http("https://www.goal.com/en", timeout=10)
        if code != 200: return topics
        soup = BeautifulSoup(text, 'html.parser')
        for art in soup.find_all('article')[:15]:
            try:
                h3 = art.find('h3')
                if not h3: continue
                title = h3.get_text(strip=True)
                if len(title) < 10: continue
                a = art.find('a', href=True)
                if not a: continue
                url = a['href']
                if url.startswith('//'): url = 'https:' + url
                elif not url.startswith('http'): url = 'https://www.goal.com' + url
                # Fetch article for og:image + timestamp
                c2, t2 = _http(url, timeout=10)
                if c2 != 200: continue
                oi = re.search(r'og:image[^>]*content="([^"]*)"', t2)
                img = oi.group(1) if oi else ""
                ts = None
                tm = re.search(r'"datePublished"\s*:\s*"([^"]*)"', t2)
                if tm:
                    try: ts = datetime.fromisoformat(tm.group(1).replace("Z","+00:00")).timestamp()
                    except: pass
                if ts and (time.time() - ts) > 21600: continue
                od = re.search(r'og:description[^>]*content="([^"]*)"', t2)
                desc = html_mod.unescape(od.group(1)) if od else ""
                topics.append(dict(title=title, source="goal", url=url, score=10,
                                   description=desc[:500], published_ts=ts, image_url=img))
            except: pass
    except: pass
    return topics

def scrape_all():
    """Scrape all sources in parallel."""
    log("Scraping 3 sources...")
    t0 = time.time()
    all_t = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {
            "mirror": ex.submit(scrape_mirror),
            "skysports": ex.submit(scrape_rss, "https://www.skysports.com/rss/11095", "skysports", 12),
            "goal": ex.submit(scrape_goal),
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
    """Load analytics feedback for topic boosts + skip list."""
    boosts, skips, hooks, cta, tone = {}, [], [], [], ""
    fb_path = f"{HOME}/.hermes/pressbox/analytics_feedback.json"
    rec_path = f"{HOME}/.hermes/pressbox/analytics_recommendations.json"
    try:
        with open(fb_path) as f:
            fb = json.load(f)
        boosts = fb.get("topic_boosts", {})
        skips = [s.get("pattern","") for s in fb.get("skip_topics",[])]
    except: pass
    try:
        with open(rec_path) as f:
            recs = json.load(f)
        gen = recs.get("analysis",{}).get("generate_tweaks",{})
        hooks = gen.get("preferred_hooks",[])
        cta = gen.get("cta_pattern","")
        tone = gen.get("tone_adjustment","")
    except: pass
    return boosts, skips, hooks, cta, tone

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

def filter_and_score(topics, posted_urls, posted_ws, boosts, skips):
    """Filter duplicates, sensitive content, score and rank."""
    results = []
    relaxed = len(topics) < 10
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
        # Analytics topic boost
        if tt in boosts:
            s = int(s * boosts[tt])
        t["_score"] = s
        t["_topic_type"] = tt
        results.append(t)
    results.sort(key=lambda x: -x["_score"])
    return results

# ── 3. EXTRACT ARTICLE ─────────────────────────────────────────────

def extract_article(raw_html):
    """Extract clean article text from HTML."""
    soup = BeautifulSoup(raw_html, 'html.parser')
    body = (soup.find('article')
            or soup.find('div', class_='sdc-article-body')
            or next((d for d in soup.find_all('div', class_=True)
                     if any(k in ' '.join(d.get('class',[])).lower()
                            for k in ['article-body','article_content','story-body'])), None))
    if not body:
        text = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', raw_html, flags=re.DOTALL|re.I)
        return html_mod.unescape(re.sub(r'<[^>]+>', ' ', text))
    for tag in body.find_all(['nav','aside','footer','script','style','form']):
        tag.decompose()
    for div in body.find_all(['div','section'], class_=True):
        try:
            cls = ' '.join(div.get('class',[])).lower()
            if any(p in cls for p in ['ad-','advert','related','recommend','newsletter','subscribe','promo','sponsor']):
                div.decompose()
        except (AttributeError, TypeError):
            continue
    return re.sub(r'\s+', ' ', body.get_text(separator=' ', strip=True))

def extract_image(raw_html):
    """Extract best og:image from HTML."""
    for pat in [r'<meta\s+property="og:image"\s+content="([^"]+)"',
                r'<meta\s+name="twitter:image"\s+content="([^"]+)"']:
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
        return extract_article(r.text), extract_image(r.text)
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

    system = f"""Football content strategist for Threads. Output EXACTLY 6-slide JSON.

[STRATEGY]
S1: HOOK (1-3 sentences, end with tension)
S2: WHAT (2-4 sentences, what happened)
S3: TENSION (2-4 sentences, conflict/stakes)
S4: HUMAN (1-4 sentences, one named person)
S5: UNRESOLVED (2-4 sentences, what's next)
S6: CTA (2-4 sentences, rhetorical question, last line: {url})

[HOOK PRIORITY]
(a) CONTROVERSY (b) CONFLICT (c) CURIOSITY GAP (d) PARADOX (e) SHOCK (f) NUMBERS
If no controversy, skip to (b) or (c). Never force drama from unrelated facts.
Must read like something you'd say to a friend at a pub.

[FORMAT — JSON only, no fences]
{{"slide_1":{{"title":"HOOK","content":"..."}},"slide_2":{{"title":"WHAT","content":"..."}},"slide_3":{{"title":"TENSION","content":"..."}},"slide_4":{{"title":"HUMAN","content":"..."}},"slide_5":{{"title":"UNRESOLVED","content":"..."}},"slide_6":{{"title":"CTA","content":"..."}}}}

[GROUNDING — STRICT]
Names, scores, dates, quotes: verbatim from article. No outside knowledge.
Missing detail = omit. Never infer feelings.

[REJECTION]
ONLY reject if article has NO usable facts. Articles with concrete facts are VALID.
Output: {{"error":"insufficient_source","reason":"..."}}

[STYLE]
Conversational English. No em-dash, hashtags, bullets, ALL CAPS, Markdown formatting.
Indonesian articles: keep names original, write in English.{extra}"""

    user = f"ARTICLE: {article_text[:6000]}\nSOURCE: {url}"

    for attempt in range(1, 4):
        log(f"   LLM attempt {attempt}/3...")
        try:
            r = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
                json={"model":"mistral-large-latest","messages":[
                    {"role":"system","content":system},{"role":"user","content":user}],
                    "max_tokens":8000,"temperature":0.5,"stream":True},
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

            # Guarantee source URL on last slide
            if url not in slides[-1]["content"]:
                slides[-1]["content"] = slides[-1]["content"].rstrip() + "\n\n" + url

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
        permalink = f"https://www.threads.com/@parkthebus.football/post/{root_id}"
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

def check_cooldown(minutes=30):
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
    if not DRY_RUN and check_cooldown(30):
        print("⏸️ Skip — baru posting < 30 menit lalu.", flush=True)
        return

    # 1. Scrape
    topics = scrape_all()
    if not topics:
        log("❌ No topics scraped")
        print("❌ Pipeline: no topics scraped", flush=True)
        sys.exit(1)

    # 2. Filter + Score
    posted_urls, posted_ws = load_posted()
    boosts, skips, hooks, cta_pattern, tone = load_analytics()
    ranked = filter_and_score(topics, posted_urls, posted_ws, boosts, skips)
    if not ranked:
        log("❌ No topics after filter")
        print("❌ Pipeline: all topics filtered out", flush=True)
        sys.exit(1)

    best = ranked[0]
    log(f"   🏆 Best: {best['title']} (score={best['_score']}, type={best.get('_topic_type','')})")

    # 3. Fetch article
    url = best["url"]
    log(f"   Fetching: {url}")
    article_text, image_url = fetch_article(url)
    if not article_text or len(article_text) < 100:
        log("❌ Article text too short")
        print("❌ Pipeline: article too short", flush=True)
        sys.exit(1)
    log(f"   Article: {len(article_text)} chars, image: {'yes' if image_url else 'no'}")

    # 4. Generate slides
    t0 = time.time()
    slides = generate_slides(article_text, url, hooks, cta_pattern, tone)
    if not slides:
        print("❌ Pipeline: LLM generation failed", flush=True)
        sys.exit(1)
    llm_time = time.time() - t0

    # 5. Grounding check
    slides_text = " ".join(s["content"] for s in slides)
    art_names = _extract_proper_nouns(article_text)
    art_stages = _extract_stages(article_text)
    warnings = grounding_check(slides_text, article_text, art_names, art_stages)
    if warnings:
        log(f"   ⚠️ Grounding warnings: {'; '.join(warnings)}")

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
    print(f"✅ {best['title'][:70]}", flush=True)
    print(f"   {permalink}", flush=True)

if __name__ == "__main__":
    main()
