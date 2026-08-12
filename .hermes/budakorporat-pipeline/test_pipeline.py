import importlib.util
import tempfile
from datetime import timedelta
from pathlib import Path


spec = importlib.util.spec_from_file_location("pipeline", Path(__file__).with_name("pipeline.py"))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

body = " ".join(f"Fakta nomor {i} tentang korupsi, kebijakan pemerintah, dan KPK sudah tercatat." for i in range(1, 45))
article = {"body": body, "published": p.now() - timedelta(hours=1), "image_url": "https://example.test/image.jpg", "title": "KPK mengusut kebijakan pemerintah", "url": "https://example.test/article"}

p.valid_image = lambda _: True
assert p.article_ok(article) == (True, "")

# LOW_REACH is ranking-only; source/body/image gates still decide readiness.
low_reach = {**article, "title": "Pemerintah umumkan kebijakan pendidikan", "body": "Pendidikan pemerintah dan kebijakan publik tercatat dalam laporan resmi. " * 25}
p.score = lambda _: 0
assert p.article_ok(low_reach)[0] is True
p.score = lambda _: 24

# Image fallback chooses first valid article image, not only og:image.
p.valid_image = lambda url: url == "https://example.test/real.jpg"
assert p.select_valid_image({"image_url": "https://example.test/logo.jpg", "image_candidates": ["https://example.test/logo.jpg", "https://example.test/real.jpg"]}) == "https://example.test/real.jpg"
p.valid_image = lambda _: True

content = ["KPK sudah mencatat fakta korupsi tersebut secara resmi. Kebijakan pemerintah itu sudah tercatat dalam dokumen publik."] * 5
content.append("KPK masih memeriksa fakta korupsi tersebut. Apa dasar pemeriksaan berikutnya?")
content_with_source = content + ["Sumber: https://example.test/article"]
assert p.validate(content_with_source, article)[0]
assert not p.validate(content, article)[0]
assert not p.validate(content + ["Sumber: https://other.test/article"], article)[0]
packet = p.fact_packet("Awal fakta yang cukup panjang untuk masuk packet. " + "Tengah fakta yang cukup panjang untuk masuk packet. " * 3 + "FAKTA AKHIR KPK menyebut angka 77 miliar.")
assert "FAKTA AKHIR KPK menyebut angka 77 miliar." in packet
assert "[F005]" in packet

number_article = {**article, "body": "KPK menyebut penerimaan uang Rp30 miliar dalam perkara itu. " * 20}
number_slides = ["KPK menyebut penerimaan 30 miliar dalam perkara itu. Fakta tersebut tercatat dalam keterangan resmi."] * 5
number_slides.append("KPK masih memeriksa penerimaan 30 miliar itu. Apa dasar pemeriksaan berikutnya?")
number_slides += ["Sumber: https://example.test/article"]
assert p.validate(number_slides, number_article)[0]
assert not p.validate(["KPK menyebut penerimaan 999 miliar dalam perkara itu. Fakta tersebut tercatat dalam keterangan resmi."] * 6 + ["Sumber: https://example.test/article"], number_article)[0]
assert not p.needs_semantic_verify(["KPK menyebut penerimaan 30 miliar dalam perkara itu."], number_article)
assert p.needs_semantic_verify(["Ini pasti akan berdampak besar."], number_article)

# S6 must anchor question in factual unresolved point, not generic engagement bait.
cta_article = {**article, "body": "KPK menetapkan lima tersangka dalam perkara itu. Baru empat orang yang ditahan, sedangkan satu tersangka dilaporkan sakit. " * 8}
cta_slides = ["KPK menetapkan lima tersangka dalam perkara itu. Fakta ini tercatat dalam keterangan resmi."] * 5
cta_slides += ["Baru empat orang yang ditahan, sedangkan satu tersangka dilaporkan sakit. Apa dasar perbedaan status penahanan itu?"]
cta_slides += ["Sumber: https://example.test/article"]
assert p.validate(cta_slides, cta_article)[0]
assert p.needs_semantic_verify(cta_slides[:p.CONTENT_SLIDES], cta_article)
no_question = cta_slides[:5] + ["Baru empat orang yang ditahan, sedangkan satu tersangka dilaporkan sakit. Status satu tersangka masih berbeda."] + cta_slides[6:]
assert p.validate(no_question, cta_article) == (False, "S6_NO_QUESTION")
generic_question = cta_slides[:5] + ["Fakta kasus masih diperiksa. Apa pendapat lu?"] + cta_slides[6:]
assert p.validate(generic_question, cta_article) == (False, "S6_GENERIC_CTA")

# Desired resumable-chain contract. Fails until journal schema/helpers exist.
assert hasattr(p, "load_partial")
assert hasattr(p, "checkpoint_post")
assert hasattr(p, "open_db")
assert p.llm_key()  # Loaded from env or protected .env fallback; value never printed.
assert p.TOKEN_FILE.name == "budakorporat_token.json"
assert p.TARGET_ACCOUNT == "budakorporat_id"
assert p.TARGET_USER_ID == "27516379201355016"
assert p.COOLDOWN_MINUTES == 15
assert p.MIN_BODY_CHARS == 1500
assert p.score({"title": "KPK korupsi", "body": "korupsi"}) >= 0
assert "tempo_politik" not in p.SOURCES
assert p.SOURCES["media_indonesia_politik"] == "https://mediaindonesia.com/politik-dan-hukum"
assert p.HOT_SIGNAL_SOURCES["google_news_politik"].endswith("when%3A24h&hl=id&gl=ID&ceid=ID%3Aid")
assert "detik_politik" not in p.SOURCES and "kompas_politik" not in p.SOURCES

# Hot ranking collapses same-story coverage and rewards independent publishers.
ranked = p.rank_candidates([
    {"title": "KPK tetapkan menteri X sebagai tersangka korupsi", "url": "https://a.test/1", "source": "a", "publisher": "A", "published": p.now() - timedelta(hours=1), "body": "korupsi"},
    {"title": "KPK menetapkan menteri X tersangka kasus korupsi", "url": "https://b.test/1", "source": "b", "publisher": "B", "published": p.now() - timedelta(hours=2), "body": "korupsi"},
    {"title": "DPR bahas kebijakan anggaran baru", "url": "https://c.test/1", "source": "c", "publisher": "C", "published": p.now() - timedelta(hours=3), "body": "anggaran"},
], ["KPK tetapkan menteri X sebagai tersangka korupsi - outlet"])
assert len(ranked) == 2
assert ranked[0]["cluster_size"] == 2
assert ranked[0]["independent_publishers"] == 2
assert ranked[0]["hot_signal"] is True

# Image dimensions are decoded from bytes; publisher headers alone never qualify as HD.
jpeg_1200 = b"\xff\xd8\xff\xc0\x00\x11\x08\x03\x20\x04\xb0" + b"\x00" * 16
assert p.image_width(jpeg_1200) == 1200
assert p.IMAGE_MIN_WIDTH == 800
assert p.IMAGE_MIN_HEIGHT == 450
assert p.image_dimensions(jpeg_1200) == (1200, 800)
assert p.image_width(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1200).to_bytes(4, "big") + b"\x00" * 4) == 1200
assert p.image_width(b"not-image") == 0

# Partial journal resumes only exact payload; dry-run DB stays read-only.
old_state = p.STATE
with tempfile.TemporaryDirectory() as tmp:
    p.STATE = Path(tmp) / "state.sqlite3"
    db = p.open_db()
    partial = {**article, "url": "https://example.test/partial"}
    slides = [f"Slide fakta {i}. Bukti tetap ada." for i in range(1, 7)]
    p.checkpoint_post(db, partial, slides, 0, "root")
    p.checkpoint_post(db, partial, slides, 1, "reply-1")
    assert p.load_partial(db, partial, slides) == [(0, "root"), (1, "reply-1")]
    try:
        p.load_partial(db, partial, slides[:-1] + ["Changed payload."])
        raise AssertionError("payload mismatch must fail closed")
    except RuntimeError as e:
        assert str(e) == "PUBLISH_AMBIGUOUS"
    db.close()
    before = p.STATE.read_bytes()
    db = p.open_db(dry_run=True)
    assert p.cooldown(db, "https://example.test/new")
    db.close()
    assert p.STATE.read_bytes() == before
p.STATE = old_state

# Editorial prompt must suppress procedural filler and unsupported public-impact claims.
seen = {}
def capture_llm(prompt):
    seen["prompt"] = prompt
    return {"status": "REJECT", "slides": [], "reason": "test"}
old_llm = p.llm
p.llm = capture_llm
p.write({"body": "Kasus korupsi mencatat kerugian Rp622 miliar. Kuasa hukum menyebut angka 271.000 dolar AS. " * 20})
p.llm = old_llm
prompt_lower = seen["prompt"].lower()
assert "jangan isi slide dengan agenda sidang, susunan majelis, atau daftar nama" in prompt_lower
assert "jika body tidak memuat dampak publik, jangan memaksakan dampak" in prompt_lower
assert "total sudah a tetapi baru b yang berstatus berbeda" in prompt_lower
assert "pengecualian bernama dan alasan literalnya" in prompt_lower
assert "coverage" in prompt_lower
# Local LLM timeout gets one bounded retry without retrying non-timeout errors.
class _LLMResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": '{"status":"REJECT","slides":[],"reason":"test"}'}}]}

llm_calls = []
def fake_llm_post(*args, **kwargs):
    llm_calls.append(kwargs["timeout"])
    if len(llm_calls) == 1:
        raise p.requests.exceptions.ReadTimeout("test timeout")
    return _LLMResponse()
old_post, old_sleep = p.requests.post, p.time.sleep
p.requests.post, p.time.sleep = fake_llm_post, lambda _: None
try:
    assert p.llm("test")["status"] == "REJECT"
finally:
    p.requests.post, p.time.sleep = old_post, old_sleep
assert llm_calls == [p.LLM_TIMEOUT, p.LLM_TIMEOUT]
assert p.LLM_TIMEOUT == 120
assert p.LLM_RETRIES == 1
assert p.TOP_N == 10
assert p.MAX_REVISION_CYCLES == 1
assert p.PREPARED.name == "prepared.json"

print("ok")
