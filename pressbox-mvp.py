#!/usr/local/bin/python3
"""Pressbox MVP — scrape, score, generate, post. One script, no staging."""
import subprocess as _sp, sys as _sys
for _p, _m in [("requests","requests"),("httpx","httpx"),("beautifulsoup4","bs4"),("python-dotenv","dotenv")]:
    try: __import__(_m)
    except ImportError: _sp.check_call([_sys.executable,"-m","pip","install","--quiet","--root-user-action=ignore",_p],stdout=_sp.DEVNULL,stderr=_sp.DEVNULL)

import html as html_mod, json, os, re, sys, time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

# Evaluator cache — persist URL→result so retried articles skip re-eval
_EVAL_CACHE = {}
_EVAL_CACHE_PATH = os.path.expanduser("~/.hermes/pressbox/eval_cache.json")
def _load_eval_cache():
    global _EVAL_CACHE
    try:
        with open(_EVAL_CACHE_PATH) as f:
            _EVAL_CACHE = json.load(f)
    except: _EVAL_CACHE = {}
def _save_eval_cache():
    try:
        os.makedirs(os.path.dirname(_EVAL_CACHE_PATH), exist_ok=True)
        with open(_EVAL_CACHE_PATH, 'w') as f:
            json.dump(_EVAL_CACHE, f)
    except: pass
_load_eval_cache()

# Engagement ring buffer — realtime per-(source, hook) performance tracking
_ENGAGEMENT_RING = {"posts": []}
_RING_PATH = os.path.expanduser("~/.hermes/pressbox/engagement_ring.json")

def _load_ring():
    global _ENGAGEMENT_RING
    try:
        with open(_RING_PATH) as f:
            _ENGAGEMENT_RING = json.load(f)
    except: _ENGAGEMENT_RING = {"posts": []}

def _save_ring():
    os.makedirs(os.path.dirname(_RING_PATH), exist_ok=True)
    with open(_RING_PATH, "w") as f:
        json.dump(_ENGAGEMENT_RING, f)

def _update_ring(topics):
    """Pull latest views into ring buffer after metrics refresh."""
    with_m = [t for t in topics if isinstance(t.get("views"), (int, float)) and t["views"] > 0]
    new_posts = []
    for t in with_m[-50:]:
        source = (t.get("source") or "").lower()
        title = (t.get("title") or "").lower()
        hook = _classify_hook(title)
        tt = classify_topic_type(title)
        new_posts.append({"source": source, "hook": hook, "topic_type": tt, "views": int(t["views"])})
    _ENGAGEMENT_RING["posts"] = new_posts[-50:]
    _save_ring()

def _query_ring(source, hook, topic_type):
    """Project adjustment based on median views for same (source, hook) combo."""
    posts = _ENGAGEMENT_RING.get("posts", [])
    if len(posts) < 5:
        return 0
    exact = sorted(p["views"] for p in posts if p["source"] == source and p["hook"] == hook)
    fallback = sorted(p["views"] for p in posts if p["source"] == source)
    key = exact if len(exact) >= 2 else (fallback if len(fallback) >= 2 else [])
    if not key:
        return 0
    med = key[len(key)//2]
    all_v = sorted(p["views"] for p in posts)
    overall = all_v[len(all_v)//2] or 1
    r = med / overall
    return 15 if r >= 1.5 else (5 if r >= 1.0 else (0 if r >= 0.5 else -10))

_load_ring()

from pressbox_common import WIB, HOME, POSTED, load_env, log, clean_words, is_similar, classify_topic_type
from pressbox_scoring import score_topic as base_score_topic
import requests
from bs4 import BeautifulSoup
# External hot topic detection
import google_trends

# ── Config ──────────────────────────────────────────────────────────
DRY_RUN = "--dry-run" in sys.argv
SOURCES = ["goal", "bbc", "fourfourtwo", "mirror"]
_SOURCE_PRIORITY = {"goal": 0, "bbc": 1, "fourfourtwo": 2, "mirror": 3}
ARTICLE_CACHE = f"{HOME}/.hermes/pressbox/article-cache.json"
SOURCE_FINGERPRINTS = f"{HOME}/.hermes/pressbox/source-fingerprints.json"
MAX_CHARS = 500  # Threads per-slide limit
SENTENCE_COUNTS = {1:(1,3), 2:(2,4), 3:(2,4), 4:(1,4), 5:(2,4), 6:(2,4)}
os.makedirs(f"{HOME}/.hermes/pressbox", exist_ok=True)

env = load_env()
MISTRAL_KEY = env.get("MISTRAL_API_KEY", "")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ── 1. SCRAPE ───────────────────────────────────────────────────────

def _http(url, timeout=8):
    """Simple HTTP GET with requests, fallback to httpx."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=True)
        return r.status_code, r.text
    except Exception:
        import httpx
        c = httpx.Client(headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True, verify=False)
        r = c.get(url)
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
            # Image: media:content > media:thumbnail > enclosure
            img = ""
            for ns in ["http://search.yahoo.com/mrss/", "http://search.yahoo.com/mrss"]:
                # media:content (SkySports, Goal)
                for mc in item.findall(f'.//{{{ns}}}content'):
                    w = int(mc.get("width", 0))
                    if w > 0: img = mc.get("url", "")
                # media:thumbnail (BBC — lower res but still useful)
                if not img:
                    for mt in item.findall(f'.//{{{ns}}}thumbnail'):
                        img = mt.get("url", "")
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

def _load_fingerprints():
    """Load source fingerprints (last-seen article title per source)."""
    try:
        with open(SOURCE_FINGERPRINTS) as f:
            return json.load(f)
    except:
        return {}

def _save_fingerprints(fps):
    """Save source fingerprints."""
    with open(SOURCE_FINGERPRINTS, "w") as f:
        json.dump(fps, f)

def scrape_all():
    """Scrape all sources in parallel. Skip sources with unchanged RSS."""
    log("Scraping 4 sources...")
    t0 = time.time()
    fingerprints = _load_fingerprints()
    new_fingerprints = {}
    all_t = []
    skipped = []

    def scrape_with_fingerprint(name, fn, *args):
        """Run scrape, check if feed changed. Returns (topics, changed)."""
        topics = fn(*args) if args else fn()
        if not topics:
            return [], False
        # First topic title = fingerprint (newest article)
        fp = topics[0].get("title", "")[:80]
        old_fp = fingerprints.get(name, "")
        if fp == old_fp:
            return [], False  # unchanged
        return topics, True

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {
            "goal": ex.submit(scrape_with_fingerprint, "goal", scrape_goal),
            "bbc": ex.submit(scrape_with_fingerprint, "bbc", scrape_rss, "https://feeds.bbci.co.uk/sport/football/rss.xml", "bbc", 10),
            "fourfourtwo": ex.submit(scrape_with_fingerprint, "fourfourtwo", scrape_rss, "https://www.fourfourtwo.com/rss", "fourfourtwo", 8),
            "mirror": ex.submit(scrape_with_fingerprint, "mirror", scrape_rss, "https://www.mirror.co.uk/sport/football/?service=rss", "mirror", 7),

        }
        for name, f in futs.items():
            try:
                topics, changed = f.result(timeout=15)
                if changed:
                    new_fingerprints[name] = topics[0].get("title", "")[:80]
                    log(f"   {name}: {len(topics)} topics (new)")
                    all_t.extend(topics)
                else:
                    skipped.append(name)
                    log(f"   {name}: unchanged (skipped)")
            except Exception as e:
                log(f"   ⚠️ {name}: {e}")

    # Merge fingerprints (keep old ones for skipped sources)
    fingerprints.update(new_fingerprints)
    _save_fingerprints(fingerprints)

    # If too few topics (<20) or all unchanged, force full scrape
    if len(all_t) < 20 and skipped:
        log("   ⚠️ All sources unchanged — forcing full scrape")
        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = {
                "goal": ex.submit(scrape_goal),
                "bbc": ex.submit(scrape_rss, "https://feeds.bbci.co.uk/sport/football/rss.xml", "bbc", 10),
                "fourfourtwo": ex.submit(scrape_rss, "https://www.fourfourtwo.com/rss", "fourfourtwo", 8),
                "mirror": ex.submit(scrape_rss, "https://www.mirror.co.uk/sport/football/?service=rss", "mirror", 7),

            }
            for name, f in futs.items():
                try:
                    r = f.result(timeout=15)
                    all_t.extend(r)
                except: pass

    log(f"   Total: {len(all_t)} in {time.time()-t0:.1f}s")
    return all_t

# ── 1.5 HOT TOPIC DETECTION ──────────────────────────────────────────

def _extract_entities(title):
    """Extract football entities (teams, players, managers) from title. Returns set of lowercase names."""
    import unicodedata
    def strip_accents(s):
        return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    tl = strip_accents(title.lower())
    found = set()
    from pressbox_scoring import BIG_TEAMS
    for entity in BIG_TEAMS:
        if strip_accents(entity) in tl:
            found.add(entity)
    return found

def detect_hot_topics(topics, window_hours=4):
    """Cluster topics by entity overlap. Returns dict: topic_url → hotness_score.

    Uses persistent article cache across runs for better 4h window coverage.
    """
    now = time.time()
    cutoff = now - (window_hours * 3600)

    # 1. Load persistent cache + merge current articles
    cached = []
    try:
        if os.path.exists(ARTICLE_CACHE):
            with open(ARTICLE_CACHE) as f:
                cached = json.load(f)
    except: pass

    # Merge: cache + current (dedup by URL)
    seen_urls = set()
    merged = []
    for t in cached + topics:
        url = t.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(t)

    # 2. Prune > 4h old + save cache
    fresh = []
    for t in merged:
        ts = t.get("published_ts") or now
        if ts >= cutoff:
            fresh.append(t)

    try:
        with open(ARTICLE_CACHE, "w") as f:
            json.dump(fresh, f)
    except: pass

    if len(fresh) < 2:
        return {}

    # 2. Extract entities per article
    article_entities = []
    for t in fresh:
        ents = _extract_entities(t.get("title", ""))
        article_entities.append((t, ents))

    # 3. Cluster by entity overlap (Union-Find style)
    n = len(article_entities)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Two articles in same cluster if they share 2+ entities
    for i in range(n):
        for j in range(i + 1, n):
            shared = article_entities[i][1] & article_entities[j][1]
            if len(shared) >= 2:
                union(i, j)

    # Also cluster if they share 1 entity AND title words are very similar (same story, different phrasing)
    skip_words = {"the","a","an","in","on","at","to","for","of","and","or","but","is","was","just","not","has","had","are","were","be","being","been","will","would","could","should","may","might","can","do","does","did","with","from","by","as","its","his","her","their","this","that","these","those","it"}
    def _title_sig(title):
        return set(title.lower().split()) - skip_words

    for i in range(n):
        for j in range(i + 1, n):
            shared_ents = article_entities[i][1] & article_entities[j][1]
            if len(shared_ents) >= 1:
                sig_i = _title_sig(article_entities[i][0].get("title", ""))
                sig_j = _title_sig(article_entities[j][0].get("title", ""))
                overlap = sig_i & sig_j
                # 4+ words in common → likely same story
                if len(overlap) >= 4:
                    union(i, j)

    # 4. Build clusters and score them
    from collections import defaultdict
    clusters = defaultdict(list)
    for i in range(n):
        root = find(i)
        clusters[root].append(article_entities[i])

    hotness = {}  # url → score
    for root, members in clusters.items():
        if len(members) < 2:
            continue  # single-source = not hot

        # Count unique sources
        count = len(members)

        # Source tier diversity bonus
        from pressbox_scoring import source_tier as _stier
        has_t1 = any(_stier(m[0].get("source","")) == 1 for m in members)
        tier_bonus = 1.5 if has_t1 else 1.0

        # Recency: articles from last 1h count more than 4h
        recency_sum = 0
        for m, _ in members:
            ts = m.get("published_ts") or now
            age_h = max(0.01, (now - ts) / 3600)
            recency_sum += 1.0 / age_h  # inverse age — fresh = high
        recency_avg = recency_sum / count

        # Final hotness: count × tier × recency
        # 3 sources from last 1h with Tier 1 = ~3 × 1.5 × 1.0 = 4.5
        # 2 sources from 3h ago, no T1     = ~2 × 1.0 × 0.33 = 0.66
        hot = count * tier_bonus * recency_avg

        # Collect cluster entities for topic relevance check
        cluster_entities = set()
        for m, ents in members:
            cluster_entities |= ents

        # Map to all members
        for m, _ in members:
            url = m.get("url", "")
            if url:
                hotness[url] = max(hotness.get(url, 0), hot)
                hotness[url + "_entities"] = list(cluster_entities)

    if hotness:
        hot_count = len(hotness)
        top_hot = sorted([(k,v) for k,v in hotness.items() if isinstance(v, (int,float))], key=lambda x: -x[1])[:3]
        log(f"🔥 Hot detection: {hot_count} articles in {sum(1 for c in clusters.values() if len(c)>=2)} clusters")
        for url, score in top_hot:
            # Find title for this URL
            title = next((t.get("title","")[:50] for t in topics if t.get("url") == url), "?")
            log(f"   🔥 {title}... (hotness={score:.1f})")

    # 5. Google Trends boost: match trending queries to article titles
    try:
        trends_data = google_trends.fetch_google_trends()
        if trends_data:
            football_keywords = {"football","soccer","world cup","premier league","champions league","la liga",
                "serie a","bundesliga","ligue 1","transfer","player","manager","goal","match","stadium"}
            matched = 0
            for t in trends_data:
                tq = t["query"].lower().strip()
                trend_score = t["score"]
                tq_words = set(tq.split())
                is_football = bool(tq_words & football_keywords) or any(
                    tq.find(k) >= 0 for k in ["vs ","fc ","utd ","afc ","cf "]
                )
                for topic in topics:
                    title = topic.get("title", "").lower()
                    url = topic.get("url", "")
                    if not url:
                        continue
                    if tq in title or any(tq.find(w) >= 0 for w in title.split() if len(w) > 3):
                        boost = min(8.0, trend_score / 200.0) if is_football else min(3.0, trend_score / 500.0)
                        if boost > 0.5:
                            hotness[url] = max(hotness.get(url, 0), boost)
                            matched += 1
                            log(f"   📈 Google Trends match: '{t['query']}' -> boost +{boost:.1f}")
            if matched:
                log(f"   📈 Google Trends: {matched}/{len(trends_data)} trends matched")
    except Exception as e:
        log(f"   ⚠️ Google Trends fetch failed: {e}")

    return hotness

# ── 2. FILTER + SCORE ──────────────────────────────────────────────

def load_posted():
    """Load posted URLs and title word-sets (72h window for similarity)."""
    from datetime import datetime, timedelta
    posted_urls, posted_ws = set(), []
    cutoff = datetime.now(WIB) - timedelta(hours=72)
    if os.path.exists(POSTED):
        try:
            with open(POSTED) as f:
                data = json.load(f)
            for t in (data.get("topics", []) if isinstance(data, dict) else data):
                u = (t.get("url") or "").strip()
                if u.startswith("http"): posted_urls.add(u)
                ti = (t.get("title") or "").strip()
                if not ti: continue
                # Only include recent posts for similarity check
                pa = t.get("posted_at", "")
                if pa:
                    try:
                        dt = datetime.fromisoformat(pa)
                        if dt.tzinfo is None: dt = dt.replace(tzinfo=WIB)
                        if dt < cutoff: continue  # too old, skip similarity
                    except: pass
                posted_ws.append(clean_words(ti))
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
        "avg_replies": avg([t.get("replies", 0) for t in with_metrics]),
        "best_hooks": [(h, avg(v)) for h, v in best_hooks[:3]],
        "best_topics": [(t, avg(v)) for t, v in best_topics[:5]],
        "best_sources": [(s, avg(v)) for s, v in best_sources],
        "worst_topics": [(t, avg(v)) for t, v in best_topics[-3:] if avg(v) < median_views * 0.5],
    }

    # Hotness A/B comparison — hot vs non-hot engagement
    hot_posts = [t for t in with_metrics if t.get("hotness_score", 0) > 0]
    cold_posts = [t for t in with_metrics if not t.get("hotness_score")]
    if hot_posts and cold_posts:
        hot_avg = avg([t.get("views", 0) for t in hot_posts])
        cold_avg = avg([t.get("views", 0) for t in cold_posts])
        summary["hot_avg_views"] = hot_avg
        summary["cold_avg_views"] = cold_avg
        summary["hot_count"] = len(hot_posts)
        summary["cold_count"] = len(cold_posts)
        if cold_avg > 0:
            ratio = hot_avg / cold_avg
            summary["hot_cold_ratio"] = round(ratio, 2)
            # Auto-boost: if hot posts get 50%+ more views, increase hot boost
            if ratio >= 1.5:
                summary["hot_boost_adjust"] = min(10, int((ratio - 1.0) * 10))
            elif ratio < 0.8:
                summary["hot_boost_adjust"] = max(-10, int((ratio - 1.0) * 10))
            else:
                summary["hot_boost_adjust"] = 0
            log(f"📊 Hot A/B: hot={hot_avg:.0f} avg ({len(hot_posts)} posts) vs cold={cold_avg:.0f} avg ({len(cold_posts)}) → ratio={ratio:.2f}")

    # Score auto-tuning: compute weight adjustments from engagement data
    if len(with_metrics) >= 20:
        summary['score_tuning'] = _compute_score_tuning(with_metrics, median_views)

    _update_ring(topics)  # feed latest views into engagement ring buffer
    return summary

def _compute_score_tuning(posts, median_views):
    """Analyze engagement data and compute scoring weight adjustments.
    
    Returns dict of component → multiplier (1.0 = no change, >1.0 = boost, <1.0 = penalize).
    Only activates after 20+ posts with metrics.
    """
    from pressbox_scoring import INCLUDE_KEYWORDS as SCORING_KEYWORDS, BIG_TEAMS
    import datetime
    
    high = [p for p in posts if p.get("views", 0) >= median_views * 1.3]
    low = [p for p in posts if p.get("views", 0) < median_views * 0.7]
    
    if len(high) < 3 or len(low) < 3:
        return {}
    
    tuning = {}
    
    # 1. Keyword effectiveness: which keywords appear more in high-performing posts?
    high_text = " ".join((p.get("title", "") or "").lower() for p in high)
    low_text = " ".join((p.get("title", "") or "").lower() for p in low)
    
    keyword_hits_high = sum(1 for kw in SCORING_KEYWORDS if kw in high_text)
    keyword_hits_low = sum(1 for kw in SCORING_KEYWORDS if kw in low_text)
    if keyword_hits_low > 0:
        kw_ratio = keyword_hits_high / keyword_hits_low
        tuning["keyword_multiplier"] = round(min(1.15, max(0.85, kw_ratio * 0.85)), 2)
    
    # 2. Audience reach effectiveness: do big team mentions correlate with views?
    team_hits_high = sum(1 for t in BIG_TEAMS if t in high_text)
    team_hits_low = sum(1 for t in BIG_TEAMS if t in low_text)
    if team_hits_low > 0:
        team_ratio = team_hits_high / team_hits_low
        tuning["audience_reach_multiplier"] = round(min(1.5, max(0.7, team_ratio)), 2)
    
    # 3. Drama effectiveness: do drama words correlate with views?
    drama_words = ["slam", "blast", "fury", "rage", "furious", "shock", "breaking", "exclusive", 
                   "revealed", "secret", "controversy", "row", "rift", "feud", "war"]
    drama_high = sum(1 for w in drama_words if w in high_text)
    drama_low = sum(1 for w in drama_words if w in low_text)
    if drama_low > 0:
        drama_ratio = drama_high / drama_low
        tuning["drama_multiplier"] = round(min(1.5, max(0.7, drama_ratio)), 2)
    
    # 4. Recency effectiveness: do newer posts perform better?
    now = time.time()
    high_ages = []
    low_ages = []
    for p in high:
        ts = p.get("published_ts") or p.get("posted_at", "")
        if isinstance(ts, str):
            try: ts = datetime.datetime.fromisoformat(ts).timestamp()
            except: ts = now
        high_ages.append((now - ts) / 3600)
    for p in low:
        ts = p.get("published_ts") or p.get("posted_at", "")
        if isinstance(ts, str):
            try: ts = datetime.datetime.fromisoformat(ts).timestamp()
            except: ts = now
        low_ages.append((now - ts) / 3600)
    if high_ages and low_ages:
        avg_high_age = sum(high_ages) / len(high_ages)
        avg_low_age = sum(low_ages) / len(low_ages)
        if avg_low_age > 0:
            recency_ratio = avg_low_age / avg_high_age  # higher = newer posts do better
            tuning["recency_multiplier"] = round(min(1.3, max(0.8, recency_ratio)), 2)
    
    # 5. First-ever effectiveness
    first_ever_high = sum(1 for p in high if "first" in (p.get("title", "") or "").lower())
    first_ever_low = sum(1 for p in low if "first" in (p.get("title", "") or "").lower())
    if first_ever_low > 0:
        fe_ratio = first_ever_high / first_ever_low
        tuning["first_ever_multiplier"] = round(min(1.5, max(0.7, fe_ratio)), 2)
    
    # 6. Human interest effectiveness: do HI posts perform better?
    hi_keywords = ["visa", "denied entry", "refused entry", "family", "mother", "father",
                   "tears", "cried", "emotional", "heartbreaking", "sacrifice", "payout",
                   "compensation", "immigration", "unfair", "injustice", "disgrace",
                   "fee", "cost", "price tag", "human cost", "barred from", "banned from"]
    hi_high = sum(1 for p in high if any(kw in (p.get("title", "") or "").lower() for kw in hi_keywords))
    hi_low = sum(1 for p in low if any(kw in (p.get("title", "") or "").lower() for kw in hi_keywords))
    if hi_low > 0:
        hi_ratio = hi_high / hi_low
        tuning["human_interest_multiplier"] = round(min(1.5, max(0.7, hi_ratio)), 2)
    elif hi_high > 0:
        tuning["human_interest_multiplier"] = 1.3  # HI posts in high but none in low = boost
    
    if tuning:
        # Save tuning to file for persistence
        tuning_file = f"{HOME}/.hermes/pressbox/score-tuning.json"
        tuning_data = {
            "computed_at": datetime.datetime.now().isoformat(),
            "posts_analyzed": len(posts),
            "median_views": median_views,
            "high_posts": len(high),
            "low_posts": len(low),
            "weights": tuning
        }
        try:
            with open(tuning_file, "w") as f:
                json.dump(tuning_data, f, indent=2)
        except: pass
        log(f"🎯 Score tuning: {tuning} (from {len(posts)} posts, median={median_views:.0f})")
    
    return tuning

# Sensitive content filter — use * as wildcard to catch variations
_SENSITIVE_EXACT = [
    "breasts","boobs","topless","nude","naked","wardrobe malfunction",
    "rape","sexual assault","pedophilia","child abuse",
    "charged with","convicted of","guilty of","domestic violence",
    "racist","racism","racial abuse","hate crime","antisemitic","islamophobia",
    "genocide","ethnic cleansing","terrorism",
    "falklands","malvinas",
    "soldiers died","soldiers killed","troops deployed",
    # exact terms (was wildcard — false positive on 'depth')
    "death","dead","deadly","kill","killed","killing","kills",
]
_SENSITIVE_WILDCARD = [
    "m*rd*r","st*bb*ng","b*mb*ng","terr*rist","sl*ying","exec*ting",
    # removed: de*th (matched 'depth'), k*ll (matched 'will'),
    # sh*ting (matched 'shooting' — football term)
]

import fnmatch as _fnmatch
def _match_sensitive(text):
    tl = text.lower()
    for kw in _SENSITIVE_EXACT:
        if kw in tl: return True
    for pat in _SENSITIVE_WILDCARD:
        if _fnmatch.fnmatch(tl, f"*{pat}*"): return True
    return False
_TV_GUIDE = ["tv channel","live stream","kick-off time","kickoff time",
             "how to watch","where to watch","what channel","start time","stream online"]
_COMMERCIAL = ["snap up","buy now","deal","discount","shop","price drop","sale","coupon","voucher",
               "bargain","save £","save $","off rrp","% off","for £","for $","amazon","ebay",
               "where to buy","get yours","order now","delivery","free shipping","stock up"]
_WOMEN = ["women","women's","womens","female","lionaesses","nwsl","wsl"]
# Post-WC garbage topics — low-value content that floods feed after major tournament ends
_POSTWC_GARBAGE = [
    # Prediction / preview (engagement trap — no real story)
    "prediction", "who will win", "match preview", "preview:",
    # Referee articles (niche, low engagement)
    "who is the referee", "referee for", "ref confirmed", "referee confirmed",
    # Kick-off / TV guide
    "what time does", "what time is", "kick-off time", "kickoff time",
    # FAQ-style questions
    "can you get 20", "do players miss", "quiz", "episode",
    # Dead rubber formats
    "player ratings", "how england could line up", "5 things",
    # Kits / merchandise (niche)
    "kit", "jersey", "boots",
    # Live/rolling blogs (low effort aggregator)
    "live", "updates",
    # Post-tournament flood (WC/major tournament aftermath)
    "farewell", "legacy", "what next for",
]


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

def filter_and_score(topics, posted_urls, posted_ws, boosts, skips, analytics_summary=None, hotness=None):
    """Filter duplicates, sensitive content, score and rank."""
    results = []
    relaxed = len(topics) < 10
    hotness = hotness or {}
    
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
        # Commercial/shopping articles — not football news
        if any(kw in tl for kw in _COMMERCIAL): continue
        # Filter out live commentary/live-blog pages
        if '/live/' in url or '/live-blog/' in url or '/quiz/' in url: continue
        # Post-WC garbage filter — skip low-value content that kills engagement
        if any(kw in tl for kw in _POSTWC_GARBAGE):
            log(f"   🗑️ Post-WC garbage: skipped '{title[:60]}'")
            continue
        # Sensitive content
        if _match_sensitive(tl) or _match_sensitive(desc): continue
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
        
        # Score auto-tuning: apply learned multipliers
        tuning = analytics_summary.get("score_tuning", {}) if analytics_summary else {}
        if tuning:
            # Boost/penalize based on what actually gets views
            kw_mult = tuning.get("keyword_multiplier", 1.0)
            if kw_mult != 1.0:
                keyword_bonus = min(s * 0.3, 15)  # cap adjustment to 15 pts
                s = int(s + keyword_bonus * (kw_mult - 1.0))
            audience_mult = tuning.get("audience_reach_multiplier", 1.0)
            if audience_mult != 1.0 and t.get("wc_boost"):
                s = int(s + 10 * (audience_mult - 1.0))
            # Human interest boost from auto-tuning
            hi_mult = tuning.get("human_interest_multiplier", 1.0)
            if hi_mult != 1.0:
                hi_keywords = ["visa", "denied entry", "family", "mother", "tears", "emotional",
                               "heartbreaking", "payout", "immigration", "unfair", "injustice",
                               "fee", "cost", "price tag", "human cost", "barred from"]
                if any(kw in tl for kw in hi_keywords):
                    s = int(s + 10 * (hi_mult - 1.0))
                    log(f"   💔 Human interest boost: ×{hi_mult} for '{title[:50]}'")
        # Pipeline bonuses
        # wc_related: +40 only if title has football context, +10 if just mentions team
        _wc_context = ["match","beat","win","loss","draw","score","goal","goals",
                       "qualify","eliminate","starter","lineup","injury","injured",
                       "transfer","sign","fee","contract","manager","sack","appointed",
                       "red card","yellow card","penalty","var","offside","suspended",
                       "captain","debut","hat-trick","brace","comeback","upset"]
        if t.get("wc_related") or t.get("wc_boost"):
            if any(kw in tl for kw in _wc_context):
                s += 40
            else:
                s += 10  # just mentions team name, not football context
                log(f"   ⚠️ wc_related reduced: +10 (no football context) for '{title[:50]}'")
        if t.get("transfer_related"): s += 15
        # Controversy/drama topic type bonus — proven viral format
        if tt == "controversy" or tt == "fifa_political" or tt == "manager_sack":
            s += 15
            log(f"   📈 Controversy boost: +15 for '{title[:50]}'")
        # Penalty for generic content (no topic type = low engagement)
        if tt and tt == "other":
            s -= 10
        # Niche topic penalty — low engagement content that happens to mention big teams
        _niche_kw = ["kit launch","kit reveal","jersey","boots","pink boots","kit deal",
                     "boot deal","stadium rules","ticket prices","travel guide",
                     "how to watch","tv channel","broadcast","kit manufacturer","shirt sponsor"]
        if any(kw in tl for kw in _niche_kw):
            s -= 30
            log(f"   📉 Niche topic: -30 for '{title[:50]}'")
        # ponytail: legacy topic boost multiplier removed — stale data inflated match_result 3x
        # Dynamic analytics boost (data-driven)
        hook = _classify_hook(tl)
        if analytics_summary and median_views > 0:
            if hook in best_hooks[:2]:
                hook_bonus = 20 if hook == "conflict" else 15  # conflict 4x better in data
                s += hook_bonus
                log(f"   📈 Hook boost: {hook} +{hook_bonus} for '{title[:50]}'")
            
            # Penalize worst-performing topic types
            if tt in worst_topics:
                s -= 20
                log(f"   📉 Topic penalty: {tt} -20 for '{title[:50]}'")

        # Realtime engagement ring: adjust by (source, hook) past performance
        ring_adjust = _query_ring(source, hook, tt)
        if ring_adjust:
            s += ring_adjust
            log(f"   📊 Ring: {source}/{hook} → {ring_adjust:+d} for '{title[:50]}'")

        # Hot topic boost (multi-source coverage = viral)
        # Skip hot boost for niche topics — they ride trending entity clusters without being newsworthy
        _is_niche = any(kw in tl for kw in _niche_kw)
        hot = hotness.get(url, 0)
        # Topic relevance: title must be ABOUT the entity (in first half), not just mention it
        _hot_relevant = True
        if hot >= 1.5:
            cluster_ents = hotness.get(url + "_entities", [])
            if cluster_ents:
                first_half = tl[:len(tl)//2]
                _hot_relevant = any(e.lower() in first_half for e in cluster_ents)
                if not _hot_relevant:
                    log(f"   ⚠️ Hot boost skipped: entity not in title first half for '{title[:50]}'")
        hot_adjust = analytics_summary.get("hot_boost_adjust", 0) if analytics_summary else 0
        # Peak-hour boost: hot stories get extra boost during high-engagement hours
        import datetime
        hour = datetime.datetime.now().hour
        peak_hours = {10, 11, 12, 17, 18, 19, 20, 21}  # WIB peak engagement windows
        peak_boost = 10 if (hour in peak_hours and hot >= 1.5) else 0
        # Post-WC retune: lower thresholds (fewer duplicate sources per story)
        if hot >= 2.0 and not _is_niche and _hot_relevant:
            boost = 25 + hot_adjust + peak_boost
            s += boost
            log(f"   🔥 Hot boost: +{boost} for '{title[:50]}' (hotness={hot:.1f}, adjust={hot_adjust:+d}, peak={hour in peak_hours})")
        elif hot >= 1.0 and not _is_niche and _hot_relevant:
            boost = 15 + hot_adjust + peak_boost
            s += boost
            log(f"   🔥 Warm boost: +{boost} for '{title[:50]}' (hotness={hot:.1f}, adjust={hot_adjust:+d}, peak={hour in peak_hours})")

        # Soft cap: above 100, diminishing returns (prevents runaway scores)
        if s > 100:
            s = int(100 + (s - 100) * 0.3)
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
    results.sort(key=lambda x: (-x["_score"], _SOURCE_PRIORITY.get(x.get("source", ""), 99)))
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
    """Extract best og:image from HTML, upscale BBC images."""
    for pat in [r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
                r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"',
                r'<meta[^>]+name="twitter:image"[^>]+content="([^"]+)"',
                r'<meta[^>]+content="([^"]+)"[^>]+name="twitter:image"']:
        m = re.search(pat, raw_html, re.I)
        if m:
            url = m.group(1)
            if "guim.co.uk" not in url:  # Guardian CDN blocks VPS
                # BBC: upscale low-res (480/624) → 1024px, keep high-res as-is
                if "ichef.bbci.co.uk" in url:
                    w = re.search(r'/(\d{3,4})/', url)
                    if w and int(w.group(1)) < 1024:
                        url = re.sub(r'/\d{3,4}/', '/1024/', url)
                return url
    return ""

def _load_article_text_cache():
    """Load cached article texts (URL → text) from article-cache.json."""
    try:
        with open(ARTICLE_CACHE) as f:
            cache = json.load(f)
        if isinstance(cache, dict):
            return {url: (d.get("text", ""), d.get("image", "")) for url, d in cache.items() if isinstance(d, dict) and d.get("text")}
        return {a["url"]: (a.get("text", ""), a.get("cached_image", "")) for a in cache if a.get("text")}
    except:
        return {}

def _save_article_text_to_cache(url, text, image_url=""):
    """Store fetched article text in cache for reuse."""
    try:
        with open(ARTICLE_CACHE) as f:
            cache = json.load(f)
        if url in cache:
            cache[url]["text"] = text[:5000]
            if image_url:
                cache[url]["image"] = image_url
        with open(ARTICLE_CACHE, "w") as f:
            json.dump(cache, f)
    except: pass

def fetch_article(url):
    """Fetch article page, extract text + image. Checks cache first.
    Always returns og:image (high-res) when available, not RSS thumbnail."""
    # Check cache
    text_cache = _load_article_text_cache()
    if url in text_cache and len(text_cache[url][0]) > 100:
        cached_text, cached_img = text_cache[url]
        log(f"   📦 Cached article: {url[:60]}")
        if cached_img:
            log(f"   🖼️ Cached og:image: {cached_img[:60]}")
        return cached_text, cached_img
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=10, allow_redirects=True)
        if r.status_code != 200: return "", ""
        text = extract_article(r.text).strip()
        image = extract_image(r.text)
        # Store in cache for future retries
        if text and len(text) > 100:
            _save_article_text_to_cache(url, text, image)
        return text, image
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

def evaluator_check(slides, article_text, url):
    """Independent evaluator — skeptical review before post.
    Generator says 'looks done'; evaluator says 'actually right'.
    Returns (decision, reasons): decision is APPROVE/REVISE/REJECT.
    """
    if not MISTRAL_KEY:
        return "APPROVE", ["no API key — skip eval"]

    slides_text = "\n\n".join(
        f"[Slide {i+1}: {s.get('title','')}]\n{s['content']}"
        for i, s in enumerate(slides)
    )
    # Truncate article to save tokens
    art_short = article_text[:3000]

    system = (
        "You are a skeptical editor reviewing social media slides BEFORE publication. "
        "Your job is to find problems, not praise. Be harsh. Look for:\n"
        "1. FACTUAL ERRORS: claims not supported by the article\n"
        "2. HALLUCINATION: invented stats, names, quotes, transfer fees\n"
        "3. SPECULATIVE EXTRAPOLATION: article mentions altitude but slide says 'players will gasp' — that's not in the article\n"
        "4. OVERSIZED PARAPHRASE: article says 'called for changes' but slide says 'told to drop X' — that's escalation\n"
        "5. PARTIAL LISTS: article mentions 5 players but slide shows only 3 as 'the lineup' — missing players = misleading\n"
        "6. TONE ISSUES: clickbait that damages credibility, insensitive content\n"
        "7. QUALITY: grammar errors, incoherent flow, too many slides\n"
        "8. MISLEADING: headline says X but article says Y\n"
        "9. NO STANCE: slide only reports (\"X said Y\") without any take, analysis, or opinion. Every slide needs a stance.\n\n"
        "RULE: For each slide, can you point to the EXACT sentence in the article that supports every claim? "
        "If a claim requires inference beyond the literal text, flag it.\n\n"
        "Respond in EXACTLY this JSON format:\n"
        '{"decision": "APPROVE|REVISE|REJECT", "reasons": ["reason1", "reason2"]}\n'
        "APPROVE = post as-is. REVISE = has issues but fixable. REJECT = do not post."
    )
    user = (
        f"ARTICLE (source):\n{art_short}\n\n"
        f"SLIDES (to review):\n{slides_text}\n\n"
        f"Source URL: {url}\n\n"
        "Review these slides. Be skeptical. Find problems."
    )

    try:
        r = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json={"model": "mistral-small-latest", "messages": [
                {"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": 800, "temperature": 0.1},
            timeout=30)
        if r.status_code != 200:
            return "APPROVE", [f"evaluator HTTP {r.status_code}"]
        content = r.json()["choices"][0]["message"]["content"].strip()
        # Parse JSON response
        candidate = re.sub(r"^```(?:json)?\s*", "", content)
        candidate = re.sub(r"\s*```$", "", candidate)
        data = json.loads(candidate, strict=False)
        decision = data.get("decision", "APPROVE").upper()
        reasons = data.get("reasons", [])
        if decision not in ("APPROVE", "REVISE", "REJECT"):
            decision = "APPROVE"
        return decision, reasons
    except Exception as e:
        return "APPROVE", [f"evaluator error: {e}"]

def _count_sentences(text):
    return len([s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if len(s.strip()) > 5])

def _select_viral_pattern(topic, article_text):
    """Select pattern: A (rule-break), C (detail/emotion), D (commentary),
    E (pressure cooker), F (behind-the-scenes).
    Patterns E+F cover content that ranked highest in real performance data."""
    title = (topic.get("title") or "").lower()
    text = article_text.lower()[:2000]
    combined = title + " " + text
    import re as _re
    
    # Pattern A signals (Rule-Break): authority violates own rules, scandal
    rule_break_words = ["rule", "regulation", "tradition", "golden rule", "broke its own",
                        "violated", "waived", "ignored its own", "bent the rules",
                        "loophole", "exception", "exemption", "contradicts", "fast-tracked",
                        "changed its own", "greenlit", "special treatment"]
    scandal_words = ["scandal", "controversy", "behind the scenes", "secret", "real reason",
                     "nobody talks", "ugly truth", "shocking", "betray", "refuse", "clash",
                     "furious", "rage", "slam", "blast", "row", "rift", "feud"]
    scandal_score = sum(2 for w in rule_break_words if w in combined) + \
                    sum(1 for w in scandal_words if w in combined)
    
    # Pattern D signals: commentary/opinion — someone famous said something
    commentary_words = ["slam", "criticise", "criticize", "attack", "comment", "opinion",
                        "says", "claims", "blasts", "hits out", "tells", "reveals",
                        "defends", "backtracks", "apologises", "apologizes", "admits",
                        "reacts", "hits back", "fires back", "calls out"]
    commentary_score = sum(1 for w in commentary_words if w in combined)
    
    # Pattern C signals: specific numbers, financial amounts, human interest
    detail_words = ["£", "$", "fee", "cost", "price", "pay", "million", "thousand",
                    "visa", "banned", "denied", "blocked", "refused", "mother", "father",
                    "family", "cry", "tears", "heart", "sacrifice", "hero", "legend"]
    detail_score = sum(1 for w in detail_words if w in combined)
    
    has_specific_number = bool(_re.search(r'\d+[\d,.]*\s*(?:£|$|million|thousand|k\b)', combined))
    if has_specific_number:
        detail_score += 3  # Strong signal for Pattern C
    
    # Pattern E signals (Pressure Cooker): player/manager under pressure, reactions, mind games
    # Based on top performers: "Tuchel NOT happy", "Haaland fumes", "Kane speaks out"
    pressure_words = ["not happy", "fumes", "fuming", "under fire", "under pressure", "pressure",
                      "speaks out", "breaks silence", "addresses", "responds to", "reacts",
                      "defiant", "fires back", "warning", "not impressed", "frustrated",
                      "frustration", "furious", "rage", "disappointed", "disappointment",
                      "ultimatum", "demands", "demand", "refuse", "refuses", "refused",
                      "considering future", "wants out", "wants to leave", "future uncertain",
                      "talks underway", "deal close", "agree", "agreed", "rejected", "reject"]
    # Tension context — headlines with "NOT happy/under fire/fumes" = strong E signal
    tension_words = ["fume", "furious", "not happy", "under fire", "speaks out", "breaks silence"]
    tension_match = sum(2 for w in tension_words if w in title)
    pressure_score = sum(1 for w in pressure_words if w in combined) + tension_match
    
    # Pattern F signals (Behind-the-Scenes): logistics, admin, referees, off-field drama
    # Based on top performers: "hotel change", "VAR decision", "air miles", "ref questions"
    bts_words = ["hotel", "travel", "stadium", "weather", "referee", "ref", "var",
                 "injury", "squad", "lineup", "starting xi", "selection", "tactics",
                 "formation", "change", "changed", "decision", "decided", "logistics",
                 "fifa", "uefa", "fa", "premier league", "administration", "rule",
                 "investigation", "probe", "banned", "ban", "suspended", "suspension",
                 "fine", "fined", "agent", "contract", "release clause", "option",
                 "medical", "fitness", "condition", "training"]
    bts_score = sum(1 for w in bts_words if w in combined)
    # Strong F signal: logistics/admin focus in headline
    logistics_title = ["why", "how", "what next", "reasons", "behind", "inside",
                       "secret", "revealed", "explained"]
    had_bts_title = sum(1 for w in logistics_title if w in title) >= 2
    if had_bts_title:
        bts_score += 2
    
    # Grounding check: does body text ACTUALLY contain rule words?
    body_rule_score = sum(2 for w in rule_break_words if w in text)
    
    # Priority: E/F first when they score high (they outperform A in real data)
    # Pattern E: Pressure Cooker (634K, 601K, 403K, 319K views in real data)
    if pressure_score >= 4 and pressure_score > max(scandal_score, detail_score, commentary_score, bts_score):
        return "e"
    
    # Pattern F: Behind-the-Scenes (536K, 487K, 226K views in real data)
    if bts_score >= 5:
        # Logistics/admin story that's not a scandal
        if scandal_score < 3:
            return "f"
    
    # Pattern D: commentary article with no actual rule violation in body
    if commentary_score >= 3 and body_rule_score < 2 and scandal_score < 3:
        if detail_score >= commentary_score and detail_score >= scandal_score:
            return "c"
        return "d"
    
    # Pattern E lower threshold: strong tension even if mixed
    if pressure_score >= 3 and pressure_score >= max(scandal_score, detail_score, commentary_score, bts_score):
        return "e"
    
    # Pattern F lower threshold: strong logistics signal
    if bts_score >= 4 and bts_score >= max(scandal_score, pressure_score):
        return "f"
    
    # Decision: rule-break wins unless detail/emotion story clearly stronger
    if scandal_score >= max(detail_score, commentary_score, pressure_score, bts_score) or (scandal_score >= 2 and scandal_score > detail_score - 2):
        return "a"
    else:
        return "c"

def _build_reference_data():
    """Build factual reference data injected into every generation prompt.
    Includes current date, WC timeline, and common player ages.
    Returns string to prepend to the user message."""
    from datetime import date
    today = date.today()

    players = [
        ("Harry Kane", 7, 28, 1993),
        ("Lionel Messi", 6, 24, 1987),
        ("Kylian Mbappe", 12, 20, 1998),
        ("Erling Haaland", 7, 21, 2000),
        ("Jude Bellingham", 6, 29, 2003),
        ("Bukayo Saka", 9, 5, 2001),
        ("Mohamed Salah", 6, 15, 1992),
        ("Lamine Yamal", 7, 13, 2007),
        ("Vinicius Jr", 7, 12, 2000),
        ("Rodri", 6, 22, 1996),
        ("Florian Wirtz", 5, 3, 2003),
        # Extras (added Jul 2026)
        ("Phil Foden", 5, 28, 2000),
        ("Cole Palmer", 5, 6, 2002),
        ("Jamal Musiala", 2, 26, 2003),
        ("Joshua Kimmich", 2, 8, 1995),
        ("Declan Rice", 1, 14, 1999),
        ("Martin Odegaard", 12, 17, 1998),
        ("Alessandro Bastoni", 4, 13, 1999),
        ("Viktor Gyokeres", 2, 4, 1998),
        ("Victor Osimhen", 12, 29, 1998),
        ("Khvicha Kvaratskhelia", 2, 12, 2001),
        ("Pau Cubarsi", 1, 22, 2007),
        ("Nico Williams", 7, 12, 2002),
        ("Federico Valverde", 7, 22, 1998),
        ("Gavi", 8, 5, 2004),
        ("Pedri", 11, 25, 2002),
        # Batch 3 (no-risk, Jul 2026)
        ("Kai Havertz", 6, 11, 1999),
        ("Gabriel Jesus", 4, 3, 1997),
        ("Ollie Watkins", 12, 30, 1995),
        ("Bruno Fernandes", 9, 8, 1994),
        ("Dominik Szoboszlai", 10, 25, 2000),
        ("Josko Gvardiol", 1, 23, 2002),
        ("William Saliba", 3, 24, 2001),
        ("Marcus Rashford", 10, 31, 1997),
        ("Trent Alexander-Arnold", 10, 7, 1998),
        ("Cristiano Ronaldo", 2, 5, 1985),
    ]

    wc_years = 2030 - today.year
    lines = [f"## FACTUAL REFERENCE DATA (ground truth for all math)"]
    lines.append(f"Current date: {today.strftime('%A, %B %d, %Y')}")
    lines.append(f"2030 FIFA World Cup: June-July 2030 → ~{wc_years} years from now")
    lines.append("")
    lines.append(f"Player ages (mid-{today.year}):")
    _months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    for name, m, d, y in players:
        age = today.year - y
        if (today.month, today.day) < (m, d):
            age -= 1
        lines.append(f"- {name}: {age} (born {d} {_months[m-1]} {y})")
    lines.append("")
    lines.append("2030 World Cup ages (use these for future-age questions):")
    for name, m, d, y in players:
        age_2030 = 2030 - y
        if (6, m) < (m, d):  # World Cup is June-July, check birthday falls after
            age_2030 -= 1
        lines.append(f"- {name}: ~{age_2030} at 2030 World Cup")
    lines.append("")
    lines.append("RULES for numbers in your output:")
    lines.append("- Every number MUST come from the article OR this reference data.")
    lines.append("- NEVER calculate ages, future dates, or fees not listed above.")
    lines.append("- When in doubt: omit the number. Wrong is worse than vague.")
    return "\n".join(lines)


def number_grounding_check(slides_text, article_text, ref_text):
    """Check numerical claims in slides are grounded in article or reference data.
    Returns list of warning strings (empty = clean).
    Uses article as primary source, reference data as secondary (allowed)."""
    import re
    warnings = []
    article_lower = article_text.lower()
    ref_lower = ref_text.lower()

    # Collect reference-safe numbers (all digits from ref data)
    ref_nums = set()
    for m in re.finditer(r"\b\d+\b", ref_lower):
        ref_nums.add(m.group())

    # Check money amounts: £80m, $100m, €50m, "80 million", etc
    for m in re.finditer(
        r"\b(?:[£$€]\s*\d[\d,.]*\s*(?:m|million|bn|billion|k|thousand)?|"
        r"\d[\d,.]*\s*(?:m|million|bn|billion|k|thousand))\b",
        slides_text, re.IGNORECASE
    ):
        val = m.group().strip().lower()
        if val in article_lower:
            continue
        if val in ref_lower:
            continue
        warnings.append(f"NUMBER_HALLUCINATION: '{m.group().strip()}' not in article or reference")

    # Check 4-digit years (likely tournament years, record milestones)
    for m in re.finditer(r"\b(20\d{2})\b", slides_text):
        year = m.group()
        if year in ref_nums:
            continue
        if re.search(r"\b" + re.escape(year) + r"\b", article_lower):
            continue
        warnings.append(f"NUMBER_HALLUCINATION: '{year}' not in source article")

    # Check "X years" / "X-year-old" patterns (ages, durations)
    for m in re.finditer(r"\b(\d{1,2})\s*(?:year(?:s)?\b|[ \-]year[ \-]old\b)", slides_text, re.IGNORECASE):
        num = m.group(1)
        if num in ref_nums:
            continue
        if re.search(r"\b" + re.escape(num) + r"\b", article_lower):
            continue
        # Also check if 2030 age appears in computed reference
        if f"~{num} at 2030" in ref_lower:
            continue
        warnings.append(f"NUMBER_HALLUCINATION: age/duration '{m.group().strip()}' not in source")

    return warnings


def generate_slides(article_text, url, title="", source="", hooks="", cta_pattern="", tone="", pattern="a", evaluator_feedback=""):
    """Call LLM to generate 6-slide thread. Returns parsed slides or None.
    If evaluator_feedback is provided, appends correction instructions to the prompt."""
    if not MISTRAL_KEY:
        log("❌ No MISTRAL_API_KEY — cannot generate")
        return None

    # ── Build system prompt dynamically ──
    base = """## INSTRUCTION PRIORITY (override order)
When instructions compete, follow this order:
1. Factual accuracy and source integrity.
2. Safety, fairness, preservation of uncertainty.
3. One-story coherence.
4. Clarity and reader comprehension.
5. Narrative tension and retention.
6. Brand voice and engagement.
7. Pattern or style preferences.
Never sacrifice accuracy for viral pattern, punchline, word limit, or engagement goal.

## IDENTITY
You are the editorial content engine for @parkthebus.football.
Write like a sharp, well-informed football fan who reads too much football news. You are NOT a journalist, bot, tabloid, or tactical analyst.

Your audience = global English-speaking casual football fans. They recognize major players, clubs, managers, and competitions. They scroll fast. They want story + drama + stakes — no fluff.

## PRIMARY OBJECTIVE
Turn one football news article into a six-slide Threads post that:
1. Stops the scroll with a specific, factual hook.
2. Makes the story understandable without reading the source.
3. Escalates tension or significance across slides.
4. Adds clear editorial perspective — without inventing facts.
5. Ends with a story-specific discussion prompt.

## EDITORIAL LENS (at least one per post)
- Expose a contradiction or double standard.
- Explain why one overlooked detail changes the story.
- Challenge a popular fan assumption with supported evidence.
- Reveal human stakes behind a headline.
- Turn a complicated issue into a fair, sharp argument.
Do not manufacture conflict when evidence does not support it.

## SOURCE VALIDATION (silent pre-draft check)
1. Title and body refer to the same main story.
2. Body has enough supported info for six slides.
3. Central claim is attributable to a named source.
4. Rumours, allegations, predictions, and confirmed facts are distinguishable.
If article is empty, truncated, contradictory, or too thin for six useful slides: produce nothing. Never pad weak input with invented context.

## EVIDENCE RULES
- Every factual claim must be supported by: the article, factual reference data below, or external sources supplied by tools.
- Preserve uncertainty exactly: "could", "reportedly", "expected", "alleged" must NOT become confirmed facts.
- Prefer paraphrase over direct quotes. Exact quote only when precise wording matters.
- **NEVER invent:** quote, fee, valuation, age, date, statistic, injury, incident, motive, tactical reason, or consequence.
- Do NOT calculate ages, future ages, fees, percentages, or time intervals unless explicitly provided in reference data below.
- Do NOT infer private motives or emotions. Frame analysis as interpretation: "That makes this feel..." / "The bigger issue is..."
- When listing part of a longer list: use "including" or "among them". Never imply completeness.
- Attribute the main source once — normally in slide 2, 3, or 4. Not slide 1 unless the source itself is the story.
- External knowledge: ONLY for slide 6 irony. Must be common knowledge (stadium name, famous club history, iconic player).

## SINGLE STORY RULE
One article = one story. Pick the strongest storyline from title + body together.
For live blogs or multi-article roundups: IGNORE everything except the story in the title. All 6 slides follow ONE line. Never merge separate transfers, matches, or controversies.

## VIRAL CRITERIA + ENGAGEMENT DRIVERS
Every slide must hit >=2 criteria. Pick >=2 drivers per post.
**CRITERIA:**
1. Pro & Con — tension, debate, two sides
2. Relatable — money, loyalty, underdog, betrayal
3. Famous figure — name-drop early
4. Comedy/irony — absurd stat, contradiction
5. Surprising fact — jaw-drop number
6. Emotional — anger, sympathy, nostalgia
7. Scroll-stopper — S1: straight to conflict in <2 seconds
**ENGAGEMENT DRIVERS:**
- Shareable insight: stat worth screenshotting
- Comment bait: polarizing take grounded in evidence
- Like fuel: praise underrated player, criticize rival
- Save-worthy: timeline, breakdown, comparison

## VOICE + STYLE
- Natural global English with football terminology (not "soccer").
- Casual but informed. Sharp but fair. Confident but properly hedged.
- Short, varied sentences. Concrete nouns + active verbs.
**FORBIDDEN:** emoji, hashtags, em dashes, all-caps (except official abbreviations).
**FORBIDDEN PHRASES:** "Did you know?" / "Let's dive in!" / "Here's the secret" / "You won't believe" / "Let that sink in" / "Fans everywhere are talking about it" / "Say what you want, but..." / "This changes everything" / "Only time will tell" / Generic "Agree or disagree?" without story-specific proposition / Generic "Follow for more" / Rage bait, fake suspense, forced rivalry, criticism added solely for likes.
**INSTEAD:** Open with surprising fact directly. Name the venue or person — not "fans everywhere". Close with natural story-specific question. Attribute source outlet once, naturally.

## 6-SLIDE ARC
**S1 — HOOK:** EXACTLY 2 sentences. Sentence 1 = specific action + who. Sentence 2 = context/stakes/why it matters. Total <=25 words. NOT bare ("Wiped. Gone. Why?") but dense ("FIFA wiped Paredes' red card — no suspension, no fine. What message does this send?").
**S2 — EVIDENCE:** Clearest detail, number, decision, scene, or verified statement. Make it tangible.
**S3 — CONTEXT:** Rule, timeline, background needed to understand the conflict.
**S4 — STAKES:** Who is affected, why it matters now. Distinguish confirmed consequences from possible implications.
**S5 — TAKE:** Sharpest fair interpretation. Reveal contradiction, overlooked detail, or larger meaning. Do not just repeat the hook.
**S6 — PAYOFF:** One or two sentences. Story-specific question. For divisive topics: name two real options ("Tuchel stays or walks?"). For sensitive topics (injuries, abuse, discrimination, criminal allegations): reflective question, NOT divisive bait.

## PER-SLIDE CONSTRAINTS
- S2-S5: 2-3 sentences each. One new insight per slide.
- EVERY SLIDE MUST HAVE A TAKE: max 1 descriptive sentence ("X said Y"). At least 1 stance sentence (agreement, disagreement, surprise, analysis, irony, or pointed question).
- Each slide reveals: physical detail, affected stakeholder, historical precedent, or ironic twist.
- Use specific numbers from the article. If zero numbers: narrative arc only. NEVER invent.
- MAX 15 WORDS PER SENTENCE. Short sentences hit harder.
- Paraphrase quotes. Never copy-paste full quotes.

## CAPTION
Zero emoji. Line 1 = headline hook. Last line = story-specific binary question with inline engagement hook: "Agree or disagree - [story-specific question]?"
NO generic "Follow for more". CTA must reference the story: "Who replaces X? Follow for more."

## NUMBER TRUTH (ZERO TOLERANCE)
1. Numbers ONLY from article text OR factual reference data below.
2. NEVER calculate ages. Use reference data age.
3. NEVER calculate years-to-event. Use reference data year-gap.
4. Hallucination history: "He's 31" (not in article), "6 years until 2030" (wrong), invented transfer fees.
5. No number > wrong number.

## COVER IMAGE
cover_image_keywords: 2-3 search terms (e.g. "Tuchel training kit England"). Prioritize story's most emotionally relevant subject. Not always a player — coach, referee, stadium, trophy, match moment may fit better. No text overlays.

## OUTPUT CONTRACT — JSON only, no markdown wrapping.
Return this EXACT schema:
{"slide_1":"","slide_2":"","slide_3":"","slide_4":"","slide_5":"","slide_6":"","caption":"","cover_image_keywords":""}
Sentences within slides: separated by \\n (newline). Between slides: the JSON keys define boundaries.
If article is insufficient: return {"slide_1":"needs_more_source","slide_2":"","slide_3":"","slide_4":"","slide_5":"","slide_6":"","caption":"","cover_image_keywords":""} with slide_1 starting with "needs_more_source".

## FINAL SELF-CHECK (silent, before output)
- Valid JSON. Exactly 6 slides. One coherent story.
- S1: exactly 2 sentences, <=25 words.
- Every sentence <=15 words.
- Slides 2-5 each add new info or interpretation (no repeats).
- S6 ends with story-specific question.
- Every claim has article or reference data support.
- Uncertainty preserved. Attribution appears once naturally.
- No forbidden phrase, emoji, hashtag, em dash present.
"""
    # Pattern-specific arc template
    arc_templates = {
        "a": """## ARC: Rule-Break (Pattern A)
S1 = VIRAL HOOK: "[Authority] just [broke/violated] its own [rule] for [Team A] vs [Team B]. [Concrete detail] — [Binary Q with irony/venue twist]"
EXACTLY 2 sentences. Example: "FIFA just broke its own golden rule for England vs Argentina. The Mercedes-Benz logo stays — engineering nightmare or sponsor snub?"

S2 = PHYSICAL DETAIL: ONE vivid detail — size, number, quote, timeline. NOT "what the rule says". Make reader imagine the scene.
S3 = LORE + CONTEXT: The existing rule, affected sponsors, why this is a first.
S4 = STAKES: Raise tension. Background context → real consequences for stakeholders.
S5 = WHAT MAKES THIS UNIQUE: Why this bends the rule matters more than usual.
S6 = BINARY: Question about interpretation or consequences using irony/venue twist.
""",
        "c": """## ARC: Detail+Emotion (Pattern C)
S1 = HOOK: Core conflict or surprising detail. Use "Revealed", "Admitted", or opinion framing. EXACTLY 2 sentences.
S2 = DATA: The specific number, quote, or report driving the story.
S3 = CONTEXT: Background making the data meaningful.
S4 = STAKEHOLDER: Affected party — player, club, fans, league.
S5 = IRONY: Why this is unexpected, contradictory, or ironic.
S6 = BINARY: Question about future implications or interpretation.
""",
        "e": """## ARC: Pressure Cooker (Pattern E)
S1 = HOOK: "[Player/Manager] [not happy/fumes/speaks out] after [event]. [Reaction] — [Binary Q about future]" EXACTLY 2 sentences.
S2 = TENSION CONTEXT: What triggered the reaction. Specific incident/decision/quote.
S3 = WHO'S INVOLVED: Other parties — teammates, board, fans, media.
S4 = STAKES: What happens if tension escalates. Job, transfer, board meeting?
S5 = WHAT'S UNIQUE: History, contract situation, timing making this matter more.
S6 = BINARY: "[Option specific to this tension] or [option specific to this tension]?"
""",
        "f": """## ARC: Behind-the-Scenes (Pattern F)
S1 = HOOK: "Why [team/authority] [did/decided] [specific thing]. [Detail] — [Binary Q]" EXACTLY 2 sentences.
S2 = THE SITUATION: What happened, when, where. Specific logistics detail.
S3 = WHY IT MATTERS: Impact on match, players, or tournament.
S4 = WHO BENEFITS/WHO LOSES: Advantage or disadvantage created.
S5 = THE REAL STORY: What this reveals about the organization behind the scenes.
S6 = BINARY: "Will [factor] affect [result], or is it just [dismissive explanation]?"
""",
        "d": """## ARC: Commentary (Pattern D)
S1 = HOOK: The quote/opinion/claim driving the story. Name the speaker. "Revealed", "Admitted", "Says" framing. EXACTLY 2 sentences.
S2 = THE QUOTE: Exact quote or specific claim. Attribute clearly.
S3 = CONTEXT: Why this person's opinion matters — their role, history, or stake.
S4 = COUNTERPOINT: Opposition, rebuttal, or nuance. Who disagrees and why.
S5 = STAKES: How this opinion affects real decisions. Transfer, selection, policy.
S6 = BINARY: Question about whether the opinion will hold up or be acted on.
""",
    }
    system = base + arc_templates.get(pattern, arc_templates["c"])
    ref_data = _build_reference_data()
    source_name = source or url.split("/")[2] if url else ""
    pattern_label = {'a':'rule_break', 'b':'contradiction', 'c':'straight_news', 'd':'straight_news', 'e':'pressure_cooker', 'f':'straight_news'}.get(pattern, 'straight_news')
    user = (
        f"<request>\n  <current_date>{datetime.now().strftime('%Y-%m-%d')}</current_date>\n"
        f"  <selected_pattern>{pattern_label}</selected_pattern>\n</request>\n\n"
        f"<primary_article>\n  <title>{title}</title>\n  <source_name>{source_name}</source_name>\n"
        f"  <source_url>{url}</source_url>\n  <article_body>\n{article_text[:8000]}\n  </article_body>\n"
        f"</primary_article>\n\n{ref_data}")
    if evaluator_feedback:
        user += f"\n\n## ⚠️ EVALUATOR REJECTED YOUR PREVIOUS ATTEMPT — FIX THESE ERRORS:\n{evaluator_feedback}\nRegenerate ALL 6 slides. Do NOT repeat the errors above."

    for attempt in range(1, 4):
        log(f"   LLM attempt {attempt}/3...")
        try:
            r = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
                json={"model":"mistral-large-latest","messages":[
                    {"role":"system","content":system},{"role":"user","content":user}],
                    "max_tokens":4000,"temperature":0.3,"stream":True},
                timeout=120, stream=True)

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
            # Clean thinking tags
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"^```(?:json|text)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            # Parse JSON output
            slides = []
            caption = ""
            hashtags = ""
            try:
                data = json.loads(content, strict=False)
                # Check for insufficient-article signal
                s1 = data.get("slide_1", "").strip()
                if s1.lower().startswith("needs_more_source"):
                    log(f"   ❌ Article insufficient: {s1[:120]}")
                    return None
                for i in range(1, 7):
                    key = f"slide_{i}"
                    text = data.get(key, "").strip()
                    if text and len(text) >= 10:
                        # Post-process: clean formatting
                        text = text.replace("—", " - ").replace("–", " - ")
                        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
                        text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', text)
                        # Insert \n between sentences (whitespace after each sentence)
                        text = re.sub(r'(?<!Mr)(?<!Mrs)(?<!Ms)(?<!Dr)(?<!St)(?<!vs)(?<!Jr)(?<!Sr)(?<!Prof)([.?!])\s+(?=[A-Z])', r'\1\n', text)
                        slides.append({"title": f"S{i}", "content": text})
                caption = data.get("caption", "").strip()
                hashtags = data.get("hashtags", "").strip()
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                # Fallback: try old "Slide N:" format
                content = re.sub(r'\*\*Slide\s+(\d)\s*:\*\*', r'Slide \1:', content)
                slide_pattern = re.compile(r'(?:^|\n)\s*Slide\s+(\d)\s*:\s*\n(.*?)(?=\n\s*Slide\s+\d\s*:|\Z)', re.DOTALL | re.IGNORECASE)
                for match in slide_pattern.finditer(content):
                    num = int(match.group(1))
                    text = match.group(2).strip()
                    if text and len(text) >= 20:
                        text = text.replace("—", " - ").replace("–", " - ")
                        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
                        text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', text)
                        # Insert \n between sentences (whitespace after each sentence)
                        text = re.sub(r'(?<!Mr)(?<!Mrs)(?<!Ms)(?<!Dr)(?<!St)(?<!vs)(?<!Jr)(?<!Sr)(?<!Prof)([.?!])\s+(?=[A-Z])', r'\1\n', text)
                        slides.append({"title": f"S{num}", "content": text})
            if len(slides) < 3:
                log(f"   ❌ Only {len(slides)} parseable slides")
                continue
            # Store caption/hashtags on slides for later use
            if caption:
                slides[0]["caption"] = caption
            if hashtags:
                slides[0]["hashtags"] = hashtags
            # Auto-trim slide 2-5 to max 3 sentences
            for i, s in enumerate(slides[:6]):
                n = _count_sentences(s["content"])
                if n > 3 and i not in (0, 5):
                    parts = re.split(r'(?<=[.!?])\s+', s["content"].strip())
                    s["content"] = " ".join(parts[:3])
            # Source URL on last slide
            last = slides[-1]["content"]
            url_base = url.split("?")[0].rstrip("/")
            if url_base not in last and url not in last:
                new_last = last.rstrip() + "\n\n" + url
                if len(new_last) > 480:
                    new_last = last.rstrip()[:480] + "...\n\n" + url
                slides[-1]["content"] = new_last
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
    except Exception: return None, None

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

# ── 5b. TELEGRAM NOTIFY ───────────────────────────────────────────

def notify_telegram(text):
    """Send notification via @szejay_bot."""
    try:
        token_file = os.path.expanduser("~/.szejay_token")
        if not os.path.exists(token_file):
            return
        with open(token_file) as f:
            token = f.read().strip()
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": 1022032312, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception:
        pass

# ── 6. TRACK ───────────────────────────────────────────────────────

def track_post(title, url, source, root_id, permalink, hotness_score=0):
    """Append to posted_topics.json."""
    try:
        with open(POSTED) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): data = {"topics":[]}
    if "topics" not in data: data["topics"] = []
    entry = {
        "title": title, "url": url, "source": source,
        "post_id": root_id, "permalink": permalink,
        "posted_at": datetime.now(WIB).isoformat(),
        "published_ts": time.time(),
    }
    if hotness_score:
        entry["hotness_score"] = round(hotness_score, 2)
    data["topics"].append(entry)
    # Keep last 200 entries
    data["topics"] = data["topics"][-200:]
    with open(POSTED, "w") as f:
        json.dump(data, f, indent=2)

# ── 7. PRE-FLIGHT ──────────────────────────────────────────────────

def _self_check():
    """Validate all essential names exist before main() runs."""
    required = [
        "scrape_rss", "scrape_goal",
        "fetch_article", "extract_article", "extract_image",
        "generate_slides", "post_to_threads", "notify_telegram",
        "track_post", "load_threads_token",
        "_select_viral_pattern", "grounding_check",
        "_extract_proper_nouns", "_extract_stages",
        "_match_sensitive", "_http", "_build_reference_data",
        "_count_sentences",
        "log",
    ]
    missing = [n for n in required if n not in globals()]
    if missing:
        msg = f"❌ Pre-flight failed — missing: {', '.join(missing)}"
        log(msg)
        print(msg, flush=True)
        sys.exit(1)
    log("✔ Pre-flight ok")

# ── MAIN ────────────────────────────────────────────────────────────

def main():
    START = time.time()
    log("=== PRESSBOX MVP ===")

    # 0. Init Threads poster (for metrics)
    token, user_id = load_threads_token()
    poster = None
    if token and user_id:
        try:
            from threads_poster import ThreadsPoster
            poster = ThreadsPoster(access_token=token, user_id=user_id)
        except:
            log("⚠️ Failed to init ThreadsPoster for reply")

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
    hotness = detect_hot_topics(topics, window_hours=4)
    ranked = filter_and_score(topics, posted_urls, posted_ws, boosts, skips, analytics_summary, hotness)
    if not ranked:
        log("❌ No topics after filter")
        print("❌ Pipeline: all topics filtered out", flush=True)
        sys.exit(1)

    # Score gate — dynamic threshold from batch median (adaptive)
    best = ranked[0]
    # Compute median of top scores in this batch
    batch_scores = sorted([t["_score"] for t in ranked[:10]])
    batch_median = batch_scores[len(batch_scores) // 2] if batch_scores else 0
    threshold = max(8, min(25, batch_median))
    log(f"   📊 Batch median={batch_median:.0f}, threshold={threshold}")
    if best["_score"] < threshold:
        log(f"   ⏸️ Best score {best['_score']} < {threshold} threshold — skipping")
        print(f"⏸️ Skip — best topic score {best['_score']} below threshold", flush=True)
        sys.exit(1)
    log(f"   🏆 Best: {best['title']} (score={best['_score']}, type={best.get('_topic_type','')})")

    # 3. Fetch article — try top 3 topics, verify body is football news
    url = best["url"]
    log(f"   Fetching: {url}")
    article_text, image_url = fetch_article(url)
    fetch_tries = 1

    def _is_commercial_body(text):
        """Check if article body is commercial/shopping, not football news."""
        bl = text[:3000].lower()
        football = sum(1 for kw in ["goal","match","score","league","cup","transfer",
            "manager","player","team","club","stadium","referee","penalty",
            "red card","yellow card","world cup","champions league",
            "premier league","tournament","qualifier","fixture","midfielder",
            "striker","defender","goalkeeper","captain","substitute"] if kw in bl)
        commercial = sum(1 for kw in ["price","buy now","shop now","discount",
            "sale","voucher","coupon","basket","checkout","delivery",
            "add to basket","purchase","save £","save $","% off","free shipping",
            "snap up","bargain","order now","next day delivery"] if kw in bl)
        return football < 2 and commercial >= 2

    while fetch_tries < len(ranked[:15]):
        # Check length
        if not article_text or len(article_text) < 100:
            log(f"   ❌ Article too short on '{best['title']}' — trying next")
        elif _is_commercial_body(article_text):
            log(f"   🛒 Body is commercial, not football — trying next")
        elif len(article_text.strip()) < 1000:
            log(f"   ⚠️ Article too short ({len(article_text)} chars) — trying next")
        elif len(article_text.split()) < 150:
            log(f"   ⚠️ Article too thin ({len(article_text.split())} words) — trying next")
        elif len([s for s in re.split(r'[.!?]+', article_text) if len(s.strip()) > 20]) < 5:
            log(f"   ⚠️ Article too few sentences (< 5) — trying next")
        else:
            break  # Article is valid
        best = ranked[fetch_tries]
        url = best["url"]
        log(f"   Fetching next: {url}")
        article_text, image_url = fetch_article(url)
        fetch_tries += 1
    if not article_text or len(article_text) < 100:
        log("❌ All top articles too short")
        print("❌ Pipeline: all articles too short", flush=True)
        sys.exit(1)
    if _is_commercial_body(article_text):
        log("❌ All top articles are commercial/shopping")
        print("❌ Pipeline: all articles are commercial, not football news", flush=True)
        sys.exit(1)
    log(f"   Article: {len(article_text)} chars, image: {'yes' if image_url else 'no'}")
    if len(article_text.strip()) < 1000:
        log(f"   ⚠️ Article too short ({len(article_text)} chars < 1000 min). Skipping LLM.")
        print(f"❌ Pipeline: article too short for carousel ({len(article_text)} chars)", flush=True)
        sys.exit(1)
    word_count = len(article_text.split())
    if word_count < 150:
        log(f"   ⚠️ Article too thin ({word_count} words < 150 min). Skipping LLM.")
        print(f"❌ Pipeline: article too thin for carousel ({word_count} words)", flush=True)
        sys.exit(1)
    # Sentence count filter — catches boilerplate-inflated articles
    sentences = [s.strip() for s in re.split(r'[.!?]+', article_text) if len(s.strip()) > 20]
    if len(sentences) < 5:
        log(f"   ⚠️ Article too few sentences ({len(sentences)} < 5 min). Skipping LLM.")
        print(f"❌ Pipeline: article too few sentences ({len(sentences)})", flush=True)
        sys.exit(1)

    # Image priority: og:image (1200px) > RSS thumbnail (240px)
    if not image_url and best.get("image_url"):
        image_url = best["image_url"]
        log(f"   🖼️ Fallback to RSS thumbnail: {image_url[:60]}")
    elif image_url and best.get("image_url"):
        log(f"   🖼️ Using og:image (HD) over RSS thumbnail")

    # 4. Generate slides (with article fallback + evaluator retry)
    # Outer loop: try next ranked article if evaluator rejects all 3 attempts
    # Inner loop: max 3 generate→evaluate cycles per article
    article_fallback_idx = fetch_tries  # start from where we left off after article quality checks
    slides = None
    llm_time = 0
    article_accepted = False
    hooks = ""
    for article_attempt in range(3):  # try up to 3 different articles
        if article_attempt > 0:
            # Try next ranked article
            next_idx = article_fallback_idx + article_attempt
            if next_idx >= len(ranked[:15]):
                log("   ❌ No more ranked articles to try")
                break
            best = ranked[next_idx]
            url = best["url"]
            log(f"   🔄 Trying next article: {best.get('title','')[:60]}")
            article_text, image_url = fetch_article(url)
            if not article_text or len(article_text) < 1000:
                log(f"   ⚠️ Next article too short ({len(article_text or '')} chars) — skipping")
                continue
            # Article quality pre-check: must have quotes + numbers (not just fluff)
            _has_quotes = '"' in article_text
            _has_numbers = bool(re.search(r'\d{3,}', article_text))  # 100+ = real stat
            if not _has_quotes and not _has_numbers:
                log(f"   ⚠️ Article quality: no quotes + no numbers — likely fluff/table, skipping")
                continue
            # Re-extract hooks for new article
            hooks = ""
            hooks_str = ", ".join(hooks) if isinstance(hooks, list) else hooks

        t0 = time.time()
        pattern = _select_viral_pattern(best, article_text)
        pattern_name = {'a': 'A (Rule-Break)', 'b': 'B (deprecated)', 'c': 'C (Detail+Emotion)', 'd': 'D (Commentary)', 'e': 'E (Pressure-Cooker)', 'f': 'F (Behind-the-Scenes)'}[pattern]
        log(f"   🎯 Viral pattern: {pattern_name}")
        hooks_str = ", ".join(hooks) if isinstance(hooks, list) else hooks
        eval_feedback = ""
        eval_accepted = False
        for eval_round in range(3):  # max 3 generate→evaluate cycles
            slides = generate_slides(article_text, url, title=best.get("title",""), source=best.get("source",""), hooks=hooks_str, cta_pattern=cta_pattern, tone=tone, pattern=pattern, evaluator_feedback=eval_feedback)
            if not slides:
                log("   ⚠️ LLM generation failed — trying next article")
                break
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
                log(f"   ⚠️ Stage warnings (soft): {'; '.join(hallucinated_stages)}")

            # 5.3 Number grounding check — reject ungrounded numbers before expensive evaluator
            ref_data_check = _build_reference_data()
            num_warnings = number_grounding_check(slides_text, article_text, ref_data_check)
            if num_warnings:
                warn_str = "; ".join(num_warnings)
                log(f"   🚫 Number hallucination detected: {warn_str}")
                if eval_round < 2:
                    eval_feedback = "\n".join(f"- {w}" for w in num_warnings)
                    log(f"   🔄 Retrying (round {eval_round+1}/3) with number grounding feedback")
                    continue
                else:
                    log(f"   🚫 Number hallucination persisted after 3 attempts — trying next article")
                    break

            # 5.5. Evaluator — skip for E/F patterns (structural, high-trust, ~50s saved)
            if pattern in ("e", "f"):
                log(f"   ⏭️ Evaluator skipped (pattern {pattern.upper()} — structural/high-trust)")
                eval_accepted = True
                break
            # Also skip for high-score posts
            score_val = hotness.get(url, 0) or best.get("_score", 0)
            if score_val >= 80:
                log(f"   ⏭️ Evaluator skipped (score {score_val:.0f} >= 80)")
                eval_accepted = True
                break
            eval_t0 = time.time()
            # Cache check: same URL evaluated before? Skip re-eval
            cached = _EVAL_CACHE.get(url)
            if cached:
                eval_decision, eval_reasons = cached["decision"], cached["reasons"]
                eval_time = time.time() - eval_t0
                log(f"   🔍 Evaluator (cached): {eval_decision} ({eval_time:.1f}s) — {'; '.join(eval_reasons[:3])}")
            else:
                eval_decision, eval_reasons = evaluator_check(slides, article_text, url)
                eval_time = time.time() - eval_t0
                log(f"   🔍 Evaluator: {eval_decision} ({eval_time:.1f}s) — {'; '.join(eval_reasons[:3])}")
                _EVAL_CACHE[url] = {"decision": eval_decision, "reasons": eval_reasons}
                _save_eval_cache()

            if eval_decision == "APPROVE":
                eval_accepted = True
                break
            elif eval_decision == "REVISE":
                log(f"   ⚠️ Evaluator REVISE — approving with notes: {'; '.join(eval_reasons[:3])}")
                eval_accepted = True
                break  # REVISE = fixable issues, post anyway
            else:  # REJECT
                if eval_round < 2:
                    eval_feedback = "\n".join(f"- {r}" for r in eval_reasons)
                    log(f"   🔄 Evaluator REJECTED (round {eval_round+1}/3) — retrying with feedback")
                    continue
                else:
                    log(f"   🚫 Evaluator REJECTED after 3 attempts — trying next article")
                    break

        if eval_accepted:
            article_accepted = True
            break

    if not article_accepted or not slides:
        log("❌ Pipeline: all articles failed evaluator or generation")
        print("❌ Pipeline: all articles failed evaluator or generation", flush=True)
        sys.exit(1)

    # 6. DRY RUN or POST
    total = time.time() - START

    if DRY_RUN:
        log(f"🔍 DRY RUN — {best['title']} ({len(slides)} slides)")
        for i, s in enumerate(slides):
            print(f"\n--- Slide {i+1} ({s['title']}) ---\n{s['content']}")
        if slides and slides[0].get("caption"):
            print(f"\n--- Caption ---\n{slides[0]['caption']}")
        if slides and slides[0].get("hashtags"):
            print(f"\n--- Hashtags ---\n{slides[0]['hashtags']}")
        print(f"\n✅ Dry run done in {total:.1f}s (LLM: {llm_time:.1f}s)")
        return

    # Post
    root_id, permalink = post_to_threads(slides, image_url)
    if not root_id:
        err_msg = f"❌ Post failed: {best.get('title','?')[:60]} | source={url[:50]}"
        notify_telegram(f"❌ <b>Post Gagal</b>\n\n{best['title']}\nSource: {url}\n\nLLM gagal generate atau post error.")
        print(err_msg, flush=True)
        sys.exit(1)

    # Track
    track_post(best["title"], url, best.get("source",""), root_id, permalink,
               hotness_score=hotness.get(url, 0))

    log(f"✅ {best['title']} → {permalink}")
    log(f"⏱️ Total: {total:.1f}s (LLM: {llm_time:.1f}s)")

    # Notify @szejay_bot
    score = best.get("_score", 0)
    notify_telegram(
        f"✅ <b>Posted!</b>\n\n"
        f"{best['title']}\n"
        f"Score: {score} | {len(slides)} slides\n"
        f"Pattern: {pattern.upper()}\n"
        f"Source: {best.get('source','?')}\n\n"
        f"<a href=\"{permalink}\">View on Threads</a>"
    )

    # Summary report (stdout → delivered to Telegram topic 20467)
    slide_count = len(slides)
    now = datetime.now(timezone(timedelta(hours=7)))
    wib = now.strftime("%H:%M WIB, %d %b %Y")
    report = f"""✅ Posted @ {wib}
{best['title'][:100]}
Score: {score} | {slide_count} slides | {total:.1f}s
{permalink}"""
    # Save for hourly report
    with open("/tmp/pressbox-last-report", "w") as f:
        f.write(report)
    print(report, flush=True)

if __name__ == "__main__":
    _self_check()
    import random as _rnd
    if "--with-jitter" in sys.argv:
        _jitter = _rnd.randint(0, 30)
        log(f"⏳ Jitter sleep: {_jitter}s")
        time.sleep(_jitter)
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        err = traceback.format_exc()
        log(f"❌ CRASH: {err[:500]}")
        notify_telegram(f"❌ <b>Pipeline Crash</b>\n\n{err[:1000]}")
        sys.exit(1)
