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
from io import BytesIO
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
    "https://www.cnnindonesia.com/ekonomi/rss",
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
    "https://www.mongabay.co.id/feed",
    "https://www.antaranews.com/rss/top-news.xml",
]
POLITICAL_RE = re.compile(r"politik|pemerintah|presiden|dpr|parlemen|menteri|partai|pemilu|pilkada|kpk|koalisi|istana|kebijakan|uu |undang-undang|anggaran|pajak|korupsi", re.I)
PUBLIC_POWER_ACTION_RE = re.compile(r"menandatangani|mengesahkan|menerbitkan|mencabut|memberlakukan|mengalokasikan|memangkas|menaikkan|menurunkan|mengizinkan|merelaksasi|melonggarkan|menetapkan|meminta|menyiapkan|memberikan|menangani|menerapkan|menindaklanjuti|mewacanakan|memfinalisasi|mengawasi|memeriksa|membatasi|melarang|menjamin|melindungi", re.I)
PUBLIC_ACTOR_RE = re.compile(r"dpr|parlemen|presiden|menteri|pemerintah", re.I)
PUBLIC_MATERIAL_RE = re.compile(r"kebijakan|anggaran|pajak|subsidi|bansos|ruu|undang-undang|peraturan|pengawasan|akuntabilitas|transparansi|korupsi|kpk|tppu|aset|konflik\s+kepentingan|penyalahgunaan\s+wewenang|hak\s+publik|pelayanan\s+publik", re.I)
DRAMA_RE = re.compile(r"kontrovers|konflik|ribut|sengketa|kritik|tuding|bantah|protes|skandal|heboh|viral|geger|polemi|pecat|gugat|ditangkap|tersangka", re.I)
EXCLUDED_RE = re.compile(r"balita|bayi|anak kecil|kekerasan seksual|pencabul|pemerkosaan|pembunuhan|kriminal|penganiayaan|tawuran", re.I)
UA = "budakorporat-pipeline/1.0"
MAX_AGE = timedelta(hours=48)
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
    title_normalized_map = {}  # normalized -> original items for dedup
    for feed in FEEDS:
        try:
            for item in items(feed):
                published = _published_at(item.get("published", ""))
                if not published or published < now - MAX_AGE or published > now + timedelta(minutes=10): continue
                hay = f"{item['title']} {item.get('description', '')}"
                if EXCLUDED_RE.search(hay) or not POLITICAL_RE.search(hay) or not _has_political_opportunity(item): continue
                # Drama is ranking signal, not hard gate: politics can be important without conflict wording.
                normalized = re.sub(r"\W+", " ", item["title"].lower()).strip()
                # Title dedup: skip if same topic already collected (fuzzy: 80% word overlap)
                skip = False
                for existing_norm in list(title_normalized_map.keys()):
                    existing_words = set(existing_norm.split())
                    new_words = set(normalized.split())
                    if existing_words and new_words:
                        overlap = len(existing_words & new_words) / min(len(existing_words), len(new_words))
                        if overlap >= 0.8:
                            skip = True
                            break
                if skip:
                    continue
                title_normalized_map.setdefault(normalized, []).append(item)
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


def _crop_image_to_4_5(image_url: str) -> str:
    """Download image, crop to 4:5 ratio (center crop), save to temp file. Returns local path."""
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow not installed; cannot crop image")
        return image_url
    
    try:
        req = Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as resp:
            img_data = resp.read()
        
        img = Image.open(BytesIO(img_data))
        w, h = img.size
        
        target_ratio = 4 / 5  # 0.8
        current_ratio = w / h
        
        if current_ratio > target_ratio:
            # Too wide — crop width
            new_w = int(h * target_ratio)
            left = (w - new_w) // 2
            img = img.crop((left, 0, left + new_w, h))
        elif current_ratio < target_ratio:
            # Too tall — crop height
            new_h = int(w / target_ratio)
            top = (h - new_h) // 2
            img = img.crop((0, top, w, top + new_h))
        # else: already 4:5, no crop needed
        
        # Resize to 1080x1350 for Threads
        img = img.resize((1080, 1350), Image.LANCZOS)
        
        out_path = f"/tmp/threads_crop_{hashlib.md5(image_url.encode()).hexdigest()[:8]}.jpg"
        img.save(out_path, "JPEG", quality=90)
        log.info("cropped image %dx%d -> %s", w, h, out_path)
        return out_path
    except Exception as e:
        log.warning("image crop failed (%s); using original URL", e)
        return image_url


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
**ATURAN KETAT: Jangan ubah fakta sumber.** Jika sumber bilang "tidak ada kebakaran", jangan tulis "api padam" atau "terbakar". Jika sumber bilang "bukan kebakaran", jangan-framing seolah memang kebakaran. Fakta sumber harus persis, bukan diplesetkan.
**ATURAN KETAT: Angka spesifik harus ada di sumber.** Jika slide menyebut "3 mobil" atau "14 personel", angka itu HARUS muncul di SOURCE_BODY. Jika tidak ada, hapus angka atau ganti deskriptif ("beberapa mobil damkar").
**ATURAN KETAT: Opini tidak boleh menambah fakta baru.** Jika source bilang "kuliah S2 di London", opini tidak boleh menulis "beli tiket pesawat" — karena "beli tiket" adalah fakta baru yang tidak ada di source. Opini hanya boleh mengekspresikan emosi/reaksi terhadap fakta yang SUDAH ADA di evidence. Contoh SALAH: "Dia malah beli tiket pesawat ke Inggris" (fakta baru: beli tiket). Contoh BENAR: "Dia malah kuliah di luar negeri sementara rakyat susah" (framing emosi terhadap fakta yang sudah ada di source). Jika slide menambah detail fakta baru meski "technically benar", flag sebagai UNSUPPORTED_CLAIM.
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

TUJUAN:
Buat thread politik/sosial yang tajam, emosional, dan punya POV — bukan news wire, ringkasan netral, atau siaran pers.

Konten harus membuat pembaca merasa sesuatu: marah, kecewa, khawatir, geli, frustrasi, curious, atau merasa ada sesuatu yang janggal.

PRIORITAS:

1. Akurasi fakta
2. Information gain
3. Emotional framing
4. Viral potential

Jika framing spicy membutuhkan asumsi atau fakta baru, pilih framing yang lebih konservatif. Fakta selalu menang.

ATURAN UTAMA:

- Gunakan hanya fakta yang tersedia di source/evidence.
- Jangan menambah nama, angka, motif, dampak, kejadian, sebab-akibat, atau detail yang tidak tersedia.
- Jangan menghitung angka, rasio, persentase, total, atau estimasi baru.
- Bedakan fakta dan inferensi.
- Jika melakukan inferensi, gunakan bahasa seperti:
  "Ini bisa berarti..."
  "Secara struktur..."
  "Yang perlu diperhatikan..."
  "Implikasinya bisa..."
- Jangan mengubah kemungkinan menjadi kepastian.
- Jangan mengasumsikan motif.
- Jangan menciptakan kontradiksi jika source tidak menunjukkan kontradiksi.
- Kritik kebijakan, aturan, lembaga, proses, insentif, standar ganda, atau distribusi kuasa — bukan karakter pribadi.

SPICY BUKAN BERARTI RAGE BAIT.

Framing harus lahir dari fakta.

Boring:
"Pemerintah menetapkan aturan baru."

Lebih tajam:
"Aturannya kelihatan tegas. Tapi bagian yang paling menentukan justru ada di detailnya."

Jika fakta cocok untuk ironi, gunakan ironi.
Jika cocok untuk kekhawatiran, gunakan kekhawatiran.
Jangan memaksa semua topik menjadi kemarahan.

STRUKTUR:
Utamakan 4 slide, maksimal 5.
Jika evidence hanya cukup untuk 3–4 slide, jangan memaksakan tambahan.

S1 — HOOK
Buka dengan fakta paling berdampak, mengejutkan, kontradiktif, janggal, atau consequential dari source.

Prioritaskan:

- angka penting,
- perubahan drastis,
- pengecualian,
- target vs realisasi,
- aturan dengan konsekuensi besar,
- kronologi janggal,
- atau fakta yang mengubah persepsi awal.

Jangan buka seperti berita formal:
"X menetapkan Y."

Hook harus punya tension, tapi tidak boleh mengarang.

S2 — EVIDENCE 1
Berikan fakta/konteks penting pertama dengan framing tajam.

Harus menambah informasi baru.

S3 — EVIDENCE 2
Tambahkan evidence berbeda yang memperdalam kasus:
angka, aturan, kronologi, pernyataan resmi, mekanisme, pengecualian, atau perbandingan eksplisit.

Jika melakukan analisis, tandai sebagai inferensi.

S4 — ESCALATION
Tambahkan evidence baru yang menjelaskan skala, mekanisme, konsekuensi, atau kontradiksi lebih dalam.

Slide ini harus membuat pembaca memahami:
"ternyata masalahnya di sini."

Jangan sekadar mengulang S2/S3 dengan wording berbeda.

S5 — SYNTHESIS / REVERSAL + CTA
Opsional.

Gunakan untuk menyatukan evidence sebelumnya dan mengubah cara pembaca melihat kasus.

Tidak perlu membawa evidence baru.

Contoh pola:
"Jadi masalahnya bukan cuma X. Yang lebih penting justru Y."

CTA harus spesifik:

- cek dokumen,
- baca aturan,
- bandingkan angka,
- lihat kronologi,
- cek realisasi,
- atau baca sumber lengkap.

Hindari CTA kosong seperti:
"Menurut lu gimana?"
"Setuju gak?"

ATURAN EVIDENCE:

- Setiap S1–S4 harus punya information gain baru.
- Satu evidence_id hanya boleh muncul sekali dalam seluruh thread.
- Jika satu evidence mendukung beberapa poin, pilih satu slide tempat evidence tersebut paling penting.
- Jangan mengulang fakta, angka, pasal, kutipan, atau kejadian hanya untuk memenuhi jumlah slide.
- S5 boleh menggunakan evidence_ids kosong jika hanya berupa synthesis.

ATURAN OPINI:
Opini boleh tajam selama masih ditopang evidence.

Opini tidak boleh menciptakan fakta baru.

Semakin jauh kesimpulan dari fakta sumber, semakin konservatif bahasanya.

Gunakan:
"bisa berarti"
"mengindikasikan"
"membuka kemungkinan"
"layak diperhatikan"
"perlu dipertanyakan"

jika evidence belum cukup untuk kesimpulan pasti.

ANTI-FILLER:
Setiap slide harus menjawab minimal satu:

- Apa fakta barunya?
- Kenapa ini penting?
- Apa mekanismenya?
- Apa yang berubah dari pemahaman sebelumnya?
Hapus slide atau kalimat yang hanya berisi:

- opini generik,
- moral kosong,
- rage bait,
- pertanyaan retoris kosong,
- atau pengulangan fakta.

STYLE:
Bahasa Indonesia percakapan.
Santai, tajam, konkret, mudah dipahami.
Gunakan gue/lu jika alami.

Jangan terdengar seperti media formal, humas, laporan pemerintah, atau akademisi.

Satu punchline kuat per thread.

Hindari hiperbola dan clickbait yang tidak dibayar oleh isi.

Jangan gunakan "viral", "heboh", "gempar", atau "bikin geger" tanpa evidence.

QUALITY CHECK INTERNAL:
Sebelum output, pastikan:

- hook berasal dari fakta source,
- tiap slide punya information gain,
- tidak ada evidence_id duplikat,
- tidak ada fakta tambahan,
- inferensi tidak ditulis sebagai kepastian,
- tidak ada motif yang diasumsikan,
- tidak ada filler,
- S5 merupakan synthesis/reversal, bukan pengulangan,
- CTA spesifik.

Jika factuality bertabrakan dengan emotional framing, factuality selalu menang.

OUTPUT:
JSON valid saja.

Tanpa markdown, URL, komentar, atau penjelasan tambahan.

Format:
{{\"slides\":[{{\"text\":\"...\",\"evidence_ids\":[\"E1\"]}},{{\"text\":\"...\",\"evidence_ids\":[\"E2\"]}},{{\"text\":\"...\",\"evidence_ids\":[]}}]}}

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
    return "\n".join(f"E{i}: [{sentence[:80]}...]" if len(sentence) > 80 else f"E{i}: {sentence}" for i, sentence in enumerate(sentences, 1))


def _evidence_catalog(body: str) -> dict[str, str]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body).strip()) if len(s.strip()) >= 40]
    return {f"E{i}": sentence for i, sentence in enumerate(sentences, 1)}

def _evidence_snippet(evidence_id: str, body: str) -> str:
    """Return truncated snippet for evidence ID."""
    catalog = _evidence_catalog(body)
    full = catalog.get(evidence_id, "")
    return full[:80] + "..." if len(full) > 80 else full


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
    markers = ("butuh uang", "butuh dana", "butuh duit", "gaya hidup", "proyek fiktif", "mark up")
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


def _factual_drift_issues(parts: list[str], body: str) -> list[str]:
    """Reject slides that contradict explicit source statements or add new facts."""
    lower_body = body.lower()
    issues = []
    # Check for "X happened" when source says "X did NOT happen"
    negation_pairs = [
        ("tidak ada kebakaran", ["api padam", "api menyala", "terbakar", "kebakaran terjadi"]),
        ("bukan kebakaran", ["api padam", "api menyala", "terbakar", "kebakaran terjadi"]),
    ]
    for i, part in enumerate(parts, 1):
        low = part.lower()
        for negated_fact, contradicts in negation_pairs:
            if negated_fact in lower_body:
                for c in contradicts:
                    if c in low:
                        issues.append(f"S{i}: contradicts source (source says '{negated_fact}', slide implies '{c}')")
        # Check for new facts introduced in opinion
        # Pattern: "beli [noun]" when source doesn't mention buying
        new_fact_patterns = [
            (r'beli\s+(tiket|properti|aset|mobil|rumah|tanah|saham)', "buying"),
            (r'mengambil\s+(dana|uang|anggaran|dana-desa)', "taking funds"),
            (r'korupsi|suap|menilep', "corruption"),
        ]
        for pattern, label in new_fact_patterns:
            if re.search(pattern, low) and not re.search(pattern, lower_body):
                issues.append(f"S{i}: possible new fact '{label}' — check if source supports this claim")
    return issues


def _unsourced_number_issues(parts: list[str], body: str) -> list[str]:
    """Reject specific numbers in slides that don't appear in source."""
    lower_body = body.lower()
    issues = []
    for i, part in enumerate(parts, 1):
        # Match numbers with units (mobil, personel, orang, unit, dll)
        for m in re.finditer(r'\b(\d+)\s*(mobil|personel|orang|unit|armada|truk|personil|petugas)\b', part.lower()):
            num, unit = m.group(1), m.group(2)
            if f"{num} {unit}" not in lower_body and f"{num} {unit}" not in lower_body:
                issues.append(f"S{i}: unsourced number '{num} {unit}' not in article")
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
    # Auto-fix repair output before strict validation
    repaired = _salvage_slides(repaired, catalog)
    if not repaired or len(repaired) < 4:
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


def _salvage_slides(slides: list[dict], catalog: dict[str, str]) -> list[dict]:
    """Auto-fix repair/draft output: truncate long, discard short, filter invalid evidence_ids."""
    fixed = []
    for s in slides:
        if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not isinstance(s.get("evidence_ids"), list):
            continue
        t = s["text"].strip()
        # discard too short
        if len(t) < 40:
            continue
        # truncate too long at last sentence boundary before 500
        if len(t) > 500:
            cut = t[:500]
            last_period = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
            t = cut[:last_period + 1] if last_period > 30 else cut[:497] + "..."
        # filter invalid evidence_ids
        valid_ids = [k for k in s["evidence_ids"] if k in catalog]
        if not valid_ids:
            continue
        fixed.append({"text": t, "evidence_ids": valid_ids})
    return fixed


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
            raw_slides = _llm_draft(item, body, correction)
            slides = _salvage_slides(raw_slides, _evidence_catalog(body))
            if not slides or len(slides) < 4:
                raise ValueError(f"salvage returned {len(slides) if slides else 0} slides")
            slides = _label_all_implicit_opinions(slides)
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
            if overlap >= 0.55:
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
    drift = _factual_drift_issues(parts, body)
    if drift: raise ValueError("; ".join(drift))
    unsourced = _unsourced_number_issues(parts, body)
    if unsourced: raise ValueError("; ".join(unsourced))
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

    # --- retry logic: if pending retry and 15 min elapsed, retry same item ---
    retry = state.get("retry")
    if retry and not dry:
        elapsed = int(time.time()) - retry.get("ts", 0)
        if elapsed >= 900:  # 15 minutes
            log.info("retrying publish for %s (elapsed %ds)", retry.get("item_key"), elapsed)
            # find the item from candidates
            candidates = collect()
            retry_item = None
            for c in candidates:
                if c["key"] == retry["item_key"]:
                    retry_item = c
                    break
            if retry_item:
                try:
                    resolve_article_url(retry_item)
                    body = article_body(retry_item)
                    image_url = article_image(retry_item)
                    if not image_url:
                        raise RuntimeError("article hero image missing")
                    parts = _safe_draft(retry_item, body, use_llm)
                    validate(parts, retry_item, body, allow_url=False)
                    result = publish(parts, dry, image_url)
                    state.setdefault("posted", []).append(retry_item["key"])
                    state["last"] = {"url": retry_item["url"], "result": result, "ts": int(time.time())}
                    state.pop("retry", None)
                    save_json(STATE, state)
                    print(json.dumps({"target": USER, "dry_run": dry, "posts": result}, ensure_ascii=False))
                    return
                except Exception as exc:
                    log.exception("retry also failed")
                    state["retry"]["ts"] = int(time.time())  # reset timer for next retry
                    state["retry"]["error"] = str(exc)
                    save_json(STATE, state)
                    print(json.dumps({"target": USER, "dry_run": dry, "status": "NO_POST_PUBLISH_ERROR", "error": str(exc), "retry_in": "15m"}, ensure_ascii=False))
                    return
            else:
                log.warning("retry item %s not found in candidates, clearing retry", retry.get("item_key"))
                state.pop("retry", None)
                save_json(STATE, state)
        elif not dry:
            log.info("retry pending for %s in %ds", retry.get("item_key"), 900 - elapsed)
            print(json.dumps({"target": USER, "dry_run": dry, "status": "RETRY_PENDING", "retry_in": f"{(900 - elapsed)//60}m"}, ensure_ascii=False))
            return

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
        if not dry:
            state["retry"] = {"item_key": item["key"], "item_url": item["url"], "item_title": item.get("title", ""), "ts": int(time.time()), "error": str(exc)}
            save_json(STATE, state)
        print(json.dumps({"target": USER, "dry_run": dry, "status": "NO_POST_PUBLISH_ERROR", "error": str(exc), "retry_in": "15m"}, ensure_ascii=False))
        return
    if not dry:
        state.setdefault("posted", []).append(item["key"]); state["last"] = {"url": item["url"], "result": result, "ts": int(time.time())}
        state.pop("retry", None)  # clear pending retry on success
        save_json(STATE, state)
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
