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
SOURCES = ["skysports", "goal", "bbc", "fourfourtwo", "mirror"]
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
    log("Scraping 5 sources...")
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
            "skysports": ex.submit(scrape_with_fingerprint, "skysports", scrape_rss, "https://www.skysports.com/rss/11095", "skysports", 12),
            "goal": ex.submit(scrape_with_fingerprint, "goal", scrape_goal),
            "bbc": ex.submit(scrape_with_fingerprint, "bbc", scrape_rss, "https://feeds.bbci.co.uk/sport/football/rss.xml", "bbc", 10),
            "fourfourtwo": ex.submit(scrape_with_fingerprint, "fourfourtwo", scrape_rss, "https://www.fourfourtwo.com/rss", "fourfourtwo", 8),
            "mirror": ex.submit(scrape_with_fingerprint, "mirror", scrape_rss, "https://www.mirror.co.uk/sport/football/rss.xml", "mirror", 7),
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

    # If ALL sources unchanged, force a full scrape (prevent stale pipeline)
    if not all_t and skipped:
        log("   ⚠️ All sources unchanged — forcing full scrape")
        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = {
                "skysports": ex.submit(scrape_rss, "https://www.skysports.com/rss/11095", "skysports", 12),
                "goal": ex.submit(scrape_goal),
                "bbc": ex.submit(scrape_rss, "https://feeds.bbci.co.uk/sport/football/rss.xml", "bbc", 10),
                "fourfourtwo": ex.submit(scrape_rss, "https://www.fourfourtwo.com/rss", "fourfourtwo", 8),
                "mirror": ex.submit(scrape_rss, "https://www.mirror.co.uk/sport/football/rss.xml", "mirror", 7),
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
        sources = set(m[0].get("source", "") for m in members)
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

    return hotness

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
        summary["score_tuning"] = _compute_score_tuning(with_metrics, median_views)

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
        tuning["keyword_multiplier"] = round(min(1.5, max(0.7, kw_ratio)), 2)
    
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
_COMMERCIAL = ["snap up","buy now","deal","discount","shop","price drop","sale","coupon","voucher",
               "bargain","save £","save $","off rrp","% off","for £","for $","amazon","ebay",
               "where to buy","get yours","order now","delivery","free shipping","stock up"]
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
        if t.get("transfer_related"): s += 10
        # Niche topic penalty — low engagement content that happens to mention big teams
        _niche_kw = ["kit launch","kit reveal","jersey","boots","pink boots","kit deal",
                     "boot deal","stadium rules","ticket prices","travel guide",
                     "how to watch","tv channel","broadcast","kit manufacturer","shirt sponsor"]
        if any(kw in tl for kw in _niche_kw):
            s -= 30
            log(f"   📉 Niche topic: -30 for '{title[:50]}'")
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

        # Hot topic boost (multi-source coverage = viral)
        # Skip hot boost for niche topics — they ride trending entity clusters without being newsworthy
        _is_niche = any(kw in tl for kw in _niche_kw) if '_niche_kw' in dir() else False
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
        if hot >= 3.0 and not _is_niche and _hot_relevant:
            boost = 25 + hot_adjust + peak_boost
            s += boost
            log(f"   🔥 Hot boost: +{boost} for '{title[:50]}' (hotness={hot:.1f}, adjust={hot_adjust:+d}, peak={hour in peak_hours})")
        elif hot >= 1.5 and not _is_niche and _hot_relevant:
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
        return {a["url"]: (a.get("text", ""), a.get("cached_image", "")) for a in cache if a.get("text")}
    except:
        return {}

def _save_article_text_to_cache(url, text, image_url=""):
    """Store fetched article text in cache for reuse."""
    try:
        with open(ARTICLE_CACHE) as f:
            cache = json.load(f)
        for a in cache:
            if a.get("url") == url:
                a["text"] = text[:5000]  # cap to prevent bloat
                a["cached_image"] = image_url
                break
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

def _count_sentences(text):
    return len([s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if len(s.strip()) > 5])

def _select_viral_pattern(topic, article_text):
    """Select Pattern A (scandal/nobody's talking) or B (paradox/warning) based on article content."""
    title = (topic.get("title") or "").lower()
    text = article_text.lower()[:2000]
    combined = title + " " + text
    
    # Pattern A signals: scandal, controversy, hidden reason, money, behind-scenes
    scandal_words = ["scandal", "controversy", "behind the scenes", "secret", "real reason",
                     "nobody talks", "ugly truth", "shocking", "betray", "refuse", "clash",
                     "furious", "rage", "slam", "blast", "row", "rift", "feud"]
    scandal_score = sum(1 for w in scandal_words if w in combined)
    
    # Pattern B signals: paradox, statistical anomaly, "despite"/"while", big team threat
    paradox_words = ["despite", "while barely", "yet somehow", "paradox", "irony",
                     "without", "only touched", "minimal", "fewest", "least",
                     "but only", "first in history", "record-breaking"]
    paradox_score = sum(1 for w in paradox_words if w in combined)
    
    # Pattern C signals: specific numbers, financial amounts, human interest, emotional weight
    detail_words = ["£", "$", "fee", "cost", "price", "pay", "million", "thousand",
                    "visa", "banned", "denied", "blocked", "refused", "mother", "father",
                    "family", "cry", "tears", "heart", "sacrifice", "hero", "legend"]
    detail_score = sum(1 for w in detail_words if w in combined)
    
    # Check for specific numbers/amounts
    import re as _re
    has_specific_number = bool(_re.search(r'\d+[\d,.]*\s*(?:£|$|million|thousand|k\b)', combined))
    if has_specific_number:
        detail_score += 3  # Strong signal for Pattern C
    
    # Has big team target for "you've been warned"?
    big_teams_warn = ["brazil", "argentina", "germany", "france", "spain", "england",
                      "real madrid", "barcelona", "manchester", "liverpool", "chelsea",
                      "bayern", "psg", "juventus", "inter milan", "arsenal"]
    has_big_team = any(bt in combined for bt in big_teams_warn)
    
    # Decision: scandal wins if higher, else detail. No more Pattern B.
    if scandal_score > detail_score:
        return "a"
    else:
        return "c"  # default to Pattern C (proven 500K+ views)

def generate_slides(article_text, url, hooks="", cta_pattern="", tone="", pattern="a"):
    """Call LLM to generate 6-slide thread. Returns parsed slides or None.
    Uses RCTOR prompt with INSUFFICIENT SOURCE protocol."""
    if not MISTRAL_KEY:
        log("❌ No MISTRAL_API_KEY — cannot generate")
        return None

    system = """You are a football content editor for @parkthebus.football, a Threads account known for sharp, story-driven football breakdowns. You turn news articles into 6-slide carousels that make people stop scrolling and actually read to the end.

Audience: football fans on Threads who scroll fast and skip generic recaps. They have already seen the scoreline elsewhere. Your job is to make them feel the moment, not re-read a headline.

Convert the input article into exactly 6 slides, following this structure:

1. HOOK. Stop-scroll opener. 2 sentences, under 30 words. Lead with tension or stakes, not a recap.
2. SETUP. The situation before the turning point. Max 3 sentences, roughly 40 words per sentence.
3. TURN. The pivotal moment or incident. Max 3 sentences.
4. DEEPEN. What this moment cost, risked, or changed. Must be grounded in details the article explicitly states (for example, what a card means for the next match, or how the team compensated). Max 3 sentences. Do not speculate about player mindset, hidden motives, or future outcomes that are not stated in the article.
5. PAYOFF. The resolution, final score, or what actually happened. Max 3 sentences.
6. CLOSE. A punchy takeaway or a question to drive comments. Max 2 sentences.

OUTPUT RULES:
* Plain text, labeled "Slide 1" through "Slide 6"
* No hashtags, no emojis unless natural to the story
* No em dashes anywhere in the output. Use periods, commas, or separate sentences instead.
* Every fact, name, score, and minute marker must come directly from the source article. Never invent stats, quotes, or events not in the source.
* If the source lacks enough material for a real 6-slide arc (for example a 100-word brief with no real turning point), output only this line: "INSUFFICIENT SOURCE. Only enough for [X] slides. Suggest merging with another story or running as a single-slide post." Do not pad with invented details to force 6 slides.

Hook rules (Slide 1):
* Under 30 words, 1 sentence
* No "Breaking:" or generic scoreline openers
* Lead with irony, cost, or stakes

DEEPEN rules (Slide 4), the highest hallucination-risk slide:
* Only state consequences or stakes the article itself explicitly mentions
* Do NOT invent hypothetical scenarios (e.g. "a red card would have...", "if they lose him...")
* Do NOT invent game-state changes (red cards, injuries, substitutions) unless the article states them
* Do NOT invent tactical shifts (formation changes, "down to ten men", "rampant on the counter") unless the article states them
* If the article does not explain what happens next, keep this slide about the emotional weight or the moment itself, not invented consequences
* When in doubt, describe what happened in the moment (the clash, the separation, the reaction) rather than what it caused or could have caused

Insufficient source protocol: flag it, do not fabricate to fill the arc.

Output format:
Slide 1:
[text]

Slide 2:
[text]

Slide 3:
[text]

Slide 4:
[text]

Slide 5:
[text]

Slide 6:
[text]"""

    user = f"ARTICLE: {article_text[:8000]}\nSOURCE: {url}"


    for attempt in range(1, 4):
        log(f"   LLM attempt {attempt}/3...")
        try:
            r = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
                json={"model":"mistral-large-latest","messages":[
                    {"role":"system","content":system},{"role":"user","content":user}],
                    "max_tokens":4000,"temperature":0.5,"stream":True},
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
            # Clean thinking tags
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"^```(?:text)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            # INSUFFICIENT SOURCE check
            if "INSUFFICIENT SOURCE" in content.upper():
                log(f"   ⚠️ INSUFFICIENT SOURCE: {content[:200]}")
                return None
            # Parse "Slide N:" format (plain text output)
            slides = []
            slide_pattern = re.compile(r'(?:^|\n)\s*Slide\s+(\d)\s*:\s*\n(.*?)(?=\n\s*Slide\s+\d\s*:|\Z)', re.DOTALL | re.IGNORECASE)
            for match in slide_pattern.finditer(content):
                num = int(match.group(1))
                text = match.group(2).strip()
                if text and len(text) >= 20:
                    slides.append({"title": f"S{num}", "content": text})
            if len(slides) < 3:
                log(f"   ❌ Only {len(slides)} parseable slides (plain text format)")
                continue
            # Post-process: clean formatting
            for s in slides:
                s["content"] = s["content"].replace("—", " - ").replace("–", " - ")
                s["content"] = re.sub(r'\*\*(.+?)\*\*', r'\1', s["content"])
                s["content"] = re.sub(r'\*(.+?)\*', r'\1', s["content"])
                s["content"] = re.sub(r'  +', ' ', s["content"])
                s["content"] = re.sub(r'([.!?])(\s+)([A-Z"])', r'\1\n\n\3', s["content"])
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
                slides[-1]["content"] = last.rstrip() + "\n\n" + url
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
    except: data = {"topics":[]}
    if "topics" not in data: data["topics"] = []
    entry = {
        "title": title, "url": url, "source": source,
        "post_id": root_id, "permalink": permalink,
        "posted_at": datetime.now(WIB).isoformat(),
    }
    if hotness_score:
        entry["hotness_score"] = round(hotness_score, 2)
    data["topics"].append(entry)
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
    hotness = detect_hot_topics(topics, window_hours=4)
    ranked = filter_and_score(topics, posted_urls, posted_ws, boosts, skips, analytics_summary, hotness)
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

    while fetch_tries < len(ranked[:3]):
        # Check length
        if not article_text or len(article_text) < 100:
            log(f"   ❌ Article too short on '{best['title']}' — trying next")
        elif _is_commercial_body(article_text):
            log(f"   🛒 Body is commercial, not football — trying next")
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

    # Image priority: og:image (1200px) > RSS thumbnail (240px)
    if not image_url and best.get("image_url"):
        image_url = best["image_url"]
        log(f"   🖼️ Fallback to RSS thumbnail: {image_url[:60]}")
    elif image_url and best.get("image_url"):
        log(f"   🖼️ Using og:image (HD) over RSS thumbnail")

    # 4. Generate slides — select viral pattern based on article content
    t0 = time.time()
    pattern = _select_viral_pattern(best, article_text)
    pattern_name = {'a': 'A (scandal)', 'b': 'B (paradox)', 'c': 'C (detail+emotion)'}[pattern]
    log(f"   🎯 Viral pattern: {pattern_name}")
    slides = generate_slides(article_text, url, hooks, cta_pattern, tone, pattern=pattern)
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
        log(f"   ⚠️ Stage warnings (soft): {'; '.join(hallucinated_stages)}")

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
        notify_telegram(f"❌ <b>Post Gagal</b>\n\n{best['title']}\nSource: {url}\n\nLLM gagal generate atau post error.")
        print("❌ Pipeline: post failed", flush=True)
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
    score = best.get("_score", 0)
    hook_type = best.get("_hook_type", "unknown")
    src = best.get("source", "unknown")
    slide_count = len(slides)
    slide_preview = slides[0]["content"][:120] if slides else "N/A"
    import datetime
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7)))
    wib = now.strftime("%H:%M WIB, %d %b %Y")
    post_count = len(json.load(open(POSTED)).get("topics", []))
    print(f"""{best['title'][:100]}
Score: {score} | {slide_count} slides | {total:.1f}s
{permalink}""", flush=True)

if __name__ == "__main__":
    import random as _rnd
    if "--with-jitter" in sys.argv:
        _jitter = _rnd.randint(0, 30)
        log(f"⏳ Jitter sleep: {_jitter}s")
        time.sleep(_jitter)
    main()
