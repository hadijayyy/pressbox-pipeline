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
content = ["KPK sudah mencatat fakta korupsi tersebut secara resmi. Kebijakan pemerintah itu sudah tercatat dalam dokumen publik."] * 6
slides = content + [f"Sumber: {article['url']}"]
assert p.validate(slides, article)[0]
assert not p.validate(["Angka 9999 muncul dalam catatan panjang tersebut. KPK sudah mencatat fakta korupsi tersebut secara resmi."] * 6 + [f"Sumber: {article['url']}"], article)[0]

number_article = {**article, "body": "KPK menyebut penerimaan uang Rp30 miliar dalam perkara itu. " * 20}
number_slides = ["KPK menyebut penerimaan 30 miliar dalam perkara itu. Fakta tersebut tercatat dalam keterangan resmi."] * 6 + [f"Sumber: {number_article['url']}"]
assert p.validate(number_slides, number_article)[0]
assert p.validate(["KPK menyebut penerimaan uang Rp30 miliar dalam perkara itu. Fakta tersebut tercatat dalam keterangan resmi."] * 4 + ["KPK menyebut penerimaan 30 miliar dalam perkara itu."] * 2 + [f"Sumber: {number_article['url']}"], number_article)[0]
assert not p.needs_semantic_verify(["KPK menyebut penerimaan 30 miliar dalam perkara itu."], number_article)
assert p.needs_semantic_verify(["Ini pasti akan berdampak besar."], number_article)

# Desired resumable-chain contract. Fails until journal schema/helpers exist.
assert hasattr(p, "load_partial")
assert hasattr(p, "checkpoint_post")
assert hasattr(p, "open_db")
assert p.llm_key()  # Loaded from env or protected .env fallback; value never printed.
assert p.TOKEN_FILE.name == "budakorporat_token.json"
assert p.TARGET_ACCOUNT == "budakorporat_id"
assert p.TARGET_USER_ID == "27516379201355016"
assert "tempo_politik" not in p.SOURCES
assert p.SOURCES["media_indonesia_politik"] == "https://mediaindonesia.com/politik-dan-hukum"
assert "detik_politik" not in p.SOURCES and "kompas_politik" not in p.SOURCES

# Image dimensions are decoded from bytes; publisher headers alone never qualify as HD.
jpeg_1200 = b"\xff\xd8\xff\xc0\x00\x11\x08\x03\x20\x04\xb0" + b"\x00" * 16
assert p.image_width(jpeg_1200) == 1200
assert p.image_width(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1200).to_bytes(4, "big") + b"\x00" * 4) == 1200
assert p.image_width(b"not-image") == 0

# Partial journal resumes only exact payload; dry-run DB stays read-only.
old_state = p.STATE
with tempfile.TemporaryDirectory() as tmp:
    p.STATE = Path(tmp) / "state.sqlite3"
    db = p.open_db()
    partial = {**article, "url": "https://example.test/partial"}
    slides = [f"Slide fakta {i}. Bukti tetap ada." for i in range(1, 7)] + [f"Sumber: {partial['url']}"]
    p.checkpoint_post(db, partial, slides, 0, "root")
    p.checkpoint_post(db, partial, slides, 1, "reply-1")
    assert p.load_partial(db, partial, slides) == [(0, "root"), (1, "reply-1")]
    try:
        p.load_partial(db, partial, slides[:-1] + ["Sumber: https://example.test/other"])
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
print("ok")
