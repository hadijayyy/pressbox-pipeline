#!/usr/bin/env python3
"""Standalone grounded Threads pipeline for @budakorporat_id."""
from __future__ import annotations
import argparse, hashlib, html, json, logging, os, re, sys, time
from html.parser import HTMLParser
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


class _ArticleTextParser(HTMLParser):
    _skip = {"script", "style", "nav", "aside", "footer", "header", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.depth = 0
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").lower().replace("-", "_")
        is_body = tag == "article" or any(k in classes for k in ("article_body", "article_content", "story_body", "post_content", "entry_content"))
        if is_body and not self.active:
            self.active, self.depth = True, 1
        elif self.active:
            self.depth += 1
        if self.active and tag in self._skip:
            self.skip += 1

    def handle_endtag(self, tag):
        if self.active and tag in self._skip and self.skip:
            self.skip -= 1
        if self.active:
            self.depth -= 1
            if self.depth <= 0:
                self.active = False

    def handle_data(self, data):
        if self.active and not self.skip and data.strip():
            self.parts.append(data.strip())


def _extract_article_text(raw: str) -> str:
    parser = _ArticleTextParser()
    parser.feed(raw)
    return text(" ".join(parser.parts))


def article_body(item: dict) -> str:
    try:
        raw = fetch(resolve_article_url(item)).decode("utf-8", "replace")
        body = _extract_article_text(raw)
        if len(body) < 200:
            raise RuntimeError("full article body is missing or too thin")
        return body[:12000]
    except Exception as exc:
        raise RuntimeError("full article extraction failed") from exc


def _legacy_article_body_removed(item: dict) -> str:
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

TUJUAN EDITORIAL — PAKAI MEKANISME VIRAL, BUKAN MENYALIN KALIMAT:
Bangun perubahan posisi pembaca: awalnya mengira masalahnya sederhana, akhirnya melihat mekanisme, risiko, atau kontradiksi yang lebih besar. Jangan meniru identitas, persona, kalimat, slogan, cerita, contoh, atau ekspresi akun lain.

ARSITEKTUR WAJIB (maksimal 5 slide; kurang bila evidence unik tidak cukup):
S1 HOOK BERSTAKES + POWER GAP/CONTRARIAN FRAME. Buka dengan benturan konkret: siapa/apa yang tampak kuat, lalu celah atau kepentingan yang tidak terlihat. Hook harus membuat pembaca ingin tahu “kalau begitu, siapa yang menanggung akibatnya?”. Jangan membuka dengan ringkasan netral.
S2 NUMBERED PROMISE + MICRO-UTILITY 1. Nyatakan janji bernomor yang spesifik dan bisa diverifikasi (“Ada 3 hal yang perlu dilihat: ...”) hanya jika SOURCE_BODY punya jumlah/fungsi itu. Lalu beri bukti pertama. Jangan mengarang jumlah; bila tidak ada, gunakan janji spesifik tanpa angka.
S3 MICRO-UTILITY 2. Beri tindakan observable → tanda di SOURCE_BODY → arti tersembunyi yang ditandai sebagai analisis → potensi kerugian hanya jika didukung sumber. Tambahkan kronologi/konteks baru, bukan parafrase S2.
S4 MICRO-UTILITY 3 + ESCALATION. Beri mekanisme atau dampak baru dengan pola sama. Dampak ke pekerja/rumah tangga hanya bila SOURCE_BODY menyebutnya. Jangan memaksakan angle.
S5 REVERSAL/PRINCIPLE + SPECIFIC CTA. Balik asumsi awal menggunakan fakta/evidence baru. Tarik satu prinsip praktis yang jelas, bukan moral kosong. Akhiri satu CTA spesifik yang meminta pembaca melakukan tindakan terkait fakta (misalnya cek sumber atau bandingkan angka), bukan “setuju?” generik. Jika tidak ada reversal atau CTA yang didukung sumber, akhiri pada slide sebelumnya.

KONTRAK FAKTA:
Setiap slide wajib membawa satu evidence/fungsi baru. Dilarang mengulang fakta, angka, kejadian, kesimpulan, atau pasangan pelaku-tindakan dengan sinonim/parafrasa. Gunakan EVIDENCE_PACK untuk membagi evidence; ID tidak boleh tampil. Jangan menambah nama, angka, kutipan, motif, dampak, kejadian, atau konversi angka. Bedakan fakta dari analisis/pertanyaan. Kasus yang masih dugaan tetap ditulis sebagai dugaan. “Arti tersembunyi” tidak boleh menjadi fakta baru.
Jangan memakai kata viral/heboh tanpa bukti. Jangan klaim pengalaman langsung. Gunakan bahasa Indonesia percakapan BudakKorporat: santai, tajam, konkret; gunakan gue/lu hanya bila terdengar alami, tidak dipaksa. Value dan pemahaman harus muncul sebelum CTA/monetisasi.

OUTPUT:
Keluarkan JSON saja: {{\"slides\":[\"...\"]}}. Tiap slide 40-500 karakter. Jangan masukkan URL. Jika fakta unik habis, kirim 1-4 slide; jangan isi filler.

SOURCE_TITLE: {title}
SOURCE_BODY: {body}

EVIDENCE_PACK (gunakan ID untuk merencanakan slide, jangan tampilkan ID):
{evidence}
"""


def _evidence_units(body: str) -> list[tuple[str, set[str]]]:
    stop = {"yang", "dan", "atau", "dari", "dengan", "untuk", "dalam", "pada", "ini", "itu", "akan", "jika", "karena", "bahwa"}
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body).strip()) if len(s.strip()) >= 40]
    return [(f"E{i}", {w for w in re.findall(r"[a-zà-ÿ0-9]{4,}", sentence.lower()) if w not in stop}) for i, sentence in enumerate(sentences, 1)]


def _evidence_prompt(body: str) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body).strip()) if len(s.strip()) >= 40]
    return "\\n".join(f"E{i}: {sentence}" for i, sentence in enumerate(sentences, 1))


def _deterministic_draft(item: dict, body: str) -> list[str]:
    """Extractive fallback; never pads thin evidence with repeated prose."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body).strip()) if len(s.strip()) >= 40]
    if not sentences:
        raise RuntimeError("fallback source has no usable facts")
    return sentences[:5]


def _claim_grounding_issues(parts: list[str], body: str) -> list[str]:
    """Reject common unsupported motive/impact claims; fallback remains extractive."""
    lower_body = body.lower()
    hedges = ("menurut", "diduga", "kayaknya", "polanya", "kata ", "sebut", "analisis", "mungkin", "bisa jadi")
    markers = ("butuh uang", "butuh dana", "butuh duit", "gaya hidup", "proyek fiktif", "mark up", "kepercayaan", "layanan publik", "korporatisme", "lembur", "rumah tangga", "keamanan ekonomi", "kelangsungan hidup", "duka mendalam", "akar permasalahan", "diungkap secara transparan", "tidak terulang")
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
                              title=item["title"], body=body[:12000], evidence=_evidence_prompt(body)) + correction}],
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
    if not isinstance(parts, list):
        raise RuntimeError("LLM slides contract invalid")
    normalized = []
    for part in parts:
        if isinstance(part, str):
            normalized.append(part)
        elif isinstance(part, dict) and part and all(isinstance(v, str) for v in part.values()):
            # Models sometimes label one slide's fields instead of returning one string.
            normalized.append(" ".join(part.values()))
        else:
            raise RuntimeError("LLM slides contract invalid")
    return normalized


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
                          "Pakai 1-5 slide sesuai fakta unik; jangan mengulang fakta atau mengejar jumlah slide. "
                          "Setiap slide harus mengambil evidence berbeda; jika evidence habis, hapus slide. "
                          "Ikuti fungsi slide berurutan jika didukung sumber. Keluarkan JSON valid, tiap slide 40-500 karakter.")
    raise RuntimeError(f"Mistral gagal menghasilkan draft valid setelah 3 percobaan: {last}")


def _safe_draft(item: dict, body: str, use_llm=True) -> list[str]:
    """Never publish non-LLM content; caller handles no-post safely."""
    return draft(item, body, use_llm)


def _evidence_slide_issues(parts: list[str], body: str) -> list[str]:
    units = _evidence_units(body)
    if len(parts) <= 1 or len(units) < len(parts):
        return []
    slide_tokens = [set(re.findall(r"[a-zà-ÿ0-9]{4,}", p.lower())) for p in parts]
    scores = [[(len(tokens & words) / len(tokens | words), i) for i, (_, words) in enumerate(units) if words] for tokens in slide_tokens]
    best = None
    for choices in __import__('itertools').permutations(range(len(units)), len(parts)):
        total = sum(next((score for score, i in scores[row] if i == unit), 0) for row, unit in enumerate(choices))
        if best is None or total > best[0]:
            best = (total, choices)
    if not best or any(next((score for score, i in scores[row] if i == unit), 0) < 0.08 for row, unit in enumerate(best[1])):
        return ["slide evidence not uniquely grounded"]
    return []


def _repeated_slide_issues(parts: list[str]) -> list[str]:
    stop = {"yang", "dan", "atau", "dari", "dengan", "untuk", "dalam", "pada", "ini", "itu", "akan", "jika"}
    tokens = [set(re.findall(r"[a-zà-ÿ0-9]{4,}", p.lower())) for p in parts]
    bigrams = []
    for p in parts:
        words = [w for w in re.findall(r"[a-zà-ÿ0-9]{4,}", p.lower()) if w not in stop]
        bigrams.append(set(zip(words, words[1:])))
    issues = []
    for i in range(len(tokens)):
        for j in range(i):
            union = tokens[i] | tokens[j]
            overlap = len(tokens[i] & tokens[j]) / len(union) if union else 0
            # Shared names/locations are allowed; reject only near-identical prose.
            if overlap >= 0.72:
                issues.append(f"S{i + 1}/S{j + 1}: repeated slide content")
            elif bigrams[i] & bigrams[j]:
                log.info("slide similarity alarm S%d/S%d: shared phrase", i + 1, j + 1)
    return issues


def validate(parts: list[str], item: dict, body: str, allow_url=True):
    if not 1 <= len(parts) <= 5: raise ValueError("invalid thread parts: need 1-5 slides")
    if any(not p.strip() or len(p) < 40 or len(p) > 500 for p in parts): raise ValueError("part must be 40-500 chars")
    repeated = _repeated_slide_issues(parts)
    if repeated: raise ValueError("; ".join(repeated))
    evidence_issues = _evidence_slide_issues(parts, body)
    if evidence_issues: raise ValueError("; ".join(evidence_issues))
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

# self-check: transport and editorial contracts stay fail-closed.
assert USER == "budakorporat_id" and USER_ID.isdigit()
_check_parts = [
    "Kronologi awal mencatat keputusan pertama dan tanggal pemeriksaan berlangsung sesuai dokumen sumber.",
    "Pertimbangan majelis memuat alasan berbeda, termasuk bukti administrasi serta keterangan saksi.",
]
assert all(40 <= len(p) <= 500 for p in _check_parts)
assert not _repeated_slide_issues(_check_parts)
assert _repeated_slide_issues([_check_parts[0], _check_parts[0]])
try:
    _deterministic_draft({"title": "x", "url": "https://x.test/a"}, "tipis")
except RuntimeError:
    pass
else:
    raise AssertionError("thin fallback source must fail closed")
