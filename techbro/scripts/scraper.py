#!/usr/bin/env python3
"""
scraper.py — 7 sources: Kompas, Detik, CNN Tech, CNN Lifestyle, CNBC Lifestyle, CNBC MyMoney, Liputan6 Bisnis
"""
import re
import json
import asyncio
import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse

WIB = timezone(timedelta(hours=7))
MAX_AGE_DAYS = 7
TOP_N = 1

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

# Scoring: viral potential for Indo AI/Tech audience
# TIER1 = viral gold (AI stories that affect people directly)
TIER1 = [
    "kecerdasan buatan", "chatgpt", "openai", "gemini", "claude",
    "anthropic", "llm", "agi", "gpt", "deepfake", "model ai",
    "phk", "hack", "bobol", "disinformasi", "scam ai", "tipu ai",
    # Gaya Hidup Produktif
    "produktif", "self-improvement", "time management", "goal setting",
    "work-life balance", "burnout", "prokrastinasi",
    # Life Hacks
    "life hack", "tips dan trik", "rahasia sukses", "trik jitu",
    # Entrepreneurship & UMKM
    "umkm", "wirausaha", "entrepreneur", "bisnis online", "reseller",
    "dropship", "modal kecil", "passive income", "franchise",
    # Psikologi & Mindset
    "psikolog", "kesehatan mental", "mental health", "depresi",
    "trauma", "self-care", "mindset", "kecemasan",
]
# TIER2 = cool tech (interesting but less viral)
TIER2 = [
    "chip", "semikonduktor", "quantum", "neural", "robot",
    "otomasi", "ai", "machine learning", "gpu",
    # Gaya Hidup Produktif
    "kebiasaan", "rutinitas", "disiplin", "motivasi", "inspirasi",
    "manajemen waktu", "fokus", "konsentrasi", "stres",
    # Life Hacks
    "shortcut", "cara mudah", "cara cepat",
    "efisien", "praktis", "otomatis",
    # Entrepreneurship & UMKM
    "omzet", "profit", "modal", "peluang usaha", "strategi bisnis",
    "marketing", "branding", "customer", "konsumen", "supply chain",
    # Psikologi & Mindset
    "kepribadian", "emosi", "konflik",
    "resilien", "adaptasi", "wellbeing", "terapi",
]
# TIER3 = generic tech (rarely scores high alone)
TIER3 = [
    "teknologi", "startup", "inovasi",
    # Gaya Hidup
    "karir", "sukses", "leadership", "pengembangan diri",
    # Entrepreneurship & UMKM
    "pengusaha", "peluang", "strategi", "investasi", "anggaran",
    # Psikologi & Mindset
    "anxiety", "overthinking", "healing", "toxic",
]
# PENALTY = product/promo patterns
PENALTY = [
    "spesifikasi", "harga promo", "diskon", "flash sale", "cashback",
    "unboxing", "hands-on", "pre-order", "giveaway",
    # Product launch/review signals
    "resmi:", "rilis:", "harga rp", "harga mulai",
    "earbuds", "earphone", "headphone", "headset",
    "anc ", "tws ", "audio lossless", "noise cancellation",
    "smartphone", "tablet flagship", "powerbank",
    "baterai mah", "fast charging", "nfc murah",
    "kamera mp", "refresh rate", "ip rating",
    "resmi rilis", "resmi diluncurkan", "bocoran spesifikasi",
    "review lengkap", "kelebihan dan kekurangan", "benchmark",
]
EXCLUDE = [
    "prediksi cuaca", "ramalan zodiak", "gosip", "skor akhir",
    "jadwal pertandingan", "transfer pemain",
]

# Pre-compiled regex — unique match per tier (not count)
_TIER1_SET = {k.lower() for k in TIER1}
_TIER2_SET = {k.lower() for k in TIER2}
_TIER3_SET = {k.lower() for k in TIER3}
_PENALTY_RE = re.compile("|".join(re.escape(k.lower()) for k in PENALTY))
_EXCL_RE    = re.compile("|".join(re.escape(k.lower()) for k in EXCLUDE))


def _unique_matches(text: str, keywords: set) -> int:
    """Count unique keyword matches (not repeat count). Word-boundary for short keywords."""
    count = 0
    for kw in keywords:
        if len(kw) <= 4:
            # Word boundary to avoid: agi→andalkan, ai→karena, etc.
            if re.search(r'\b' + re.escape(kw) + r'\b', text):
                count += 1
        elif kw in text:
            count += 1
    return count


def score_article(title: str, body: str) -> int:
    title_l = title.lower()
    body_l = body[:1000].lower()
    text = title_l + " " + body_l

    # Auto-zero: excluded topics
    if _EXCL_RE.search(text):
        return 0

    # Auto-zero: product promo/launch — 3+ penalty keywords = product article
    penalty_hits = len(set(_PENALTY_RE.findall(text)))
    if penalty_hits >= 3:
        return 0

    # Score by tier — title counts 3x (that's the hook)
    t1 = _unique_matches(title_l, _TIER1_SET) * 30 + _unique_matches(body_l, _TIER1_SET) * 15
    t2 = _unique_matches(title_l, _TIER2_SET) * 12 + _unique_matches(body_l, _TIER2_SET) * 5
    t3 = _unique_matches(title_l, _TIER3_SET) * 4  + _unique_matches(body_l, _TIER3_SET) * 2

    s = t1 + t2 + t3

    # Penalty: product review/promo patterns
    penalty_count = len(_PENALTY_RE.findall(text))
    s -= penalty_count * 12

    return max(0, min(s, 100))

def fix_image_url(url: str) -> str:
    if not url:
        return url
    if url.endswith(".jpe"):
        url += "g"
    parsed = urlparse(url)
    if "akcdn.detik" in parsed.netloc:
        base = urlunparse(parsed._replace(query=""))
        return base + "?w=1200&type=jpeg"
    # Liputan6: CDN hash depends on full path including filters — keep as-is
    if "akamaized.net" in parsed.netloc:
        return url
    # Kompas: strip crops/filters/watermark, keep original /data/photo/ path
    if "asset.kompas.com" in parsed.netloc:
        m = re.search(r"(/data/photo/.+)$", parsed.path)
        if m:
            return f"https://asset.kompas.com{m.group(1)}"
        path = re.sub(r"/\d+x\d+/", "/1200x675/", parsed.path)
        return urlunparse(parsed._replace(path=path, query=""))
    return url

def parse_date_from_url(url: str):
    # Kompas: /read/2026/06/29/...
    m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=WIB)
        except ValueError:
            pass
    # CNBC: /tech/20260630095442-37-746746/...
    m = re.search(r"/(\d{4})(\d{2})(\d{2})\d{6}-\d+-\d+/", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=WIB)
        except ValueError:
            pass
    # CNN: /teknologi/20260629092526-641-1374523/...
    m = re.search(r"/(\d{4})(\d{2})(\d{2})\d{6}-", url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=WIB)
        except ValueError:
            pass
    return None

def parse_date_from_html(soup):
    """Extract date from HTML meta tags or <time> tag."""
    # meta[name=publishdate] — Detik uses: "2026/06/29 18:17:26"
    tag = soup.find("meta", attrs={"name": "publishdate"})
    if tag and tag.get("content"):
        try:
            return datetime.strptime(tag["content"].strip(), "%Y/%m/%d %H:%M:%S").replace(tzinfo=WIB)
        except ValueError:
            pass
    # meta[property=article:published_time] — ISO format
    tag = soup.find("meta", property="article:published_time")
    if tag and tag.get("content"):
        try:
            from dateutil.parser import parse as dtparse
            return dtparse(tag["content"]).replace(tzinfo=WIB)
        except Exception:
            pass
    # <time datetime="">
    tag = soup.find("time")
    if tag and tag.get("datetime"):
        try:
            from dateutil.parser import parse as dtparse
            return dtparse(tag["datetime"]).replace(tzinfo=WIB)
        except Exception:
            pass
    return None

def is_fresh(dt) -> bool:
    """Fix 2+6: unknown date → reject. <= MAX_AGE_DAYS (include day 3)."""
    if dt is None:
        return False
    return (datetime.now(WIB) - dt).days <= MAX_AGE_DAYS

def get_og_image(soup) -> str:
    tag = soup.find("meta", property="og:image")
    if tag and tag.get("content"):
        return fix_image_url(str(tag["content"]).strip())
    return ""

def extract_body(soup, selectors: list[tuple]) -> str:
    """Try each (tag, pre-compiled cls) until one yields paragraphs."""
    for tag, cls in selectors:
        div = soup.find(tag, class_=cls) if cls else soup.find(tag)
        if not div:
            continue
        paras = [p.get_text(" ", strip=True) for p in div.find_all("p")
                 if len(p.get_text(strip=True)) > 40]
        if paras:
            return "\n\n".join(paras)
    return ""

# Fix 3: pre-compiled class patterns per source
_KOMPAS_SEL = [("div", re.compile("read__content")), ("div", re.compile("article-body"))]
_DETIK_SEL  = [("div", re.compile("detail__body-text")), ("div", re.compile("itp_bodycontent"))]
_CNN_SEL    = [("div", re.compile("detail-text")), ("div", re.compile("content-det"))]
_CNBC_SEL   = [("div", re.compile("detail-text")), ("div", re.compile("cnbc-body"))]
_LIP6_SEL   = [("div", re.compile("container-main")), ("div", re.compile("read-page__content"))]

async def scrape_article_async(url: str, client: httpx.AsyncClient, source: str) -> dict | None:
    try:
        r = await client.get(url, timeout=12)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        title_tag = soup.find("h1")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            return None
        dt = parse_date_from_url(url) or parse_date_from_html(soup)
        if not is_fresh(dt):
            return None
        image = get_og_image(soup)
        if source == "kompas":
            body = extract_body(soup, _KOMPAS_SEL)
        elif source == "detik":
            body = extract_body(soup, _DETIK_SEL)
        elif source == "cnn":
            body = extract_body(soup, _CNN_SEL)
        elif source == "cnn_lifestyle":
            body = extract_body(soup, _CNN_SEL)
        elif source == "cnbc_lifestyle":
            body = extract_body(soup, _CNBC_SEL)
        elif source == "cnbc_mymoney":
            body = extract_body(soup, _CNBC_SEL)
        elif source == "liputan6_bisnis":
            body = extract_body(soup, _LIP6_SEL)
        else:
            return None
        if not body:
            return None
        return {"title": title, "date": dt, "image": image, "body": body, "url": url, "source": source}
    except Exception:
        return None

async def get_links_kompas(client: httpx.AsyncClient) -> list[str]:
    r = await client.get("https://tekno.kompas.com/", timeout=10)
    soup = BeautifulSoup(r.text, "html.parser")
    links = set()
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if "tekno.kompas.com/read/" in href:
            links.add(href.split("?")[0])
    return list(links)[:50]

async def get_links_detik(client: httpx.AsyncClient) -> list[str]:
    """Detik inet — RSS feed (80+ articles) + HTML fallback."""
    links = set()
    # Primary: RSS feed
    try:
        r = await client.get("https://inet.detik.com/rss", timeout=12)
        for m in re.finditer(r"<link>([^<]+)</link>", r.text):
            url = m.group(1).strip()
            if re.match(r"https://inet\.detik\.com/[a-z]+/d-\d+/", url):
                links.add(url.split("?")[0])
    except Exception:
        pass
    # Fallback: HTML listing
    if len(links) < 10:
        try:
            r = await client.get("https://inet.detik.com/", timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = str(a["href"])
                if re.match(r"https://inet\.detik\.com/[a-z]+/d-\d+/", href):
                    links.add(href.split("?")[0])
        except Exception:
            pass
    return list(links)[:50]

async def get_links_cnn(client: httpx.AsyncClient) -> list[str]:
    """CNN Indonesia teknologi — RSS feed (100 articles) + HTML fallback."""
    links = set()
    # Primary: RSS feed
    try:
        r = await client.get("https://www.cnnindonesia.com/teknologi/rss", timeout=12)
        for m in re.finditer(r"<link>([^<]+)</link>", r.text):
            url = m.group(1).strip()
            if re.match(r"https://www\.cnnindonesia\.com/teknologi/\d+", url):
                links.add(url.split("?")[0])
    except Exception:
        pass
    # Fallback: HTML listing
    if len(links) < 10:
        try:
            r = await client.get("https://www.cnnindonesia.com/teknologi", timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = str(a["href"])
                if re.match(r"https://www\.cnnindonesia\.com/teknologi/\d+", href):
                    links.add(href.split("?")[0])
        except Exception:
            pass
    return list(links)[:50]

async def get_links_cnn_lifestyle(client: httpx.AsyncClient) -> list[str]:
    """CNN Gaya Hidup — RSS feed (100 articles)."""
    links = set()
    try:
        r = await client.get("https://www.cnnindonesia.com/gaya-hidup/rss", timeout=12)
        for m in re.finditer(r"<link>([^<]+)</link>", r.text):
            url = m.group(1).strip()
            if re.match(r"https://www\.cnnindonesia\.com/gaya-hidup/\d+", url):
                links.add(url.split("?")[0])
    except Exception:
        pass
    return list(links)[:50]

async def get_links_cnbc_lifestyle(client: httpx.AsyncClient) -> list[str]:
    """CNBC Indonesia lifestyle — RSS <guid> tags (100 articles)."""
    links = set()
    try:
        r = await client.get("https://www.cnbcindonesia.com/lifestyle/rss", timeout=12)
        for m in re.finditer(r"<guid>([^<]+)</guid>", r.text):
            url = m.group(1).strip()
            if re.match(r"https://www\.cnbcindonesia\.com/lifestyle/\d+", url):
                links.add(url.split("?")[0])
    except Exception:
        pass
    return list(links)[:50]

async def get_links_cnbc_mymoney(client: httpx.AsyncClient) -> list[str]:
    """CNBC Indonesia MyMoney — RSS (entrepreneurship, UMKM, keuangan)."""
    links = set()
    try:
        r = await client.get("https://www.cnbcindonesia.com/mymoney/rss", timeout=12)
        for m in re.finditer(r"<guid>([^<]+)</guid>", r.text):
            url = m.group(1).strip()
            if re.match(r"https://www\.cnbcindonesia\.com/mymoney/\d+", url):
                links.add(url.split("?")[0])
    except Exception:
        pass
    return list(links)[:50]

async def get_links_liputan6_bisnis(client: httpx.AsyncClient) -> list[str]:
    """Liputan6 bisnis — sitemap XML (entrepreneurship, UMKM, ekonomi)."""
    links = set()
    try:
        r = await client.get("https://www.liputan6.com/bisnis/sitemap.xml", timeout=12)
        for m in re.finditer(r"<loc>(https://www\.liputan6\.com/bisnis/read/\d+/[^<]+)</loc>", r.text):
            links.add(m.group(1).strip())
    except Exception:
        pass
    return list(links)[:50]

async def scrape_all_async(top_n: int = TOP_N) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        link_tasks = await asyncio.gather(
            get_links_kompas(client),
            get_links_detik(client),
            get_links_cnn(client),
            get_links_cnn_lifestyle(client),
            get_links_cnbc_lifestyle(client),
            get_links_cnbc_mymoney(client),
            get_links_liputan6_bisnis(client),
            return_exceptions=True,
        )
        all_tasks = []
        for src, links in zip(["kompas", "detik", "cnn", "cnn_lifestyle", "cnbc_lifestyle", "cnbc_mymoney", "liputan6_bisnis"], link_tasks):
            if not isinstance(links, list) or not links:
                continue
            for url in links:
                all_tasks.append(scrape_article_async(url, client, src))

        results = await asyncio.gather(*all_tasks, return_exceptions=True)

    articles: list[dict] = []
    seen: set[str] = set()
    for art in results:
        if not isinstance(art, dict):
            continue
        if art["url"] in seen:
            continue
        seen.add(art["url"])
        art["score"] = score_article(art["title"], art["body"])
        if art["score"] > 25:
            articles.append(art)

    articles.sort(key=lambda x: x["score"], reverse=True)
    return articles[:top_n]

def scrape_all(top_n: int = TOP_N) -> list[dict]:
    return asyncio.run(scrape_all_async(top_n))


if __name__ == "__main__":
    import sys, time
    n = int(sys.argv[1]) if len(sys.argv) > 1 else TOP_N
    t0 = time.time()
    results = scrape_all(n)
    elapsed = time.time() - t0
    src_label = {
        "kompas": "Kompas Tekno", "detik": "Detik Inet", "cnn": "CNN Indonesia",
        "cnn_lifestyle": "CNN Gaya Hidup", "cnbc_lifestyle": "CNBC Lifestyle",
        "cnbc_mymoney": "CNBC MyMoney", "liputan6_bisnis": "Liputan6 Bisnis",
    }
    for i, art in enumerate(results, 1):
        dt_str = art["date"].strftime("%Y-%m-%d %H:%M WIB") if art["date"] else "unknown date"
        print(f"\n{'='*60}")
        print(f"#{i} [{src_label.get(art['source'], art['source'])}] score={art['score']}")
        print(f"Title : {art['title']}")
        print(f"Date  : {dt_str}")
        print(f"Image : {art['image']}")
        print(f"URL   : {art['url']}")
        print(f"Body  : {len(art['body'])} chars")
        print(f"Preview:\n{art['body'][:400]}...")
    if not results:
        print("No articles found.")
    print(f"\nDone in {elapsed:.1f}s")
