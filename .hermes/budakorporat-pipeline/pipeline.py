#!/usr/bin/env python3
"""Budakorporat: source-grounded Indonesian political Threads pipeline."""
import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import struct
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE = Path(__file__).parent
STATE = BASE / "state.sqlite3"
PREPARED = BASE / "prepared.json"
TOKEN_FILE = Path("/home/ubuntu/.hermes/budakorporat_token.json")
TARGET_ACCOUNT = "budakorporat_id"
TARGET_USER_ID = "27516379201355016"
POSTER_DIR = Path("/home/ubuntu/.hermes/scripts")
sys.path.insert(0, str(POSTER_DIR))

WIB = timezone(timedelta(hours=7))
MAX_AGE_HOURS = 24
MIN_BODY_CHARS = 1500
MIN_SENTENCES = 10
SLIDES = 7
CONTENT_SLIDES = 6
SLIDE_LIMIT = 500
LLM_TIMEOUT = 120
LLM_RETRIES = 1
COOLDOWN_MINUTES = 15
TOP_N = 10
MAX_REVISION_CYCLES = 1
IMAGE_MIN_WIDTH = 800
IMAGE_MIN_HEIGHT = 450
SOURCES = {
    "media_indonesia_politik": "https://mediaindonesia.com/politik-dan-hukum",
    "cnn_nasional": "https://www.cnnindonesia.com/nasional/rss",
    "antara_politik": "https://www.antaranews.com/rss/politik.xml",
}
HOT_SIGNAL_SOURCES = {
    "google_news_politik": "https://news.google.com/rss/search?q=politik+Indonesia+when%3A24h&hl=id&gl=ID&ceid=ID%3Aid",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Budakorporat/1.0)"}
POLITICAL = ("presiden", "pemerintah", "dpr", "kpk", "kejagung", "polri", "mahkamah", "menteri", "gubernur", "bupati", "walikota", "partai", "kebijakan", "uu", "putusan", "sidang", "korupsi", "suap", "anggaran", "pilkada")
REJECT = ("bola", "liga", "konser", "artis", "selebriti", "ramalan", "zodiak", "foto:", "video:", "lifestyle")
HIGH_IMPACT = ("korupsi", "suap", "gratifikasi", "kpk", "kejagung", "sidang", "tersangka", "vonis", "anggaran", "kebijakan", "putusan", "dpr", "mahkamah")
GENERIC_CTA = ("apa pendapat lu", "bagaimana menurut lu", "setuju gak", "gimana menurut kalian")


def now():
    return datetime.now(WIB)


def log(msg):
    print(f"{now():%F %T} {msg}", flush=True)


def open_db(dry_run=False):
    if dry_run:
        return sqlite3.connect(f"file:{STATE}?mode=ro", uri=True) if STATE.exists() else sqlite3.connect(":memory:")
    db = sqlite3.connect(STATE)
    db.execute("CREATE TABLE IF NOT EXISTS posts (url TEXT PRIMARY KEY, title TEXT NOT NULL, payload_hash TEXT NOT NULL, slides_json TEXT, root_id TEXT, permalink TEXT, posted_at TEXT NOT NULL, complete INTEGER NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS chain_posts (url TEXT NOT NULL, slide_idx INTEGER NOT NULL, post_id TEXT NOT NULL, PRIMARY KEY(url, slide_idx), FOREIGN KEY(url) REFERENCES posts(url))")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, started_at TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL)")
    columns = {row[1] for row in db.execute("PRAGMA table_info(posts)")}
    if "slides_json" not in columns:
        db.execute("ALTER TABLE posts ADD COLUMN slides_json TEXT")
    db.commit()
    return db


def clean(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def parse_feed(source, url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        log(f"SOURCE_FAIL {source}: {type(e).__name__}")
        return []
    out = []
    for item in root.findall(".//item")[:25]:
        title, link = clean(item.findtext("title")), clean(item.findtext("link"))
        pub = item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date")
        try:
            published = parsedate_to_datetime(pub).astimezone(WIB) if pub else None
        except Exception:
            published = None
        if title and link:
            out.append({"source": source, "title": title, "url": link, "published": published})
    log(f"SOURCE {source}: {len(out)}")
    return out


def parse_hot_signals(source, url):
    items = parse_feed(source, url)
    signals = []
    for item in items:
        title = re.sub(r"\s+-\s+[^-]{2,40}$", "", item["title"]).strip()
        signals.append({**item, "title": title})
    return signals


def parse_media_indonesia(source, url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log(f"SOURCE_FAIL {source}: {type(e).__name__}")
        return []
    urls = []
    for href in re.findall(r'href=["\']([^"\']+)', r.text, re.I):
        article_url = urljoin(url, html.unescape(href))
        if re.search(r"^https://mediaindonesia\.com/politik-dan-hukum/\d+/", article_url) and article_url not in urls:
            urls.append(article_url)
    out = [{"source": source, "title": "", "url": article_url, "published": None} for article_url in urls[:25]]
    log(f"SOURCE {source}: {len(out)}")
    return out


def extract(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception:
        return None
    page = r.text
    title = clean(re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', page, re.I).group(1)) if re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', page, re.I) else ""
    image_m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page, re.I)
    image = urljoin(url, html.unescape(image_m.group(1))) if image_m else ""
    image_candidates = [image]
    image_candidates += [urljoin(url, html.unescape(x)) for x in re.findall(r'<img\b[^>]+src=["\']([^"\']+)', page, re.I)]
    for srcset in re.findall(r'\bsrcset=["\']([^"\']+)', page, re.I):
        image_candidates += [urljoin(url, item.split()[0]) for item in srcset.split(",") if item.strip()]
    image_candidates = list(dict.fromkeys(x for x in image_candidates if x))
    paragraphs = [clean(x) for x in re.findall(r"<p\b[^>]*>(.*?)</p>", page, re.I | re.S)]
    body = "\n".join(x for x in paragraphs if len(x) >= 50 and "advert" not in x.lower())
    if len(body) < MIN_BODY_CHARS:
        json_bodies = []
        for raw in re.findall(r'"articleBody"\s*:\s*"((?:\\\\.|[^"\\\\])*)"', page, re.I):
            try:
                json_bodies.append(clean(json.loads('"' + raw + '"')))
            except json.JSONDecodeError:
                pass
        if json_bodies:
            body = max(json_bodies, key=len)
    published = None
    for pattern in (r'"datePublished"\s*:\s*"([^"]+)', r'article:published_time[^>]+content=["\']([^"\']+)', r'<time[^>]+datetime=["\']([^"\']+)'):
        m = re.search(pattern, page, re.I)
        if m:
            try:
                published = datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).astimezone(WIB)
                break
            except ValueError:
                pass
    return {"title": title, "body": body, "image_url": image, "image_candidates": image_candidates, "published": published, "url": r.url}


def image_width(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">I", data[16:20])[0]
    if data[:2] != b"\xff\xd8":
        return 0
    pos = 2
    while pos + 9 <= len(data):
        if data[pos] != 0xff:
            pos += 1
            continue
        while pos < len(data) and data[pos] == 0xff:
            pos += 1
        marker = data[pos]
        pos += 1
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf) and pos + 7 <= len(data):
            return struct.unpack(">H", data[pos + 5:pos + 7])[0]
        if pos + 2 > len(data):
            break
        size = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += size
    return 0


def image_dimensions(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">I", data[16:20])[0], struct.unpack(">I", data[20:24])[0]
    if data[:2] != b"\xff\xd8":
        return (0, 0)
    pos = 2
    while pos + 9 <= len(data):
        if data[pos] != 0xff:
            pos += 1
            continue
        while pos < len(data) and data[pos] == 0xff:
            pos += 1
        marker = data[pos]
        pos += 1
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf) and pos + 7 <= len(data):
            return struct.unpack(">H", data[pos + 5:pos + 7])[0], struct.unpack(">H", data[pos + 3:pos + 5])[0]
        if pos + 2 > len(data):
            break
        size = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += size
    return (0, 0)


def valid_image(url):
    if not url:
        return False
    try:
        r = requests.get(url, headers={**HEADERS, "Range": "bytes=0-65535"}, timeout=15)
        content_type = r.headers.get("content-type", "").lower()
        width, height = image_dimensions(r.content)
        return r.ok and (content_type.startswith("image/") or content_type == "application/octet-stream") and width >= IMAGE_MIN_WIDTH and height >= IMAGE_MIN_HEIGHT
    except Exception:
        return False


def select_valid_image(article):
    for candidate in article.get("image_candidates", [article.get("image_url", "")]):
        if valid_image(candidate):
            return candidate
    return ""


def score(article):
    text = (article["title"] + " " + article["body"][:4000]).lower()
    if any(x in text for x in REJECT) or not any(x in text for x in POLITICAL):
        return -999
    score = sum(12 for x in HIGH_IMPACT if x in text)
    if re.search(r"\b(?:rp\s*)?\d[\d.,]*\s*(?:juta|miliar|triliun|persen|%)", text):
        score += 12
    if any(x in text for x in ("prabowo", "kpk", "kejagung", "dpr", "mahkamah")):
        score += 10
    return score


def _title_overlap(left, right):
    words = lambda text: set(re.findall(r"[\w]+", text.lower()))
    a, b = words(left), words(right)
    return len(a & b) / len(a | b) if a and b else 0


def rank_candidates(candidates, hot_titles=()):
    """Cluster same-story articles; rank hot, fresh, independently corroborated stories."""
    clusters = []
    for article in candidates:
        cluster = next((group for group in clusters if any(_title_overlap(article["title"], other["title"]) >= 0.45 for other in group)), None)
        if cluster is None:
            clusters.append([article])
        else:
            cluster.append(article)
    ranked = []
    for group in clusters:
        publishers = {item.get("publisher") or item.get("source") for item in group}
        base = max(score(item) for item in group)
        item = dict(max(group, key=lambda article: (score(article), article["published"])))
        item["cluster_size"] = len(group)
        item["independent_publishers"] = len(publishers)
        item["hot_signal"] = any(_title_overlap(item["title"], title) >= 0.35 for title in hot_titles)
        age_hours = max(0, (now() - item["published"]).total_seconds() / 3600)
        item["hot_score"] = base + len(group) * 8 + len(publishers) * 10 + max(0, 24 - age_hours) + (20 if item["hot_signal"] else 0)
        ranked.append(item)
    return sorted(ranked, key=lambda item: (-item["hot_score"], item["url"]))


def sentence_count(text):
    return len([x for x in re.split(r"(?<=[.!?])\s+", text) if len(x.strip()) > 25])


def article_ok(article):
    age = now() - article["published"]
    if age.total_seconds() < -300 or age > timedelta(hours=MAX_AGE_HOURS):
        return False, "STALE"
    if len(article["body"]) < MIN_BODY_CHARS or sentence_count(article["body"]) < MIN_SENTENCES:
        return False, "THIN_BODY"
    image_url = select_valid_image(article)
    if not image_url:
        return False, "IMAGE_INVALID"
    article["image_url"] = image_url
    return True, ""


def fact_packet(body):
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", body.strip()) if x.strip()]
    return "\n".join(f"[F{i:03d}] {sentence}" for i, sentence in enumerate(sentences, 1))


def llm_key():
    """Read pipeline key in cron shells without printing its value."""
    key = os.environ.get("HERMES_CUSTOM_43_157_200_187_20128_API_KEY")
    if key:
        return key
    try:
        for line in Path("/home/ubuntu/.hermes/.env").read_text().splitlines():
            if line.startswith("HERMES_CUSTOM_43_157_200_187_20128_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def llm(prompt):
    key = llm_key()
    if not key:
        raise RuntimeError("CONFIG_FAILURE: HERMES_CUSTOM_43_157_200_187_20128_API_KEY missing")
    payload = {"model": "Terra", "messages": [{"role": "system", "content": "Output JSON only."}, {"role": "user", "content": prompt}], "temperature": 0.35, "max_tokens": 1800}
    for attempt in range(LLM_RETRIES + 1):
        try:
            r = requests.post("http://127.0.0.1:20128/v1/chat/completions", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=payload, timeout=LLM_TIMEOUT)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
            return json.loads(content)
        except requests.exceptions.ReadTimeout:
            if attempt >= LLM_RETRIES:
                raise
            log(f"LLM_TIMEOUT retry {attempt + 1}/{LLM_RETRIES}")
            time.sleep(2)


def write(article):
    prompt = f'''Buat JSON persis {{"status":"PUBLISH|REJECT","slides":["..."],"coverage":{{"critical_ids":["F001"],"slides":{{"1":["F001"]}}}},"reason":"..."}}.
Akun: Budakorporat, Threads politik Indonesia. Nada tajam, natural, casual; bukan mengarang motif atau dampak.
Gunakan HANYA FAKTA PACKET. Bila bukti tidak cukup untuk 6 slide, status REJECT.
PUBLISH: tepat 6 slide total, tiap slide 2 kalimat faktual, <= {SLIDE_LIMIT} karakter, tanpa URL, tanpa bullet, tanpa hashtag. Semua fakta bernilai harus tersebar di 6 slide; jangan mengulang fakta kecuali untuk menjelaskan konteks. Isi coverage dengan ID fakta packet yang dipakai. critical_ids hanya fakta paling penting; semua critical_ids wajib dipetakan ke satu atau lebih slide. Susunan wajib: S1 buka dengan konflik atau benturan fakta paling kuat, bukan agenda/prosedur; jika body memuat dua angka atau versi yang bertentangan, prioritaskan benturan itu. Jika body memuat pola total-status versus status-berbeda, gunakan pembuka literal: total sudah A tetapi baru B yang berstatus berbeda, lalu sebut pengecualian bernama dan alasan literalnya. Arc berikutnya: kasus/angka, tindakan resmi sebelumnya, langkah berikutnya, pengecualian atau kontradiksi, bukti yang belum lengkap, lalu pertanyaan penilaian spesifik. Jangan paksa pola ini bila fakta packet tidak mendukung. S2 jelaskan inti dakwaan/peristiwa. S3-S4 lanjutkan urutan fakta dan sebab-akibat yang LITERAL ada di bukti; jangan isi slide dengan agenda sidang, susunan majelis, atau daftar nama kecuali penting untuk konflik. S5 tampilkan bantahan, perbedaan angka, atau bukti yang dipersoalkan. S6 wajib memuat fakta yang belum selesai lalu satu pertanyaan tajam berbasis fakta itu. Pilih fokus STAKE bila body menyebut pihak terdampak, kerugian, hak, akses publik, atau uang negara; ACCOUNTABILITY bila body menyebut keputusan pejabat/lembaga; EVIDENCE bila body hanya memuat status, angka, atau bukti yang belum jelas. Jika body tidak memuat dampak publik, jangan memaksakan dampak; gunakan konsekuensi hukum atau bukti yang LITERAL ada. Jangan gunakan pertanyaan opini generik; pertanyaan harus meminta penilaian atas inti konflik, status, bukti, atau keputusan yang disebut di body. Jangan tambah nama, angka, tanggal, tuduhan, prediksi, konsekuensi, motif, atau dampak baru.
FAKTA PACKET:\n{fact_packet(article["body"])}'''
    data = llm(prompt)
    if data.get("status") != "PUBLISH":
        return None, "WRITER_REJECT"
    slides = data.get("slides")
    if not isinstance(slides, list) or len(slides) != CONTENT_SLIDES or not all(isinstance(x, str) for x in slides):
        return None, "CONTRACT_FAILURE"
    coverage = data.get("coverage")
    if not isinstance(coverage, dict) or not isinstance(coverage.get("critical_ids"), list) or not isinstance(coverage.get("slides"), dict):
        return None, "COVERAGE_CONTRACT_FAILURE"
    fact_ids = set(re.findall(r"\[(F\d+)\]", fact_packet(article["body"])))
    mapped = {str(key): set(value) for key, value in coverage["slides"].items() if isinstance(value, list)}
    if any(item not in fact_ids for item in coverage["critical_ids"]) or any(item not in fact_ids for values in mapped.values() for item in values):
        return None, "COVERAGE_UNKNOWN_FACT"
    if any(item not in {fact_id for values in mapped.values() for fact_id in values} for item in coverage["critical_ids"]):
        return None, "COVERAGE_MISSING_CRITICAL"
    article["coverage"] = coverage
    return [clean(x) for x in slides], ""


def repair(article, slides, reason):
    prompt = f'''Perbaiki 6 slide JSON berikut setelah audit: {reason}
Output JSON persis {{"status":"PUBLISH|REJECT","slides":["..."],"reason":"..."}}.
Gunakan hanya FAKTA PACKET. Pertahankan setiap qualifier sumber secara literal: diduga, disebut, menurut, berpotensi, keberatan, dan bentuk ketidakpastian lain tidak boleh diubah menjadi kepastian. Jangan menambah objek tersirat seperti uang, motif, dampak, sebab-akibat, atau status hukum. S6 tetap satu pertanyaan berbasis fakta yang belum selesai tanpa premis baru. Tepat 6 slide, dua kalimat faktual per slide, <= {SLIDE_LIMIT} karakter, tanpa URL.
FAKTA PACKET:\n{fact_packet(article["body"])}
SLIDE LAMA:\n{json.dumps(slides[:CONTENT_SLIDES], ensure_ascii=False)}'''
    data = llm(prompt)
    repaired = data.get("slides") if data.get("status") == "PUBLISH" else None
    if not isinstance(repaired, list) or len(repaired) != CONTENT_SLIDES or not all(isinstance(x, str) for x in repaired):
        return None
    return [clean(x) for x in repaired]


def _source_fallback_slides(article):
    """Build conservative slides from complete source sentences only."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", article["body"].strip()) if len(s.strip()) >= 25]
    if len(sentences) < 11:
        return None
    slides = [" ".join(sentences[i:i + 2]) for i in range(0, 10, 2)]
    slides.append(f"{sentences[10]} Apa yang perlu diperhatikan dari fakta ini?")
    if any(len(slide) > SLIDE_LIMIT for slide in slides):
        return None
    return slides + [f"Sumber: {article['url']}"]


def _validated_source_fallback(article, failed_reason):
    """Relax style only; retain final deterministic and semantic gates."""
    slides = _source_fallback_slides(article)
    if not slides:
        return None, failed_reason
    ok, reason = validate(slides, article)
    if ok:
        ok, reason = verify(slides[:CONTENT_SLIDES], article)
    if not ok:
        log(f"FALLBACK_BLOCKED {reason}")
        return None, f"FALLBACK_{reason}"
    log(f"FALLBACK_SOURCE_ONLY after {failed_reason}")
    return slides, ""


def draft_article(article):
    slides, reason = write(article)
    if not slides:
        return _validated_source_fallback(article, reason)
    slides.append(f"Sumber: {article['url']}")
    for revision in range(MAX_REVISION_CYCLES + 1):
        ok, reason = validate(slides, article)
        if ok:
            ok, reason = verify(slides[:CONTENT_SLIDES], article)
        if ok:
            return slides, ""
        if revision >= MAX_REVISION_CYCLES:
            return _validated_source_fallback(article, f"VERIFY: {reason}" if reason else "CONTRACT_FAILURE")
        repaired = repair(article, slides, reason)
        if not repaired:
            return _validated_source_fallback(article, "REPAIR_REJECT")
        slides = repaired + [f"Sumber: {article['url']}"]
    return _validated_source_fallback(article, "CONTRACT_FAILURE")


def validate(slides, article):
    body = re.sub(r"\s+", " ", article["body"]).lower()
    body_numbers = number_tokens(body)
    if len(slides) != SLIDES:
        return False, "SOURCE_SLIDE_CONTRACT"
    for i, slide in enumerate(slides[:CONTENT_SLIDES], 1):
        if not slide or len(slide) > SLIDE_LIMIT or "http" in slide.lower():
            return False, f"SLIDE_{i}_CONTRACT"
        if sentence_count(slide) < (1 if i >= 5 else 2):
            return False, f"SLIDE_{i}_THIN"
        for n in number_tokens(slide):
            if n not in body_numbers:
                return False, f"SLIDE_{i}_UNVERIFIED_NUMBER"
    if "?" not in slides[CONTENT_SLIDES - 1]:
        return False, "S6_NO_QUESTION"
    if any(phrase in slides[CONTENT_SLIDES - 1].lower() for phrase in GENERIC_CTA):
        return False, "S6_GENERIC_CTA"
    if slides[-1] != f"Sumber: {article['url']}" or len(slides[-1]) > SLIDE_LIMIT:
        return False, "SOURCE_SLIDE_CONTRACT"
    return True, ""


def number_tokens(text):
    """Canonical digits only: Rp30, Rp 30, and 30 refer to same source number."""
    return {re.sub(r"[^0-9]", "", n) for n in re.findall(r"(?<!\w)(?:rp\s*)?\d[\d.,]*(?!\w)", text.lower())}


def needs_semantic_verify(slides, article):
    """Spend verifier call only when draft asserts a risky, non-literal claim."""
    if len(slides) >= CONTENT_SLIDES and "?" in slides[CONTENT_SLIDES - 1]:
        return True
    return bool(re.search(r"\b(?:pasti|menyebabkan|berdampak|akibatnya|motif|tujuan|karena itu|dirugikan|melindungi|menguntungkan|mengorbankan)\b", " ".join(slides).lower()))


def verify(slides, article):
    prompt = f'''Audit factual. Body satu-satunya bukti. Output JSON persis {{"verdict":"PASS|FAIL","reason":"..."}}. FAIL jika slide menambah fakta, nama, angka, sebab-akibat, dampak, status hukum, atau prediksi yang tidak didukung literal body. Style bukan alasan gagal.
BODY:\n{fact_packet(article["body"])}\nSLIDES:\n{json.dumps(slides, ensure_ascii=False)}'''
    data = llm(prompt)
    return data.get("verdict") == "PASS", data.get("reason", "VERIFY_FAIL")


def cooldown(db, url):
    row = db.execute("SELECT posted_at FROM posts WHERE url=? AND complete=1", (url,)).fetchone()
    if row:
        return False
    row = db.execute("SELECT posted_at FROM posts WHERE complete=1 ORDER BY posted_at DESC LIMIT 1").fetchone()
    return not row or now() - datetime.fromisoformat(row[0]) >= timedelta(minutes=COOLDOWN_MINUTES)


def load_partial(db, article, slides):
    """Return prior accepted chain state only if payload and post IDs are complete enough to resume."""
    payload_hash = hashlib.sha256(json.dumps(slides, ensure_ascii=False).encode()).hexdigest()
    row = db.execute("SELECT payload_hash, slides_json, complete FROM posts WHERE url=?", (article["url"],)).fetchone()
    if not row or row[2]:
        return []
    if row[0] != payload_hash or row[1] != json.dumps(slides, ensure_ascii=False):
        raise RuntimeError("PUBLISH_AMBIGUOUS")
    records = db.execute("SELECT slide_idx, post_id FROM chain_posts WHERE url=? ORDER BY slide_idx", (article["url"],)).fetchall()
    if not records or len(records) >= SLIDES or [idx for idx, _ in records] != list(range(len(records))) or not all(pid for _, pid in records):
        raise RuntimeError("PUBLISH_AMBIGUOUS")
    return records


def checkpoint_post(db, article, slides, index, post_id, permalink=""):
    payload = json.dumps(slides, ensure_ascii=False)
    payload_hash = hashlib.sha256(payload.encode()).hexdigest()
    db.execute("INSERT OR IGNORE INTO posts(url,title,payload_hash,slides_json,root_id,permalink,posted_at,complete) VALUES(?,?,?,?,?,?,?,0)", (article["url"], article["title"], payload_hash, payload, post_id if index == 0 else None, permalink if index == 0 else "", now().isoformat()))
    db.execute("INSERT OR REPLACE INTO chain_posts(url,slide_idx,post_id) VALUES(?,?,?)", (article["url"], index, post_id))
    db.commit()


def publish(slides, article, db):
    from threads_poster import ThreadsPoster
    try:
        token = json.loads(TOKEN_FILE.read_text())
        access_token = token["access_token"]
        user_id = str(token["user_id"])
    except (OSError, KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"CONFIG_FAILURE THREADS_TOKEN: {type(e).__name__}") from e
    try:
        response = requests.get(
            "https://graph.threads.net/v1.0/me",
            params={"fields": "id,username", "access_token": access_token},
            timeout=20,
        )
        response.raise_for_status()
        identity = response.json()
    except (requests.RequestException, ValueError) as e:
        raise RuntimeError(f"CONFIG_FAILURE THREADS_IDENTITY: {type(e).__name__}") from e
    if str(identity.get("id")) != TARGET_USER_ID or identity.get("username") != TARGET_ACCOUNT or user_id != TARGET_USER_ID:
        raise RuntimeError("CONFIG_FAILURE THREADS_ACCOUNT_MISMATCH")
    poster = ThreadsPoster(access_token, TARGET_USER_ID)
    prior = load_partial(db, article, slides)
    last_id = prior[-1][1] if prior else None
    root_id = prior[0][1] if prior else None
    for index in range(len(prior), SLIDES):
        post_id = poster.post_single(slides[index], reply_to_id=last_id, image_url=article["image_url"] if index == 0 else None, image_fallback=False, fetch_permalink=False)
        checkpoint_post(db, article, slides, index, post_id)
        if index == 0:
            root_id = post_id
        last_id = post_id
    if not root_id:
        raise RuntimeError("PUBLISH_AMBIGUOUS")
    permalink = poster.get_permalink(root_id)
    db.execute("UPDATE posts SET complete=1, root_id=?, permalink=?, posted_at=? WHERE url=?", (root_id, permalink, now().isoformat(), article["url"]))
    db.commit()
    return permalink


def save_prepared(article, slides):
    stored_article = dict(article)
    stored_article["published"] = article["published"].isoformat()
    payload = {"article": stored_article, "slides": slides, "prepared_at": now().isoformat()}
    tmp = PREPARED.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PREPARED)


def load_prepared():
    try:
        payload = json.loads(PREPARED.read_text(encoding="utf-8"))
        prepared_at = datetime.fromisoformat(payload["prepared_at"])
        if now() - prepared_at > timedelta(hours=24):
            return None
        article, slides = payload["article"], payload["slides"]
        if not isinstance(article, dict) or not isinstance(slides, list):
            return None
        article["published"] = datetime.fromisoformat(article["published"])
        if not isinstance(article.get("published"), datetime):
            return None
        return article, slides
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def run_prepared():
    prepared = load_prepared()
    if not prepared:
        print("NO_PREPARED_DRAFT")
        return 0
    article, slides = prepared
    ok, reason = validate(slides, article)
    if not ok:
        print(f"PREPARED_INVALID {reason}")
        return 1
    ok, reason = verify(slides[:CONTENT_SLIDES], article)
    if not ok:
        print(f"PREPARED_INVALID VERIFY: {reason}")
        return 1
    db = open_db()
    try:
        if not cooldown(db, article["url"]):
            print("PREPARED_DUPLICATE_OR_COOLDOWN")
            return 0
        permalink = publish(slides, article, db)
    finally:
        db.close()
    PREPARED.unlink(missing_ok=True)
    print(f"PUBLISHED {permalink}")
    return 0


def run(dry_run, prepare=False):
    db = open_db(dry_run)
    run_id = hashlib.sha256(f"{now().isoformat()}-{os.getpid()}".encode()).hexdigest()[:12]
    outcome, detail = "TRANSIENT_FAILURE", "unhandled"
    try:
        hot_titles = []
        for source, feed in HOT_SIGNAL_SOURCES.items():
            hot_titles.extend(item["title"] for item in parse_hot_signals(source, feed))
        candidates = []
        for source, feed in SOURCES.items():
            parser = parse_media_indonesia if source == "media_indonesia_politik" else parse_feed
            for item in parser(source, feed):
                extracted = extract(item["url"])
                if not extracted:
                    continue
                item.update({key: value for key, value in extracted.items() if value or key != "published"})
                if not item["published"]:
                    log(f"REJECT MISSING_TIMESTAMP: {item['title'][:90]}")
                    continue
                ok, reason = article_ok(item)
                if ok:
                    candidates.append(item)
                else:
                    log(f"REJECT {reason}: {item['title'][:90]}")
        ranked = rank_candidates(candidates, hot_titles)
        for article in ranked[:TOP_N]:
            if not cooldown(db, article["url"]):
                log("SKIPPED_COOLDOWN")
                continue
            slides, reason = draft_article(article)
            if not slides:
                log(f"REJECT {reason}")
                continue
            if dry_run:
                print(json.dumps({"terminal_outcome":"DRY_RUN_OK", "title":article["title"], "source_url":article["url"], "slides":slides}, ensure_ascii=False, indent=2))
                outcome, detail = "DRY_RUN_OK", article["url"]
                return 0
            if prepare:
                save_prepared(article, slides)
                print(f"PREPARED {article['url']}")
                outcome, detail = "PREPARED", article["url"]
                return 0
            permalink = publish(slides, article, db)
            print(f"PUBLISHED {permalink}")
            outcome, detail = "PUBLISHED", permalink
            return 0
        print("NO_SAFE_CANDIDATE")
        outcome, detail = "NO_SAFE_CANDIDATE", "all candidates rejected"
        return 0
    except requests.RequestException as e:
        print(f"TRANSIENT_FAILURE {type(e).__name__}")
        outcome, detail = "TRANSIENT_FAILURE", type(e).__name__
        return 1
    except RuntimeError as e:
        print(str(e))
        outcome, detail = "CONFIG_FAILURE" if str(e).startswith("CONFIG_FAILURE") else "CONTRACT_FAILURE", str(e)
        return 1
    finally:
        if not dry_run:
            db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?)", (run_id, now().isoformat(), outcome, detail))
            db.commit()
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--prepare-next", action="store_true")
    p.add_argument("--publish-prepared", action="store_true")
    args = p.parse_args()
    if args.publish_prepared and args.prepare_next:
        p.error("--publish-prepared and --prepare-next are mutually exclusive")
    raise SystemExit(run_prepared() if args.publish_prepared else run(args.dry_run, args.prepare_next))
