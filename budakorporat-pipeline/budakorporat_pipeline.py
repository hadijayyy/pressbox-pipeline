#!/usr/bin/env python3
"""Standalone grounded Threads pipeline for @budakorporat_id."""
from __future__ import annotations
import argparse, hashlib, html, json, logging, os, re, sys, time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state.json"
LOG = ROOT / "pipeline.log"
TOKEN_FILE = Path.home() / ".hermes" / "threads_token_budakorporat.json"
USER = "budakorporat_id"
USER_ID = "27516379201355016"
FEEDS = [
    "https://www.kompas.com/rss",
    "https://rss.detik.com/index.php/detikcom",
    "https://www.cnnindonesia.com/nasional/rss",
    "https://www.antaranews.com/rss/politik.xml",
    "https://rss.tempo.co/",
]
POLITICAL_RE = re.compile(r"politik|pemerintah|presiden|dpr|parlemen|menteri|partai|pemilu|pilkada|kpk|koalisi|istana|hukum|korupsi", re.I)
DRAMA_RE = re.compile(r"kontrovers|konflik|ribut|sengketa|kritik|tuding|bantah|protes|skandal|heboh|viral|geger|polemi|pecat|gugat|ditangkap|tersangka", re.I)
UA = "budakorporat-pipeline/1.0"
MAX_AGE = timedelta(hours=12)
DATE_FIELDS = ("pubDate", "published", "updated", "date")
log = logging.getLogger("budakorporat")


def fetch(url: str, timeout=20) -> bytes:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read(1_000_000)


def resolve_article_url(item: dict) -> str:
    """Allow only direct publisher URLs for body and hero extraction."""
    url = item["url"]
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() in {"news.google.com", "www.news.google.com"}:
        raise RuntimeError("source URL is not a direct publisher article")
    return url


def text(x: str) -> str:
    x = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", x or "", flags=re.I | re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x))).strip()


def items(feed: str) -> list[dict]:
    root = ET.fromstring(fetch(feed))
    out = []
    for item in root.findall(".//item"):
        title = text(item.findtext("title"))
        url = text(item.findtext("link"))
        desc = text(item.findtext("description"))
        published = next((text(item.findtext(name)) for name in DATE_FIELDS if item.find(name) is not None), "")
        if title and url:
            out.append({"title": title, "url": url, "description": desc, "published": published, "source": urlparse(url).netloc})
    return out


def load_json(path: Path, default):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return default


def save_json(path: Path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def _published_at(value: str):
    if not value: return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try: dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError: return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _candidate_score(item: dict) -> int:
    hay = f"{item['title']} {item.get('description', '')}"
    return (4 if DRAMA_RE.search(hay) else 0) + (3 if POLITICAL_RE.search(hay) else 0) + (2 if item["source"] == "news.google.com" else 0) + min(len(item.get("description", "")) // 100, 2)


def collect() -> list[dict]:
    seen, out, now = set(), [], datetime.now(timezone.utc)
    title_hits = {}
    for feed in FEEDS:
        try:
            for item in items(feed):
                published = _published_at(item.get("published", ""))
                if not published or published < now - MAX_AGE or published > now + timedelta(minutes=10): continue
                hay = f"{item['title']} {item.get('description', '')}"
                if not (POLITICAL_RE.search(hay) and DRAMA_RE.search(hay)): continue
                key = hashlib.sha256(item["url"].encode()).hexdigest()
                if key not in seen:
                    seen.add(key); title_hits[re.sub(r"\W+", " ", item["title"].lower()).strip()] = title_hits.get(re.sub(r"\W+", " ", item["title"].lower()).strip(), 0) + 1; item["key"] = key; out.append(item)
        except Exception as exc:
            log.warning("feed failed %s: %s", feed, exc)
    for item in out:
        normalized = re.sub(r"\W+", " ", item["title"].lower()).strip()
        item["score"] = _candidate_score(item) + min(title_hits[normalized], 3)
    return sorted(out, key=lambda x: (x["score"], x.get("published", "")), reverse=True)


def article_body(item: dict) -> str:
    try:
        raw = fetch(item["url"]).decode("utf-8", "replace")
        metas = re.findall(r'<meta\b[^>]*\b(?:name|property)=["\'](?:description|og:description)["\'][^>]*\bcontent=["\']([^"\']+)', raw, re.I)
        metas += re.findall(r'<meta\b[^>]*\bcontent=["\']([^"\']+)["\'][^>]*\b(?:name|property)=["\'](?:description|og:description)["\']', raw, re.I)
        body = text(max(metas, key=len) if metas else raw)
        if len(body) < 200:
            body = text(f"{body} {item.get('description', '')} {item['title']}")
        return body[:12000]
    except Exception:
        return text(item["description"])


def article_image(item: dict) -> str:
    """Get article's declared hero image; never use arbitrary related images."""
    try:
        raw = fetch(item["url"]).decode("utf-8", "replace")
        patterns = [
            r'<meta\b[^>]*\bproperty=["\']og:image["\'][^>]*\bcontent=["\']([^"\']+)',
            r'<meta\b[^>]*\bcontent=["\']([^"\']+)["\'][^>]*\bproperty=["\']og:image["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, raw, re.I)
            if match and match.group(1).startswith(("https://", "http://")):
                return html.unescape(match.group(1))
    except Exception:
        pass
    return ""


def _load_local_env():
    path = Path.home() / ".hermes" / ".env"
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    except OSError:
        pass


_load_local_env()
LLM_URL = os.environ.get("BUDAKORPORAT_LLM_URL", "http://43.157.200.187:20128/v1/chat/completions").strip()
LLM_MODEL = os.environ.get("BUDAKORPORAT_LLM_MODEL", "cx/gpt-5.6-luna").strip()
LLM_KEY = os.environ.get("BUDAKORPORAT_LLM_KEY", os.environ.get("HERMES_CUSTOM_43_157_200_187_20128_API_KEY", "")).strip()

PROMPT = """Kamu penulis komentar sosial Indonesia untuk akun budakorporat_id.
Tulis 5-8 slide Threads orisinal dari sumber di bawah.
Buat 5-8 slide dengan isi yang sepenuhnya bersumber dari SOURCE_BODY.
Dampak ke pekerja/rumah tangga hanya boleh ditulis jika SOURCE_BODY menyebutnya; jangan memaksakan angle.
Bedakan fakta dan opini. Opini harus ditandai sebagai analisis atau pertanyaan, bukan fakta.
Jangan menambah nama, angka, kutipan, motif, dampak, atau kejadian. Jangan mengubah angka atau melakukan konversi.
Jangan memakai kata viral/heboh jika SOURCE_BODY tidak memberi bukti pendukung.
Jangan meniru identitas, persona, kalimat, slogan, cerita, atau ekspresi @arfzulfikar maupun akun lain.
Keluarkan JSON saja: {{\"slides\":[\"...\"]}}. Tiap slide 120-500 karakter. Jangan masukkan URL; pipeline menambahkannya.
Jika sumber terlalu tipis untuk 5 slide, ulangi fakta sumber dengan sudut penjelasan berbeda; jangan mengarang.

SOURCE_TITLE: {title}
SOURCE_BODY: {body}
"""


def _deterministic_draft(item: dict, body: str) -> list[str]:
    """Safe slot-preserving fallback: repeat only source-backed evidence."""
    title = re.sub(r"\\s+", " ", item["title"]).strip()
    evidence = re.sub(r"\s+", " ", body).strip()[:140]
    if len(evidence) < 40:
        raise RuntimeError("fallback source evidence too thin")
    seeds = [
        f"Sumber membahas: {title}. Berikut fakta yang tersedia tanpa menambah nama, angka, kutipan, atau kejadian baru: {evidence}",
        f"Konteks yang bisa dipastikan dari sumber hanya ini: {evidence} Judulnya menyebut {title}. Bagian di luar informasi tersebut tidak dipakai sebagai fakta.",
        f"Apa yang diketahui? Sumber menyatakan: {evidence} Karena itu, analisis di thread ini dibatasi pada informasi tersebut dan tidak mengisi celah dengan dugaan atau dampak yang tidak disebutkan.",
        f"Konflik atau kontroversi dalam berita ini harus dibaca dari fakta sumber, bukan asumsi tambahan. Fakta yang tersedia: {evidence} Judul terkait: {title}.",
        f"Batas bukti penting dijaga. Sumber hanya menjadi dasar untuk pernyataan berikut: {evidence} Jika ada pertanyaan lanjutan tentang {title}, jawabannya belum boleh dianggap fakta sebelum ada sumber tambahan.",
    ]
    return [p if len(p) >= 120 else p + " Informasi lain tidak ditambahkan." * 3 for p in seeds]


def _claim_grounding_issues(parts: list[str], body: str) -> list[str]:
    """Reject common unsupported motive/impact claims; fallback remains extractive."""
    lower_body = body.lower()
    hedges = ("menurut", "diduga", "kayaknya", "polanya", "kata ", "sebut", "analisis", "mungkin", "bisa jadi")
    markers = ("butuh uang", "butuh dana", "butuh duit", "gaya hidup", "proyek fiktif", "mark up", "kepercayaan", "layanan publik", "korporatisme", "lembur", "rumah tangga", "keamanan ekonomi", "kelangsungan hidup")
    issues = []
    for i, part in enumerate(parts, 1):
        low = part.lower()
        for marker in markers:
            if marker in low and marker not in lower_body and not any(h in low for h in hedges):
                issues.append(f"S{i}: unsupported claim '{marker}'")
    return issues


def _llm_draft(item: dict, body: str, correction: str = "") -> list[str]:
    if not (LLM_URL and LLM_MODEL and LLM_KEY):
        raise RuntimeError("LLM config incomplete; set BUDAKORPORAT_LLM_URL, _MODEL, _KEY")
    payload = json.dumps({"model": LLM_MODEL, "temperature": 0.4,
                          "messages": [{"role": "user", "content": PROMPT.format(
                              title=item["title"], body=body[:12000]) + correction}],
                          "response_format": {"type": "json_object"}}).encode()
    req = Request(LLM_URL, data=payload, headers={"Authorization": f"Bearer {LLM_KEY}",
                   "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urlopen(req, timeout=45) as response:
            data = json.loads(response.read(200_000))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"LLM request failed: {type(exc).__name__}") from exc
    try:
        raw = data["choices"][0]["message"]["content"]
        result = json.loads(raw) if isinstance(raw, str) else raw
        parts = result["slides"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("LLM response contract invalid") from exc
    if not isinstance(parts, list) or not all(isinstance(p, str) for p in parts):
        raise RuntimeError("LLM slides contract invalid")
    return parts


def draft(item: dict, body: str, use_llm=True) -> list[str]:
    if not use_llm:
        return _deterministic_draft(item, body)
    last = None
    correction = ""
    for attempt in range(3):
        try:
            parts = _llm_draft(item, body, correction)
            validate(parts, item, body, allow_url=False)
            return parts
        except (RuntimeError, ValueError) as exc:
            last = exc
            log.warning("LLM draft rejected attempt %d: %s", attempt + 1, exc)
            correction = ("\n\nPERBAIKI OUTPUT SEBELUMNYA. Alasan penolakan: "
                          f"{type(exc).__name__}. Tulis ulang dari SOURCE_BODY saja. "
                          "Keluarkan JSON valid dengan 5-8 slide, tiap slide 120-500 karakter.")
    raise RuntimeError(f"Mistral gagal menghasilkan draft valid setelah 3 percobaan: {last}")


def _safe_draft(item: dict, body: str, use_llm=True) -> list[str]:
    """Never publish non-LLM content; caller handles no-post safely."""
    return draft(item, body, use_llm)


def validate(parts: list[str], item: dict, body: str, allow_url=True):
    if not 5 <= len(parts) <= 8: raise ValueError("invalid thread parts: need 5-8 slides")
    if any(not p.strip() or len(p) < 120 or len(p) > 500 for p in parts): raise ValueError("part must be 120-500 chars")
    joined = "\n".join(parts)
    if allow_url and item["url"] not in joined: raise ValueError("source URL missing")
    if not allow_url and re.search(r"https?://", joined, re.I): raise ValueError("LLM URL leak")
    if len(body) < 200: raise ValueError("source body too thin")
    issues = _claim_grounding_issues(parts, body)
    if issues: raise ValueError("; ".join(issues))


def token():
    data = load_json(TOKEN_FILE, {})
    value = data.get("access_token") or data.get("token")
    uid = str(data.get("user_id") or USER_ID)
    if not value: raise RuntimeError(f"missing local credential: {TOKEN_FILE}")
    if uid != USER_ID: raise RuntimeError("credential user_id is not budakorporat_id")
    return value, uid


def publish(parts: list[str], dry: bool, image_url: str = ""):
    if dry: return [{"text": p, "post_id": "DRY_RUN", **({"image_url": image_url} if i == 0 and image_url else {})} for i, p in enumerate(parts)]
    sys.path.insert(0, "/home/ubuntu/pressbox-pipeline")
    from threads_poster import ThreadsPoster
    access, uid = token()
    return [r.__dict__ for r in ThreadsPoster(access, uid).post_thread(parts, image_urls=[image_url] + [None] * (len(parts) - 1))]


def run(dry=False, use_llm=True):
    state = load_json(STATE, {"posted": []})
    posted = set(state.get("posted", []))
    candidates = [x for x in collect() if x["key"] not in posted]
    if not candidates: raise RuntimeError("no unposted source candidate")
    last = None
    for item in candidates[:5]:
        try:
            resolve_article_url(item)
            body = article_body(item)
            image_url = article_image(item)
            if not image_url:
                raise RuntimeError("article hero image missing")
            parts = _safe_draft(item, body, use_llm)
            validate(parts, item, body, allow_url=False)
            break
        except (RuntimeError, ValueError) as exc:
            last = exc
            log.warning("candidate rejected; trying next source: %s", exc)
    else:
        log.error("NO_POST_LLM_DRAFT_INVALID: %s", last)
        print(json.dumps({"target": USER, "dry_run": dry, "status": "NO_POST_LLM_DRAFT_INVALID"}))
        return
    if not image_url: raise RuntimeError("article hero image missing")
    try:
        result = publish(parts, dry, image_url)
    except Exception as exc:
        log.exception("publish failed; no state committed")
        print(json.dumps({"target": USER, "dry_run": dry, "status": "NO_POST_PUBLISH_ERROR", "error": str(exc)}, ensure_ascii=False))
        return
    if not dry:
        state.setdefault("posted", []).append(item["key"]); state["last"] = {"url": item["url"], "result": result, "ts": int(time.time())}; save_json(STATE, state)
    print(json.dumps({"target": USER, "dry_run": dry, "posts": result}, ensure_ascii=False))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--no-llm", action="store_true", help="disable only for local debugging")
    logging.basicConfig(filename=LOG, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = ap.parse_args()
    try: run(args.dry_run, not args.no_llm)
    except Exception as exc: log.exception("pipeline failed"); print(f"ERROR: {exc}", file=sys.stderr); raise SystemExit(1)
else:
    pass

# self-check: transport contract stays fail-closed.
assert USER == "budakorporat_id" and USER_ID.isdigit()
assert all(len(p) <= 500 for p in draft({"title":"x","url":"https://x.test/a"}, "source body " * 50, use_llm=False))
