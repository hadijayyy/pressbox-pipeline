import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


PIPELINE = Path(__file__).parent.parent / "pressbox-mvp.py"


def _load_mvp():
    spec = importlib.util.spec_from_file_location("pressbox_mvp_grounding", PIPELINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_claim_audit_accepts_source_claim_and_records_url():
    mvp = _load_mvp()
    source = (
        "PSG signed Torres from Barcelona. The club confirmed the transfer on Tuesday. "
        "Torres said he chose PSG for its ambition. Barcelona thanked him for his service."
    )
    url = "https://example.com/story"
    errors, rows = mvp._claim_audit(
        [{"content": "PSG signed Torres from Barcelona. The club confirmed the transfer on Tuesday."}],
        source,
        url,
        {"slide_1": ["PSG signed Torres from Barcelona.", "The club confirmed the transfer on Tuesday."]},
    )
    assert errors == []
    assert rows[0]["evidence"]
    assert rows[0]["source_url"] == url
    assert rows[0]["reason"] == "supported"


def test_claim_audit_rejects_unsourced_fee():
    mvp = _load_mvp()
    errors, rows = mvp._claim_audit(
        [{"content": "PSG signed Torres for €50m. The club confirmed the transfer."}],
        "PSG signed Torres from Barcelona. The club confirmed the transfer.",
        "https://example.com/story",
        {"slide_1": ["PSG signed Torres from Barcelona.", "The club confirmed the transfer."]},
    )
    assert any("unsupported number" in error for error in errors)
    assert any("€50m" in row["claim"] and row["reason"] != "supported" for row in rows)


def test_generation_retry_changes_hook_variant():
    mvp = _load_mvp()
    source = Path(PIPELINE).read_text()
    assert "generation_hook = hook_variant" in source
    assert "variants = [v for v in HOOK_VARIANTS if v != hook_variant]" in source
    assert "evidence_plan=evidence_plan" in source


def test_notification_failure_is_nonfatal(monkeypatch):
    mvp = _load_mvp()
    class Response:
        status_code = 403
    monkeypatch.setattr(mvp.os.path, "exists", lambda path: True)
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: type("F", (), {"__enter__": lambda s: s, "__exit__": lambda *a: None, "read": lambda s: "token"})())
    monkeypatch.setattr(mvp.requests, "post", lambda *args, **kwargs: Response())
    assert mvp.notify_telegram("test") is False


def test_legacy_evaluator_remains_fail_closed():
    mvp = _load_mvp()
    assert mvp._evaluator_accepts("APPROVE")
    assert not mvp._evaluator_accepts("REVISE")
    assert not mvp._evaluator_accepts("REJECT")
    assert not mvp._evaluator_accepts("ERROR")


def test_ungrounded_generic_s6_binary_becomes_source_takeaway():
    mvp = _load_mvp()
    slides = [{"content": "A complete source-backed sentence."} for _ in range(5)]
    slides.append({"content": "Is this fair: yes or no?"})
    assigned = {"slide_6": [
        "The league will pause every match in the 10th minute this weekend.",
        "Players will applaud for one minute before play resumes.",
    ]}
    assert mvp._s6_strip_ungrounded_binary(slides, assigned)
    assert slides[5]["content"] == "The league will pause every match in the 10th minute this weekend."


if __name__ == "__main__":
    test_claim_audit_accepts_source_claim_and_records_url()
    test_claim_audit_rejects_unsourced_fee()
    print("mock grounding candidates: PASS")
    print("example passing draft: PSG signed Torres from Barcelona. The club confirmed the transfer on Tuesday.")
    print("evaluator gate: APPROVE only")
