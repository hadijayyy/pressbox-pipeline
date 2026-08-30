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
    "https://www.cnnindonesia.com/nasional/rss",
    "https://www.cnnindonesia.com/internasional/rss",
    "https://www.cnbcindonesia.com/news/rss",
    "https://www.cnbcindonesia.com/market/rss",
    "https://nasional.sindonews.com/rss",
    "https://ekbis.sindonews.com/rss",
    "https://feed.liputan6.com/rss/news",
    "https://feed.liputan6.com/rss/bisnis",
    "https://sindikasi.okezone.com/index.php/rss/1/RSS2.0",
    "https://sindikasi.okezone.com/index.php/rss/11/RSS2.0",
    "https://www.inews.id/feed",
    "https://katadata.co.id/rss",
    "https://www.antaranews.com/rss/terkini.xml",
    "https://www.antaranews.com/rss/top-news.xml",
]
POLITICAL_RE = re.compile(r"politik|pemerintah|presiden|dpr|parlemen|menteri|partai|pemilu|pilkada|kpk|koalisi|istana|kebijakan|uu |undang-undang|anggaran|pajak|korupsi", re.I)
PUBLIC_POWER_ACTION_RE = re.compile(r"menandatangani|mengesahkan|menerbitkan|mencabut|memberlakukan|mengalokasikan|memangkas|menaikkan|menurunkan|mengizinkan|merelaksasi|melonggarkan|menetapkan|meminta|menyiapkan|memberikan|menangani|menerapkan|menindaklanjuti|mewacanakan|memfinalisasi|mengawasi|memeriksa|membatasi|melarang|menjamin|melindungi", re.I)
PUBLIC_ACTOR_RE = re.compile(r"dpr|parlemen|presiden|menteri|pemerintah", re.I)
PUBLIC_MATERIAL_RE = re.compile(r"kebijakan|anggaran|pajak|subsidi|bansos|ruu|undang-undang|peraturan|pengawasan|akuntabilitas|transparansi|korupsi|kpk|tppu|aset|konflik\s+kepentingan|penyalahgunaan\s+wewenang|hak\s+publik|pelayanan\s+publik", re.I)
DRAMA_RE = re.compile(r"kontrovers|konflik|ribut|sengketa|kritik|tuding|bantah|protes|skandal|heboh|viral|geger|polemi|pecat|gugat|ditangkap|tersangka", re.I)
EXCLUDED_RE = re.compile(r"balita|bayi|anak kecil|kekerasan seksual|pencabul|pemerkosaan|pembunuhan|kriminal|penganiayaan|tawuran", re.I)
UA = "budakorporat-pipeline/1.0"
MAX_AGE = timedelta(hours=24)
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


def _candidate_score(item: dict, corroboration: int = 0) -> int:
    hay = f"{item['title']} {item.get('description', '')}"
    # No engagement API in RSS; corroboration + conflict signals are bounded virality proxies.
    return (4 if DRAMA_RE.search(hay) else 0) + (3 if POLITICAL_RE.search(hay) else 0) + min(corroboration, 3) + min(len(item.get("description", "")) // 100, 2)


def _has_political_opportunity(item: dict) -> bool:
    """Require a policy, power, public-money, or accountability angle."""
    hay = f"{item['title']} {item.get('description', '')}"
    return bool(PUBLIC_MATERIAL_RE.search(hay) or (PUBLIC_ACTOR_RE.search(hay) and PUBLIC_POWER_ACTION_RE.search(hay)))


def collect() -> list[dict]:
    seen, out, now = set(), [], datetime.now(timezone.utc)
    title_hits = {}
    for feed in FEEDS:
        try:
            for item in items(feed):
                published = _published_at(item.get("published", ""))
                if not published or published < now - MAX_AGE or published > now + timedelta(minutes=10): continue
                hay = f"{item['title']} {item.get('description', '')}"
                if EXCLUDED_RE.search(hay) or not POLITICAL_RE.search(hay) or not _has_political_opportunity(item): continue
                # Drama is ranking signal, not hard gate: politics can be important without conflict wording.
                normalized = re.sub(r"\W+", " ", item["title"].lower()).strip()
                title_hits[normalized] = title_hits.get(normalized, 0) + 1
                key = hashlib.sha256(item["url"].encode()).hexdigest()
                if key not in seen:
                    seen.add(key); item["key"] = key; out.append(item)
        except Exception as exc:
            log.warning("feed failed %s: %s", feed, exc)
    for item in out:
        normalized = re.sub(r"\W+", " ", item["title"].lower()).strip()
        item["score"] = _candidate_score(item, title_hits[normalized])
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
        is_body = tag == "article" or any(k in classes for k in ("article_body", "article_content", "story_body", "post_content", "entry_content", "c_detail"))
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
    body = text(" ".join(parser.parts))
    if len(body) < 200:
        matches = re.findall(r'"articleBody"\s*:\s*("(?:\\.|[^"\\])*")', raw, re.I)
        bodies = [text(json.loads(match)) for match in matches]
        body = max(bodies, key=len, default=body)
    return body


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

AI_EDITOR_PROMPT = """Kamu reviewer editorial fail-closed.
Review setiap slide hanya terhadap EVIDENCE yang ditautkan pada slide itu. SOURCE_BODY hanya konteks; jangan pakai fakta di luar EVIDENCE slide untuk membenarkan klaim. Jangan cari fakta baru. Jangan mengubah angka,
nama, kutipan, motif, dampak, atau sebab-akibat tanpa dukungan SOURCE_BODY.
Deteksi klaim tersirat, framing hiperbolik, evidence berulang, angka tidak konsisten, dan tulisan datar.
Periksa gaya editorial: draft harus punya POV tajam — jangan cuma ringkasan netral. Tulisan datar tanpa "grease" = REPAIR.
Contoh REPAIR: "Keputusan ini diambil setelah kajian mendalam" = BORING, harus di-repair jadi sesuatu yang bikin emosi.
Contoh PASS: "Kajian mendalam? Siapa yang kajian? Yang diuntungkan siapa?" = SPICY, PASS.
Draft grounded tanpa opini tetap PASS jika factual-nya tajam. Tapi draft yang terlalu hati-hati dan kehilangan emosi = REPAIR.
Opini kuat diperbolehkan selama berbasis evidence. Serangan kebijakan/lembaga/aturan/insentif/standar ganda/distribusi kuasa = PASS. Serangan pribadi tanpa dasar = REPAIR.
Jangan meloloskan tuduhan motif, vonis kriminal, atau klaim dampak yang tidak didukung SOURCE_BODY. Tapi jangan tolak framing bold yang grounded hanya karena "terlalu tajam".
Desakan atau rekomendasi bukan bukti bahwa respons pemerintah lambat, gagal, tidak cukup, simbolik, atau terfragmentasi. Klaim penilaian itu wajib punya evidence eksplisit.

Keluarkan JSON valid saja:
{{"status": "PASS", "issues": []}}
atau
{{"status": "REPAIR", "issues": [{{"slide": 1, "type": "UNSUPPORTED_CLAIM", "reason": "..."}}]}}
atau
{{"status": "REJECT", "issues": [{{"slide": 1, "type": "...", "reason": "..."}}]}}

PASS jika semua slide grounded, tiap slide punya information gain, dan punya emosi/grease. REPAIR jika generator perlu menulis ulang dari evidence tertaut; jangan keluarkan slides baru.
REJECT jika evidence tidak cukup, tulisan tidak punya information gain, atau serangan diarahkan ke pribadi tanpa dasar.
Pastikan emosi berasal dari ketimpangan, kontradiksi, risiko, atau dampak yang benar-benar didukung SOURCE_BODY.
Setiap slide hasil repair 40–500 karakter.
Jangan pernah menulis nama field internal seperti SOURCE_BODY, SOURCE_TITLE,
EVIDENCE_PACK, SLIDES, atau metadata prompt ke dalam slide publik.

SOURCE_BODY:
{body}

SLIDES_WITH_EVIDENCE:
{slides}
"""

PROMPT = """Kamu penulis komentar sosial Indonesia untuk akun budakorporat_id.

⚠️ ATURAN PALING PENTING — WAJIB DIPATUHI:
Konten kamu HARUS SPICY DAN EMOSIONAL. Bukan news wire. Bukan ringkasan netral. Bukan siaran pers.
Setiap slide harus bikin pembaca MERASA SESUATU: marah, kecewa, sedih, curious, atau tertawa.
Jika kamu menulis seperti news wire ("X menetapkan aturan Y", "Keputusan diambil setelah kajian"), kamu GAGAL.
Contoh BORING (DILARANG): "Pemerintah menetapkan aturan baru tentang larangan rangkap jabatan."
Contoh SPICY (WAJIB): "Pemerintah bilang larangan rangkap jabatan demi transparansi. Tapi aturannya sendiri punya lubang segede gudang."
Contoh LAIN SPICY (WAJIB): "Lu bayar pajak. Tapi yang dapat untung siapa? Bukan lu."
Contoh LAIN SPICY (WAJIB): "Aturan baru? Jangan senang dulu. Lu baca pasal kecilnya belum?"

TUJUAN:
Buat thread yang bikin pembaca emosi: kecewa, sedih, marah, atau tertawa. Bukan cuma informatif — harus bikin orang nggak bisa scrolling lewat. Fakta yang disampaikan harus di-framing supaya pembaca merasakan dampaknya secara personal.

MEKANISME VIRAL YANG DIADAPTASI:
- Mulai dari masalah dekat dengan konsekuensi konkret bagi pembaca, bukan klaim heboh.
- Cari power gap atau sudut contrarian hanya jika evidence mendukung.
- Pakai janji spesifik bila evidence pack punya jumlah/fungsi yang jelas.
- Tiap slide memberi micro-utility: tindakan observable → tanda → arti tersembunyi → potensi kerugian, tanpa mengubah analisis menjadi fakta.
- Bangun progression menuju reversal dan perubahan posisi pembaca, lalu CTA spesifik.
- Value harus muncul sebelum CTA; CTA bukan monetisasi otomatis.
- Jangan meniru wording, klaim, angka, contoh, atau jumlah poin dari referensi.

Jangan meniru kalimat, persona, slogan, cerita, atau ekspresi akun lain.

STRUKTUR:
Utamakan 4 slide; maksimal 5. Jika evidence kuat, boleh naik ke 5. Jika evidence unik tidak cukup untuk 4, gunakan lebih sedikit. Sebelum output, cek mekanis bahwa tidak ada evidence ID yang muncul di lebih dari satu slide.

S1 — HOOK (WAJIB SPICY — bukan ringkasan netral)
Buka dengan kontradiksi yang bikin emosi: marah, curious, atau nggak percaya. 
Contoh: "Lu bayar pajak. Tapi yang dapat untung siapa? Bukan lu."
Jangan buka dengan "X menetapkan aturan Y" — itu boring.
Jangan mengarang konflik, motif, atau kepentingan.

S2 — EVIDENCE 1
Berikan fakta/konteks penting pertama dengan framing yang bikin emosi. Contoh: "Selama ini lu kira aturannya adil. Tapi ini yang sebenarnya terjadi."

S3 — EVIDENCE 2
Berikan evidence baru + konteks baru + analisis yang bikin pembaca frustasi atau marah.
Jika melakukan inferensi, tandai jelas dengan bahasa seperti:
"Ini bisa berarti…"
"Secara struktur…"
"Yang perlu diperhatikan…”

S4 — EVIDENCE 3 / ESCALATION
Tambahkan mekanisme, kontradiksi, kronologi, angka, aturan, atau konsekuensi baru yang belum dipakai. Framing: bikin pembaca merasa "kok bisa sih?"

S5 — REVERSAL + CTA
Balik asumsi awal hanya jika tersedia evidence baru yang belum dipakai slide lain. Tarik satu prinsip praktis dan tutup dengan CTA spesifik seperti cek sumber, bandingkan angka, baca kronologi, atau cek aturan.

Jangan tambahkan fakta baru di S5 jika tidak perlu.

ATURAN FAKTA:
- Setiap S1–S4 harus membawa fungsi informasi/evidence baru.
- Satu evidence tidak boleh dipakai ulang sebagai bukti utama.
- Jangan menambah nama, angka, kutipan, motif, dampak, kejadian, atau sebab-akibat yang tidak ada di sumber.
- Jangan menghitung persentase, rasio, atau total baru; hanya pakai angka yang tertulis verbatim di evidence.
- Bedakan fakta, dugaan, dan analisis.
- Dugaan tetap ditulis sebagai dugaan.
- Jangan mengasumsikan motif.
- Opini boleh membandingkan fakta eksplisit dari evidence. Opini juga boleh menarik kesimpulan tajam dari fakta — asal evidence mendukung. Jangan memakai metafora sebagai klaim baru.
- Jika tidak ada kontradiksi eksplisit di evidence, buat hook dari fakta paling berdampak tanpa menciptakan kontradiksi.
- Semakin jauh kesimpulan dari fakta sumber, semakin konservatif bahasanya — tapi tetap tajam, jangan netral.
- Jangan memakai “viral/heboh/gempar” tanpa bukti.
- Dampak ke pekerja, rumah tangga, masyarakat, dll hanya jika sumber mendukung.

ANTI-FILLER:
Jika evidence hanya cukup untuk 4 slide, berhenti di 4. Jika tidak cukup untuk 4, cari evidence lain di source.
Jangan mengulang fakta dengan sinonim.
Jangan isi slide dengan opini generik, moral kosong, atau pertanyaan retoris tanpa fungsi.

STYLE:
Bahasa Indonesia percakapan: santai, tajam, konkret, mudah dipahami.
Gunakan gue/lu jika alami.
Spicy dan emosional: buat pembaca merasa marah, dicurangi, takut, frustrasi, atau terganggu oleh kontradiksi yang ada di source.
Contoh framing BORING (DILARANG):
- "Aturan ini berlaku untuk masa jabatan saat ini maupun calon di masa depan."
- "Keputusan ini diambil setelah kajian mendalam."
Contoh framing SPICY (WAJIB):
- "Aturan baru? Jangan senang dulu. Lu baca pasal kecilnya belum?"
- "Kajian mendalam? Siapa yang kajian? Yang diuntungkan siapa?"
Slide harus punya POV tajam — jangan cuma menyampaikan berita. Fakta yang disampaikan harus di-framing supaya pembaca merasakan dampaknya.
Mayoritas slide wajib berupa fakta sumber. Slide opini/inferensi boleh banyak — tulis natural tanpa label "Analisis:" atau "Penilaian:". Opini harus mengalir dalam cerita, bukan pakai prefix.
Opini kuat diperbolehkan. Jika evidence mendukung, serang langsung — jangan soft-pedal. Jika tidak ada bukti, tulis fakta tajam tanpa opini.
Serang kebijakan, lembaga, aturan, insentif, standar ganda, dan distribusi kuasa — bukan pribadi.
Satu punchline kuat per thread. Setiap slide harus bikin pembaca ingin lanjut ke slide berikutnya.
Hindari hiperbola, tuduhan motif, vonis kriminal, dan framing partisan tanpa evidence. Tapi jangan terlalu hati-hati sampai kehilangangrease.
Setiap slide harus punya "grease" — sesuatu yang bikin pembaca geram, curious, atau merasa dipermainkan. Bukan cuma informasi.

QUALITY CHECK INTERNAL:
Pastikan:
- hook bikin emosi (marah/curious/frustrasi) tanpa mengarang,
- tiap slide punya information gain,
- tidak ada evidence duplikat,
- analisis tidak terdengar seperti fakta,
- reversal benar-benar mengubah cara melihat kasus,
- setiap slide bikin pembaca ingin lanjut ke slide berikutnya.

OUTPUT:
JSON valid saja, tanpa markdown, URL, komentar lain, atau nama field internal.

{{\"slides\":[{{\"text\":\"...\",\"evidence_ids\":[\"E1\"]}}]}}

Setiap slide wajib mencantumkan 1–5 ID dari EVIDENCE_PACK yang benar-benar mendasari text. ID yang sama tidak boleh dipakai di slide berbeda.

Setiap slide 40–500 karakter.

Jangan pernah menulis SOURCE_BODY, SOURCE_TITLE, EVIDENCE_PACK, SLIDES, atau
metadata prompt ke dalam slide publik. Gunakan fakta source langsung.

SOURCE_TITLE:
{title}

SOURCE_BODY:
{body}

EVIDENCE_PACK:
{evidence}
"""


def _evidence_units(body: str) -> list[tuple[str, set[str]]]:
    stop = {"yang", "dan", "atau", "dari", "dengan", "untuk", "dalam", "pada", "ini", "itu", "akan", "jika", "karena", "bahwa"}
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body).strip()) if len(s.strip()) >= 40]
    return [(f"E{i}", {w for w in re.findall(r"[a-zà-ÿ0-9]{4,}", sentence.lower()) if w not in stop}) for i, sentence in enumerate(sentences, 1)]


def _evidence_prompt(body: str) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body).strip()) if len(s.strip()) >= 40]
    return "\\n".join(f"E{i}: {sentence}" for i, sentence in enumerate(sentences, 1))


def _evidence_catalog(body: str) -> dict[str, str]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body).strip()) if len(s.strip()) >= 40]
    return {f"E{i}": sentence for i, sentence in enumerate(sentences, 1)}


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
    motive_frames = ("main-main di belakang layar", "sengaja ngeblokir", "sengaja memblokir", "sengaja menghalangi")
    issues = []
    for i, part in enumerate(parts, 1):
        low = part.lower()
        for marker in markers:
            if marker in low and marker not in lower_body and not any(h in low for h in hedges):
                issues.append(f"S{i}: unsupported claim '{marker}'")
        for frame in motive_frames:
            if frame in low and frame not in lower_body:
                issues.append(f"S{i}: unsupported motive framing '{frame}'")
    return issues


def _number_value(value: str) -> float:
    return float(value.replace(".", "").replace(",", "."))


def _numeric_consistency_issues(parts: list[str]) -> list[str]:
    text_value = "\n".join(parts).lower()
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:dari|/)\s*(\d+(?:[.,]\d+)?)\D{0,80}(\d+(?:[.,]\d+)?)\s*%", text_value)
    if not match:
        return []
    numerator, denominator, stated = (_number_value(value) for value in match.groups())
    if denominator == 0:
        return ["numeric denominator is zero"]
    calculated = numerator / denominator * 100
    if abs(calculated - stated) > 0.15:
        return [f"numeric inconsistency: stated {stated:g}%, calculated {calculated:.2f}%"]
    return []


def _llm_editor(slides: list[dict], body: str) -> dict:
    if not (LLM_URL and LLM_MODEL and LLM_KEY):
        raise RuntimeError("AI editor failed closed: LLM config incomplete")
    catalog = _evidence_catalog(body)
    review_items = [{"text": slide["text"], "evidence": [catalog[key] for key in slide["evidence_ids"]]} for slide in slides]
    payload = json.dumps({"model": LLM_MODEL, "temperature": 0,
                          "messages": [{"role": "user", "content": AI_EDITOR_PROMPT.format(
                              body=body[:12000], slides=json.dumps(review_items, ensure_ascii=False))}],
                          "response_format": {"type": "json_object"}}).encode()
    req = Request(LLM_URL, data=payload, headers={"Authorization": f"Bearer {LLM_KEY}",
                   "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urlopen(req, timeout=45) as response:
            data = json.loads(response.read(200_000))
        raw = data["choices"][0]["message"]["content"]
        result = json.loads(raw) if isinstance(raw, str) else raw
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("AI editor failed closed: response invalid") from exc
    if result.get("status") not in {"PASS", "REPAIR", "REJECT"} or not isinstance(result.get("issues", []), list):
        raise RuntimeError("AI editor failed closed: contract invalid")
    return result


def _review_and_repair(slides: list[dict], item: dict, body: str) -> list[str]:
    parts = [slide["text"] for slide in slides]
    review = _llm_editor(slides, body)
    if review["status"] == "PASS":
        return parts
    if review["status"] == "REJECT":
        raise ValueError(f"AI editor rejected: {review.get('issues', [])}")
    # REPAIR: attempt one targeted fix before raising to outer retry loop
    issues_text = json.dumps(review.get("issues", []), ensure_ascii=False)
    catalog = _evidence_catalog(body)
    current_slides = [{"text": s["text"], "evidence_ids": s["evidence_ids"],
                       "evidence": [catalog[k] for k in s["evidence_ids"] if k in catalog]}
                      for s in slides]
    repair_prompt = (
        f"Perbaiki slide berikut berdasarkan issue editor. "
        f"HAPUS klaim/framing yang disebut di issue. Jangan ganti sinonim — hapus total. "
        f"Tulis ulang hanya bagian yang bermasalah, pertahankan bagian lain. "
        f"PENTING: Hasil repair HARUS SPICY DAN EMOSIONAL. Bukan news wire. Bukan ringkasan netral. "
        f"Contoh BORING (DILARANG): 'Keputusan diambil setelah kajian mendalam.' "
        f"Contoh SPICY (WAJIB): 'Kajian mendalam? Siapa yang kajian? Yang diuntungkan siapa?' "
        f"Output JSON: {{\"slides\":[{{\"text\":\"...\",\"evidence_ids\":[\"E1\"]}}]}}\n\n"
        f"ISSUES:\n{issues_text}\n\n"
        f"CURRENT SLIDES:\n{json.dumps(current_slides, ensure_ascii=False)}\n\n"
        f"SOURCE_BODY (gunakan sebagai satu-satunya sumber fakta):\n{body[:12000]}"
    )
    payload = json.dumps({"model": LLM_MODEL, "temperature": 0.3,
                          "messages": [{"role": "user", "content": repair_prompt}],
                          "response_format": {"type": "json_object"}}).encode()
    req = Request(LLM_URL, data=payload, headers={"Authorization": f"Bearer {LLM_KEY}",
                   "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urlopen(req, timeout=45) as response:
            data = json.loads(response.read(200_000))
        raw = data["choices"][0]["message"]["content"]
        result = json.loads(raw) if isinstance(raw, str) else raw
        repaired = result["slides"]
    except Exception:
        raise ValueError(f"AI editor requested repair: {review.get('issues', [])}")
    if not isinstance(repaired, list) or not repaired:
        raise ValueError(f"AI editor requested repair: {review.get('issues', [])}")
    for s in repaired:
        if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not isinstance(s.get("evidence_ids"), list):
            raise ValueError(f"AI editor requested repair: {review.get('issues', [])}")
        if not all(key in catalog for key in s["evidence_ids"]):
            raise ValueError(f"AI editor requested repair: {review.get('issues', [])}")
    repaired = _label_all_implicit_opinions(repaired)
    parts = [s["text"] for s in repaired]
    validate(parts, item, body, allow_url=False)
    return parts


def _llm_draft(item: dict, body: str, correction: str = "") -> list[dict]:
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
        slides = result["slides"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("LLM response contract invalid") from exc
    if not isinstance(slides, list):
        raise RuntimeError("LLM slides contract invalid")
    catalog = _evidence_catalog(body)
    for slide in slides:
        if not isinstance(slide, dict) or not isinstance(slide.get("text"), str) or not isinstance(slide.get("evidence_ids"), list):
            raise RuntimeError("LLM slides contract invalid")
        ids = slide["evidence_ids"]
        if not 1 <= len(ids) <= 5 or not all(isinstance(key, str) and key in catalog for key in ids):
            raise RuntimeError("LLM evidence id invalid")
    used = set()
    unique = []
    for slide in slides:
        available = []
        for key in slide["evidence_ids"]:
            if key not in used and key not in available:
                available.append(key)
        if available:
            slide["evidence_ids"] = available
            unique.append(slide)
            used.update(available)
    return unique


def _label_all_implicit_opinions(slides: list[dict]) -> list[dict]:
    """No-op: opinions flow naturally without labels. Keep for compatibility."""
    return [{**slide} for slide in slides]


def draft(item: dict, body: str, use_llm=True) -> list[str]:
    if not use_llm:
        return _deterministic_draft(item, body)
    last = None
    correction = ""
    for attempt in range(3):
        try:
            slides = _label_all_implicit_opinions(_llm_draft(item, body, correction))
            parts = [slide["text"] for slide in slides]
            validate(parts, item, body, allow_url=False)
            return _review_and_repair(slides, item, body)
        except (RuntimeError, ValueError) as exc:
            last = exc
            log.warning("LLM draft rejected attempt %d: %s", attempt + 1, exc)
            correction = ("\n\nPERBAIKI OUTPUT SEBELUMNYA. Alasan penolakan: "
                          f"{type(exc).__name__}: {str(exc)[:500]}. Tulis ulang dari SOURCE_BODY saja. "
                          "Hapus semua klaim, pertanyaan, metafora, dan framing yang dikutip dalam alasan penolakan; jangan parafrasekan. "
                          "Pakai 1-5 slide sesuai fakta unik; jangan mengulang fakta atau mengejar jumlah slide. "
                          "Setiap slide harus mengambil evidence berbeda; jika evidence habis, hapus slide. "
                          "Ikuti fungsi slide berurutan jika didukung sumber. Keluarkan JSON valid; tiap slide berupa object text 40-500 karakter dan evidence_ids valid. "
                          "ATURAN KRITIS: Tulis natural seperti cerita — JANGAN pakai prefix 'Analisis:' atau 'Penilaian:'. Opini mengalir dalam kalimat. Opini unlimited. "
                          "Slide non-opini wajib murni fakta source — JANGAN pakai bahasa inferensi di slide non-opini.")
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


_INTERNAL_LABEL_RE = re.compile(r"\b(?:SOURCE_BODY|SOURCE_TITLE|EVIDENCE_PACK|SLIDES)\b", re.I)
_OPINION_LABEL_RE = re.compile(r"^\s*(?:Analisis|Penilaian):", re.I)
_INFERENCE_RE = re.compile(r"(?:^|[.!?]\s+)(?:Artinya|Ini (?:menunjukkan|berarti|membuktikan)|Secara struktur|Yang perlu diperhatikan)\b", re.I)


def validate(parts: list[str], item: dict, body: str, allow_url=True):
    if not 4 <= len(parts) <= 5: raise ValueError("invalid thread parts: need 4-5 slides")
    if any(not p.strip() or len(p) < 40 or len(p) > 500 for p in parts): raise ValueError("part must be 40-500 chars")
    if any(_INTERNAL_LABEL_RE.search(p) for p in parts): raise ValueError("internal prompt metadata leaked")
    # ponytail: removed max-1-opinion cap — user wants unlimited opinions; editor checks grounding only.
    # ponytail: no label enforcement — opinions flow naturally, no "Analisis:" or "Penilaian:" prefix.
    repeated = _repeated_slide_issues(parts)
    if repeated: raise ValueError("; ".join(repeated))
    # Explicit evidence IDs plus per-slide AI review supersede lexical matching.
    joined = "\n".join(parts)
    if "#" in joined: raise ValueError("hashtag not allowed")
    if allow_url and item["url"] not in joined: raise ValueError("source URL missing")
    if not allow_url and re.search(r"https?://", joined, re.I): raise ValueError("LLM URL leak")
    if len(body) < 200: raise ValueError("source body too thin")
    issues = _claim_grounding_issues(parts, body)
    if issues: raise ValueError("; ".join(issues))
    numeric_issues = _numeric_consistency_issues(parts)
    if numeric_issues: raise ValueError("; ".join(numeric_issues))


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
