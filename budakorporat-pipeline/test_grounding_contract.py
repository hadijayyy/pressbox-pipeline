import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import budakorporat_pipeline as p


def test_policy_opportunity_gate():
    assert p._has_political_opportunity({"title": "DPR bahas anggaran", "description": "pengawasan pemerintah"})
    assert p._has_political_opportunity({"title": "Presiden menandatangani keputusan baru", "description": "aturan berlaku nasional"})
    assert p._has_political_opportunity({"title": "Pemerintah melonggarkan syarat devisa", "description": "eksportir mendapat fasilitas"})
    assert p._has_political_opportunity({"title": "Kementerian menerapkan panduan pengawasan", "description": "hak pekerja dilindungi"})
    assert not p._has_political_opportunity({"title": "Polisi menangkap pencuri ruko", "description": "laporan warga"})
    assert not p._has_political_opportunity({"title": "Presiden akan menutup muktamar", "description": "acara berlangsung tertib"})
    assert not p._has_political_opportunity({"title": "Menteri mengajak kampus berkarya", "description": "acara organisasi"})


def test_candidate_pool_uses_24_hour_freshness_and_live_political_sources():
    assert p.MAX_AGE == p.timedelta(hours=24)
    assert "https://www.cnbcindonesia.com/news/rss" in p.FEEDS
    assert "https://nasional.sindonews.com/rss" in p.FEEDS
    assert {
        "https://feed.liputan6.com/rss/news",
        "https://sindikasi.okezone.com/index.php/rss/1/RSS2.0",
        "https://www.inews.id/feed",
        "https://katadata.co.id/rss",
    } <= set(p.FEEDS)
    assert not {
        "https://www.kompas.com/rss",
        "https://rss.detik.com/index.php/detikcom",
        "https://rss.tempo.co/",
    } & set(p.FEEDS)


def test_article_text_uses_json_ld_article_body():
    body = "Isi artikel penuh tentang pengawasan kebijakan publik. " * 8
    raw = f'<script type="application/ld+json">{{"@type":"NewsArticle","articleBody":{json.dumps(body)}}}</script>'
    assert p._extract_article_text(raw) == body.strip()


def test_article_text_supports_c_detail_body():
    body = "Isi artikel penuh tentang pengawasan kebijakan publik. " * 8
    assert p._extract_article_text(f'<div class="c-detail read"><p>{body}</p></div>') == body.strip()


def test_prompt_forbids_invented_reader_stakes_and_rejected_frames():
    assert "Jangan menyeret pembaca, pajak, kerugian negara" in p.PROMPT
    assert "hapus klaimnya; jangan sekadar melunakkan" in p.PROMPT
    assert "membandingkan dua fakta eksplisit" in p.PROMPT


def test_evidence_ids_must_exist_and_be_unique(monkeypatch):
    body = "Kalimat sumber pertama cukup panjang untuk menjadi bukti utama. Kalimat sumber kedua juga cukup panjang untuk menjadi bukti lain."
    response = {"slides": [
        {"text": "a" * 40, "evidence_ids": ["E1"]},
        {"text": "b" * 40, "evidence_ids": ["E1", "E2", "E2"]},
        {"text": "c" * 40, "evidence_ids": ["E1"]},
    ]}

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, *_): return json.dumps({"choices": [{"message": {"content": json.dumps(response)}}]}).encode()

    monkeypatch.setattr(p, "urlopen", lambda *args, **kwargs: Reply())
    monkeypatch.setattr(p, "LLM_URL", "https://llm.test")
    monkeypatch.setattr(p, "LLM_MODEL", "model")
    monkeypatch.setattr(p, "LLM_KEY", "secret")
    slides = p._llm_draft({"title": "Judul", "url": "https://example.test"}, body)
    assert [slide["evidence_ids"] for slide in slides] == [["E1"], ["E2"]]


def test_editor_rejects_stronger_judgment_than_evidence():
    assert "Desakan atau rekomendasi bukan bukti" in p.AI_EDITOR_PROMPT
    assert "lambat, gagal, tidak cukup, simbolik, atau terfragmentasi" in p.AI_EDITOR_PROMPT


def test_hashtag_rejected():
    body = "Kalimat sumber yang cukup panjang untuk validasi. " * 8
    with __import__("pytest").raises(ValueError, match="hashtag"):
        p.validate(["Fakta sumber tetap dijaga tetapi tag dilarang #politik"], {"url": "https://example.test"}, body, allow_url=False)


def test_prompt_limits_opinion_and_speculation():
    assert "Mayoritas slide wajib berupa fakta sumber" in p.PROMPT
    assert "Maksimal satu slide boleh memuat opini" in p.PROMPT
    assert "Jangan membuat pertanyaan spekulatif" in p.PROMPT
    assert "Jangan menghitung persentase" in p.PROMPT
    assert "Opini hanya boleh membandingkan fakta" in p.PROMPT
    assert "Jika tidak ada kontradiksi eksplisit" in p.PROMPT


def test_validator_enforces_one_labeled_opinion():
    body = "Kalimat sumber yang cukup panjang untuk validasi. " * 8
    item = {"url": "https://example.test"}
    with __import__("pytest").raises(ValueError, match="opinion must be labeled"):
        p.validate(["Fakta tersedia. Artinya, lembaga hanya bergerak karena tekanan luar."], item, body, allow_url=False)
    with __import__("pytest").raises(ValueError, match="at most one opinion"):
        p.validate([
            "Analisis: dua fakta eksplisit menunjukkan kontradiksi kebijakan yang jelas.",
            "Penilaian: dua fakta lain menunjukkan standar lembaga yang berbeda.",
        ], item, body, allow_url=False)