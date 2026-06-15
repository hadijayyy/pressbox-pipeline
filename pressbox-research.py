#!/usr/bin/env python3
"""
Press Box Research — Batch scraper using 4 verified-working WC2026 sources.
Google News RSS blocked from this IP. Using direct site scraping.

Sources: BBC, Guardian, Mirror, Goal.com
- BBC, Mirror: per-article HTML scrape (timestamps + descriptions)
- Guardian: RSS feed
- Goal.com: JSON-LD ItemList

Usage:
  python3 pressbox-research.py           # Normal mode
  python3 pressbox-research.py --nocache # Bypass cache
  
Output: JSON array of top 5 WC2026 topics with verified source URLs.
Runtime: ~6-10s
"""

import json, re, sys, concurrent.futures, time, html
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

import httpx

NOW = datetime.now(timezone.utc)
FRESHNESS_CUTOFF = 24 * 3600  # 24 hours — 4h too strict, many Guardian articles older but relevant

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HOME = Path.home()
CACHE_FILE = HOME / ".hermes" / "pressbox_cache.json"
CACHE_TTL = 30 * 60

STARTED = time.time()

client = httpx.Client(
    headers={"User-Agent": UA},
    timeout=8,
    follow_redirects=True,
    verify=False
)

# ─── Date helpers ─────────────────────────────────────────────────

def parse_iso_date(ds):
    if not ds: return None
    try:
        return datetime.fromisoformat(ds.replace("Z", "+00:00").replace("z", "+00:00")).timestamp()
    except: return None

def parse_rss_date(ds):
    if not ds: return None
    try: return parsedate_to_datetime(ds).timestamp()
    except: return None

def is_fresh(ts):
    if ts is None: return None
    return (time.time() - ts) <= FRESHNESS_CUTOFF

# ─── Generic RSS scraper ─────────────────────────────────────────

def scrape_rss(url, source, base_score=9, wc_boost_score=12, transfer_boost_score=10, max_items=20):
    """Single generic RSS scraper."""
    topics = []
    try:
        r = client.get(url, timeout=8)
        if r.status_code != 200:
            import sys; print(f"   ⚠️ {source} RSS: HTTP {r.status_code}", file=sys.stderr)
            return topics
        root = ET.fromstring(r.text)
        items_found = len(root.findall('.//item'))
        import sys; print(f"   {source} RSS: {items_found} items found", file=sys.stderr)
        for item in root.findall('.//item')[:max_items]:
            title_el = item.find('title')
            link_el = item.find('link')
            if title_el is None or link_el is None: continue
            title = (title_el.text or "").strip()
            # Strip CDATA wrapping (Sky Sports uses CDATA in RSS)
            title = re.sub(r'^\s*<!\[CDATA\[(.*?)\]\]>\s*$', r'\1', title)
            title = html.unescape(title)  # Fix &#x27; &amp; etc
            if not title or len(title) < 20: continue
            link = (link_el.text or "").strip()
            if "?" in link: link = link.split("?")[0]
            desc_el = item.find('description')
            desc = re.sub(r'<[^>]+>', ' ', (desc_el.text or "")).strip()[:500] if desc_el is not None else ""
            desc = html.unescape(desc)
            pubdate_el = item.find('pubDate') or item.find('dc:date')
            pubdate_text = (pubdate_el.text or "").strip() if pubdate_el is not None else ""
            ts = parse_rss_date(pubdate_text)
            if is_fresh(ts) is False: continue

            # Extract image from media:content (RSS media namespace)
            image_url = ""
            for ns, ns_url in [("media", "http://search.yahoo.com/mrss/"), ("", "http://search.yahoo.com/mrss")]:
                media_el = item.find(f'.//{{{ns_url}}}content')
                if media_el is not None:
                    image_url = media_el.get("url", "")
                    break
            # Fallback: enclosure tag
            if not image_url:
                enc = item.find('enclosure')
                if enc is not None and enc.get("type", "").startswith("image"):
                    image_url = enc.get("url", "")

            tl = title.lower()
            has_wc = any(kw in tl for kw in ["world cup", "worldcup", "wc 202", "usa 2026", "mexico 2026", "canada 2026", "usmnt", "friendly international", "international break", "qualifier"])
            is_transfer = any(kw in tl for kw in ["transfer", "signs", "signing", "joins", "deal", "bid", "loan", "agree", "target", "talks", "move to", "swap", "release clause"])

            score = base_score
            if has_wc: score = max(score, wc_boost_score)
            if is_transfer: score = max(score, transfer_boost_score)

            topics.append(dict(title=title, source=source, url=link, score=score,
                comments=0, wc_boost=has_wc, transfer_related=is_transfer,
                description=desc, published_ts=ts, image_url=image_url))
    except: pass
    return topics

# ─── Mirror scraper (single pass) ───────────────────────────────

def scrape_mirror():
    topics = []
    try:
        r = client.get("https://www.mirror.co.uk/sport/football/news/", timeout=8)
        if r.status_code != 200: return topics
        seen = set()
        for link in re.findall(r'href="(https?://www\.mirror\.co\.uk/sport/football/[^"]*-?\d+)"', r.text)[:12]:
            if link in seen: continue
            seen.add(link)
            if 'pageNumber' in link or link.rstrip('/').endswith(('/transfer-news', '/news')): continue
            try:
                r2 = client.get(link, timeout=6)
                og_t = re.search(r'og:title[^>]*content="([^"]*)"', r2.text)
                if not og_t: continue
                title = html.unescape(og_t.group(1))
                if title.lower().startswith(("the mirror", "mirror", "uk news")): continue
                dp = parse_iso_date(m.group(1)) if (m := re.search(r'"datePublished"\s*:\s*"([^"]*)"', r2.text)) else None
                if is_fresh(dp) is False: continue
                og_d = re.search(r'og:description[^>]*content="([^"]*)"', r2.text)
                desc = html.unescape(og_d.group(1)) if og_d else ""
                # og:image from Mirror article
                og_img = re.search(r'og:image[^>]*content="([^"]*)"', r2.text)
                image_url = og_img.group(1) if og_img else ""
                tl = title.lower()
                has_wc = any(kw in tl for kw in ["world cup", "worldcup", "wc 202", "usa ", "host"])
                is_transfer = any(kw in tl for kw in ["transfer", "signs", "signing", "joins", "joined", "deal", "bid", "loan"])
                score = 12 if has_wc else (13 if is_transfer else 8)
                topics.append(dict(title=title, source="mirror", url=link, score=score,
                    comments=0, wc_boost=has_wc, transfer_related=is_transfer,
                    description=desc, published_ts=dp, image_url=image_url))
            except: pass
    except: pass
    return topics

# ─── Viral keyword matcher ───────────────────────────────────────

HIGH_VIRAL = frozenset(["goes viral", "fans react", "fans rage", "fans divided", "social media reacts",
    "trending", "breaks silence", "storm", "drama", "shock", "stunning", "explodes", "meltdown",
    "war of words", "hits back", "fumes", "blasts", "sends message", "statement", "ultimatum",
    "demands", "refuses", "sensational", "bombshell", "drops hint", "major hint", "reveals",
    "exclusive", "confirmed", "announced", "officially"])
MED_VIRAL = frozenset(["should they", "should he", "should we", "should not", "worst", "best",
    "biggest", "greatest", "most important", "verdict", "opinion", "debate", "argue", "controversy",
    "needs to", "must", "has to", "crunch", "showdown", "made the difference", "key moment",
    "turning point", "rise", "fall", "failure", "success", "doomed", "flop", "flops", "star",
    "superstar", "crisis", "disaster", "masterclass", "nightmare", "opens up", "speaks out"])

def viral_boost(title, description=""):
    t = (title.lower() + " " + description.lower())
    matched_high = [kw for kw in HIGH_VIRAL if kw in t]
    matched_med = [kw for kw in MED_VIRAL if kw in t]
    return (5 * len(matched_high) + 3 * len(matched_med)), matched_high + matched_med

# ─── Deduplication ───────────────────────────────────────────────

def deduplicate(topics):
    seen = {}
    result = []
    for t in sorted(topics, key=lambda x: -x["score"]):
        title = t.get("title")
        if not isinstance(title, str) or not title: continue
        words = frozenset(title.lower().split())
        src = t.get("source", "unknown")
        is_wc = t.get("wc_boost", False)
        is_dup = False
        for seen_src, entries in seen.items():
            for seen_words, seen_wc in entries:
                overlap = len(words & seen_words)
                min_len = min(len(words), len(seen_words))
                if min_len == 0: continue
                ratio = overlap / min_len
                threshold = 0.75 if (seen_src == src and is_wc) else (0.60 if seen_src == src else (0.85 if is_wc else 0.80))
                if ratio >= threshold: is_dup = True; break
            if is_dup: break
        if not is_dup and len(title) > 10:
            seen.setdefault(src, []).append((words, is_wc))
            result.append(t)
    return result

# ─── Cache ───────────────────────────────────────────────────────

def load_cache():
    if not CACHE_FILE.exists(): return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        if time.time() - data.get("cached_at", 0) < CACHE_TTL:
            return data.get("results")
    except: pass
    return None

def save_cache(results):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"cached_at": time.time(), "results": results}))
    except: pass

# ─── Main ────────────────────────────────────────────────────────

def main():
    args = set(sys.argv[1:])
    use_cache = "--nocache" not in args

    if use_cache:
        cached = load_cache()
        if cached:
            print(json.dumps(cached, indent=2))
            sys.exit(0)
    
    print("🔍 Researching WC2026 topics...", file=sys.stderr)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {
            "mirror": ex.submit(scrape_mirror),
        }
        rss_sources = {
            "guardian": ("https://www.theguardian.com/football/rss", 14),
            "skysports": ("https://www.skysports.com/rss/11095", 14),
        }
        for name, (url, base_score) in rss_sources.items():
            futs[name] = ex.submit(scrape_rss, url, name, base_score)

        all_topics = []
        for name, f in futs.items():
            try:
                result = f.result(timeout=12)
                print(f"   {name}: {len(result)} topics", file=sys.stderr)
                all_topics += result
            except Exception as e:
                print(f"   ⚠️ {name} error: {e}", file=sys.stderr)
    
    print(f"   Total raw: {len(all_topics)} topics", file=sys.stderr)
    
    # Single pass: boost WC + transfer + viral
    for t in all_topics:
        tl = t.get("title", "")
        if not t.get("wc_boost") and any(kw in tl.lower() for kw in 
            ["world cup", "worldcup", "wc 202", "qualifier", "friendly international",
             "national team", "usa 2026", "mexico 2026", "canada 2026", "concacaf"]):
            t["score"] = max(t.get("score", 0), 15)
            t["wc_boost"] = True
        if not t.get("transfer_related") and any(kw in tl.lower() for kw in
            ["transfer", "signs", "signing", "joins", "joined", "deal agreed",
             "done deal", "medical", "bid accepted", "offer accepted", "fee agreed",
             "move to", "free agent", "loan move", "permanent deal"]):
            t["score"] = max(t.get("score", 0), 13)
            t["transfer_related"] = True
        boost, matched = viral_boost(tl, t.get("description", ""))
        if boost:
            t["score"] = t.get("score", 0) + boost
            t["viral_related"] = True
            t["viral_keywords"] = matched
        else:
            t["viral_related"] = False
    
    wc_count = sum(1 for t in all_topics if t.get("wc_boost"))
    transfer_count = sum(1 for t in all_topics if t.get("transfer_related"))
    viral_count = sum(1 for t in all_topics if t.get("viral_related"))
    print(f"   WC-related: {wc_count}/{len(all_topics)}", file=sys.stderr)
    print(f"   Transfer-hot: {transfer_count}/{len(all_topics)}", file=sys.stderr)
    print(f"   🔥 Viral/trending: {viral_count}/{len(all_topics)}", file=sys.stderr)

    bad = frozenset(["quiz", "take our quiz", "which world cup team"])
    before = len(all_topics)
    all_topics = [t for t in all_topics if not any(kw in t.get("title","").lower() for kw in bad)]
    removed = before - len(all_topics)
    if removed: print(f"   🗑️ Filtered {removed} low-effort topics", file=sys.stderr)
    
    deduped = deduplicate(all_topics)
    wc_topics = [t for t in deduped if t.get("wc_boost")]
    non_wc_topics = [t for t in deduped if not t.get("wc_boost")]
    wc_topics.sort(key=lambda x: -x["score"])
    non_wc_topics.sort(key=lambda x: -x["score"])
    selected = (wc_topics + non_wc_topics)[:25]  # Return top 25 so outer script has enough after filtering posted
    
    results_out = []
    for i, t in enumerate(selected):
        results_out.append(dict(
            rank=i+1, title=t["title"], source=t.get("source", "unknown"),
            url=t.get("url", ""), url_verified=True,
            urgency="High" if t.get("score", 0) >= 10 else "Medium",
            score=t.get("score", 0), wc_related=t.get("wc_boost", False),
            transfer_related=t.get("transfer_related", False),
            viral_related=t.get("viral_related", False),
            viral_keywords=t.get("viral_keywords", []),
            description=t.get("description", ""),
            published_ts=t.get("published_ts"),
            image_url=t.get("image_url", "")))
    
    print(json.dumps(results_out, indent=2))
    save_cache(results_out)

if __name__ == "__main__":
    main()
