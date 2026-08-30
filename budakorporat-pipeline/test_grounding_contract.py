import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import budakorporat_pipeline as p


def test_policy_opportunity_gate():
    assert p._has_political_opportunity({"title": "DPR bahas anggaran", "description": "pengawasan pemerintah"})
    assert not p._has_political_opportunity({"title": "Polisi menangkap pencuri ruko", "description": "laporan warga"})


def test_evidence_ids_must_exist_and_be_unique(monkeypatch):
    body = "Kalimat sumber pertama cukup panjang untuk menjadi bukti utama. Kalimat sumber kedua juga cukup panjang untuk menjadi bukti lain."
    response = {"slides": [
        {"text": "a" * 40, "evidence_ids": ["E1"]},
        {"text": "b" * 40, "evidence_ids": ["E1"]},
    ]}

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, *_): return json.dumps({"choices": [{"message": {"content": json.dumps(response)}}]}).encode()

    monkeypatch.setattr(p, "urlopen", lambda *args, **kwargs: Reply())
    monkeypatch.setattr(p, "LLM_URL", "https://llm.test")
    monkeypatch.setattr(p, "LLM_MODEL", "model")
    monkeypatch.setattr(p, "LLM_KEY", "secret")
    with __import__("pytest").raises(RuntimeError, match="evidence repeated"):
        p._llm_draft({"title": "Judul", "url": "https://example.test"}, body)


def test_hashtag_rejected():
    body = "Kalimat sumber yang cukup panjang untuk validasi. " * 8
    with __import__("pytest").raises(ValueError, match="hashtag"):
        p.validate(["Fakta sumber tetap dijaga tetapi tag dilarang #politik"], {"url": "https://example.test"}, body, allow_url=False)


def test_prompt_limits_opinion_and_speculation():
    assert "Mayoritas slide wajib berupa fakta sumber" in p.PROMPT
    assert "Maksimal satu slide boleh memuat opini" in p.PROMPT
    assert "Jangan membuat pertanyaan spekulatif" in p.PROMPT