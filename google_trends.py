#!/usr/bin/env python3
"""Fetch Google Trends football topics. Returns list of trending queries.
No API key needed — scrapes Google Trends RSS/daily feed."""
import json, time, sys, re, os
from urllib.request import urlopen, Request
from datetime import datetime, timedelta

TRENDS_URL = "https://trends.google.com/trending/rss?geo=GB"
# Daily trends API (no category filter — filtered by keyword matching instead)
DAILY_URL = "https://trends.google.com/trends/api/dailytrends?hl=en-GB&geo=GB&ns=15"

CACHE_FILE = os.path.expanduser("~/.hermes/pressbox-pipeline/.trends_cache.json")
CACHE_TTL = 1800  # 30 min

def fetch_google_trends():
    """Get trending football queries from Google Trends UK.
    Returns list of (query, score) tuples sorted by score desc."""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*"
    }
    
    try:
        # Method 1: Try daily trends API (structured, more reliable)
        req = Request(DAILY_URL, headers=headers)
        raw = urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
        
        # Google returns: )]}'\n{json}
        if raw.startswith(")]}'"):
            raw = raw[5:].strip()
        data = json.loads(raw)
        
        trends = []
        for day in data.get("default", {}).get("trendingSearchesDays", []):
            for search in day.get("trendingSearches", []):
                title = search.get("title", {}).get("query", "")
                traffic = search.get("formattedTraffic", "")
                articles = search.get("articles", [])
                news_count = len(articles)
                score = _parse_traffic(traffic) + (news_count * 2)
                if title:
                    trends.append((title, score, news_count))
        
        if trends:
            trends.sort(key=lambda t: -t[1])
            return _to_output(trends)
    except Exception as e:
        print(f"[trends] Daily API failed: {e}", file=sys.stderr)
    
    # Method 2: RSS fallback
    try:
        req = Request(TRENDS_URL, headers=headers)
        raw = urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
        
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw.encode('utf-8'))
        # Extract the actual ht namespace from the document
        ns_match2 = re.search(r'xmlns:ht="([^"]+)"', raw)
        ht_ns = ns_match2.group(1) if ns_match2 else "https://trends.google.com/trending/rss"
        
        trends = []
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                title = title_el.text.strip()
            else:
                continue
            
            # Traffic estimate — use full namespace
            traffic_el = item.find("{%s}approx_traffic" % ht_ns)
            traffic_str = traffic_el.text.strip() if traffic_el is not None and traffic_el.text else "0"
            score = _parse_traffic(traffic_str)
            
            # News articles linked
            news_count = len(item.findall("{%s}news_item" % ht_ns))
            score += news_count * 3
            
            if not title:
                continue
            trends.append((title, score, news_count))
        
        return _to_output(trends)
    
    except Exception as e:
        print(f"[trends] Error: {e}", file=sys.stderr)
        return _to_output([])

def _parse_traffic(traffic_str):
    """Convert '10K+' to 10000, '1M+' to 1000000."""
    if not traffic_str:
        return 0
    traffic_str = traffic_str.replace("+", "").replace(",", "").strip()
    multiplier = 1
    if "M" in traffic_str:
        multiplier = 1000000
        traffic_str = traffic_str.replace("M", "")
    elif "K" in traffic_str:
        multiplier = 1000
        traffic_str = traffic_str.replace("K", "")
    try:
        return int(float(traffic_str) * multiplier)
    except ValueError:
        return 0

def _to_output(trends):
    """Format: list of {query, score, source}"""
    return [{"query": q, "score": s, "articles": n, "source": "google_trends"} 
            for q, s, n in trends]


# ── Direct run ──
if __name__ == "__main__":
    # Check cache
    now = time.time()
    if os.path.exists(CACHE_FILE):
        mtime = os.path.getmtime(CACHE_FILE)
        if now - mtime < CACHE_TTL:
            with open(CACHE_FILE) as f:
                cached = json.load(f)
            print(json.dumps(cached, indent=2))
            sys.exit(0)
    
    result = fetch_google_trends()
    with open(CACHE_FILE, "w") as f:
        json.dump(result, f)
    
    print(json.dumps(result, indent=2))
