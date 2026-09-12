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


def test_claim_audit_rejects_number_bound_to_wrong_attribute():
    """Regression: source age -> slide match minute (24-year-old -> 24th minute)."""
    mvp = _load_mvp()
    source = (
        "Archie Brown scored a crucial equaliser as Fenerbahce held Roma to a 1-1 draw. "
        "Shortly after the restart, 24-year-old Brown dinked the ball over Mile Svilar "
        "following a clever pass from Mason Greenwood to level the scoreline."
    )
    errors, rows = mvp._claim_audit(
        [{"content": "Brown's equaliser (24th minute) came after Mason Greenwood's pass."}],
        source,
        "https://example.com/brown",
        {"slide_1": [source]},
    )
    assert any("unsupported number context" in error for error in errors), errors
    assert rows[0]["reason"] == "unsupported number context: slide says minute 24, source records no 24 minute"


def test_claim_audit_allows_genuine_source_minute():
    mvp = _load_mvp()
    source = "He scored in the 24th minute after a clever pass from a team-mate."
    errors, rows = mvp._claim_audit(
        [{"content": "He scored in the 24th minute after a clever pass from a team-mate."}],
        source,
        "https://example.com/minute",
        {"slide_1": [source]},
    )
    assert errors == [], errors


def test_claim_audit_rejects_word_number_quantity_not_in_source():
    """Regression: source had no duration at all; slide invented "seven months"."""
    mvp = _load_mvp()
    source = (
        "James Rodriguez is unattached and has not played competitively since early July, "
        "though he has been training alone to stay fit while he waits for a new club."
    )
    errors, rows = mvp._claim_audit(
        [{"content": "Rodriguez looks rusty after seven months out of action and needs a new club."}],
        source,
        "https://example.com/james",
        {"slide_1": [source]},
    )
    assert any("unsupported quantity" in error for error in errors), errors
    assert rows[0]["reason"].startswith("unsupported quantity: slide says 7 month")


def test_claim_audit_rejects_half_unit_downgrade():
    """Regression: source "an hour and a half each way" -> slide "an hour each way"."""
    mvp = _load_mvp()
    source = (
        "Her mother drove her four times a week to training, an hour and a half each way, "
        "with barely enough fuel money to make the trip."
    )
    errors, rows = mvp._claim_audit(
        [{"content": "Her mother drove her four times a week to training, an hour each way, with barely enough fuel money."}],
        source,
        "https://example.com/cooney",
        {"slide_1": [source]},
    )
    assert any("unsupported quantity" in error for error in errors), errors


def test_claim_audit_allows_digit_and_word_number_equivalence():
    mvp = _load_mvp()
    source = "The striker is expected to be out for three months after the scan result."
    errors, rows = mvp._claim_audit(
        [{"content": "The striker is expected to be out for 3 months after the scan result."}],
        source,
        "https://example.com/scan",
        {"slide_1": [source]},
    )
    assert errors == [], errors


def test_story_guard_blocks_same_saga_inside_24h(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta
    mvp = _load_mvp()
    now = datetime.now().astimezone()
    posted = tmp_path / "posted_topics.json"
    posted.write_text(json.dumps({"topics": [{
        "title": "Fenerbahce boss stuns club by quitting after Roma draw",
        "url": "https://www.bbc.com/sport/football/articles/c8r6vyjpye2o",
        "posted_at": (now - timedelta(hours=3)).isoformat(),
    }]}))
    monkeypatch.setattr(mvp, "POSTED", str(posted))
    assert mvp._story_guard_blocks(
        "Fenerbahce manager resigns from fourth stint in charge",
        "https://www.mirror.co.uk/sport/football/news/fenerbahce-manager-archie-brown-37650105",
    )


def test_story_guard_allows_different_event_for_same_entity(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta
    mvp = _load_mvp()
    now = datetime.now().astimezone()
    posted = tmp_path / "posted_topics.json"
    posted.write_text(json.dumps({"topics": [{
        "title": "Sesko sends clear message to Carrick on night of few revelations",
        "url": "https://www.mirror.co.uk/sport/football/news/benjamin-sesko-man-utd-carrick-35123456",
        "posted_at": (now - timedelta(hours=2)).isoformat(),
    }]}))
    monkeypatch.setattr(mvp, "POSTED", str(posted))
    assert not mvp._story_guard_blocks(
        "Two goals in two games: Sesko makes his case",
        "https://www.bbc.com/sport/football/articles/c770d63d850o",
    )


def _posted_fixture(tmp_path, monkeypatch, mvp, entries):
    import json
    posted = tmp_path / "posted_topics.json"
    posted.write_text(json.dumps({"topics": entries}))
    monkeypatch.setattr(mvp, "POSTED", str(posted))


def test_cross_post_guard_rejects_contradicting_goal_role(tmp_path, monkeypatch):
    """Regression: 06:28 published 'Brown's equaliser', 11:44 published 'opener'."""
    from datetime import datetime, timedelta
    mvp = _load_mvp()
    now = datetime.now().astimezone()
    _posted_fixture(tmp_path, monkeypatch, mvp, [{
        "title": "Fenerbahce manager resigns from fourth stint hours after Mason Greenwood decision",
        "url": "https://www.mirror.co.uk/sport/football/news/fenerbahce-mason-greenwood-37650105",
        "posted_at": (now - timedelta(hours=5)).isoformat(),
        "slides": [
            "His resignation came after a 1-1 draw with Roma, where Greenwood assisted Archie Brown's equaliser against Roma.",
        ],
    }])
    errors = mvp._cross_post_conflict_errors(
        [{"content": "Brown, who scored the opener against Roma, was told by the club's media assistant mid-interview."}],
        "Baffled Champions League star learns his manager resigned",
        "https://www.mirror.co.uk/sport/football/news/fenerbahce-manager-archie-brown-news-37650130",
        now=now,
    )
    assert errors, "expected contradiction"
    assert "equaliser" in errors[0] and "opener" in errors[0]


def test_cross_post_guard_allows_different_scorer(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    mvp = _load_mvp()
    now = datetime.now().astimezone()
    _posted_fixture(tmp_path, monkeypatch, mvp, [{
        "title": "Fenerbahce manager resigns after Roma draw",
        "url": "https://www.theguardian.com/football/2026/sep/10/fenerbahce-coach-resigns-ismail-kartal-roma-champions-league",
        "posted_at": (now - timedelta(hours=5)).isoformat(),
        "slides": ["Roma took the lead in the 39th minute and Fenerbahce equalised before half-time."],
    }])
    errors = mvp._cross_post_conflict_errors(
        [{"content": "Cristante scored the opener for Roma after 39 minutes."}],
        "Kartal resigns",
        "https://www.theguardian.com/football/2026/sep/10/kartal",
        now=now,
    )
    assert errors == [], errors


def test_cross_post_guard_ignores_posts_outside_24h(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    mvp = _load_mvp()
    now = datetime.now().astimezone()
    _posted_fixture(tmp_path, monkeypatch, mvp, [{
        "title": "Fenerbahce beat Roma",
        "url": "https://www.mirror.co.uk/sport/football/news/greenwood-37650105",
        "posted_at": (now - timedelta(hours=30)).isoformat(),
        "slides": ["Greenwood assisted Archie Brown's equaliser against Roma."],
    }])
    errors = mvp._cross_post_conflict_errors(
        [{"content": "Brown scored the opener against Roma."}],
        "Brown interview",
        "https://www.mirror.co.uk/sport/football/news/brown-37650130",
        now=now,
    )
    assert errors == [], errors


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


def test_internal_evidence_tag_is_stripped_from_copy():
    """Regression (2026-09-12): model copied EVIDENCE_PACK labels into slides.
    16 live slides across 5 posts shipped '(E2)', '(E3, E15)' etc."""
    mvp = _load_mvp()
    assert mvp._strip_internal_tags(
        "That means Alonso's move (E2) backfired and loyalty (E13) met a bench."
    ) == "That means Alonso's move backfired and loyalty met a bench."
    assert mvp._strip_internal_tags(
        "The logic here: workload is high (E3, E15)."
    ) == "The logic here: workload is high."
    assert mvp._strip_internal_tags("Flick's record [E4] is a mirage.") == \
        "Flick's record is a mirage."


def test_internal_tag_stripper_leaves_clean_copy_untouched():
    """The stripper must not normalise spacing in copy that has no tag."""
    mvp = _load_mvp()
    clean = "Inter's mental battle begins  -  not against ghosts."
    assert mvp._strip_internal_tags(clean) == clean
    assert mvp._strip_internal_tags("No tags here at all.") == "No tags here at all."


def test_internal_tag_validator_fails_closed():
    mvp = _load_mvp()
    tagged = [{"content": "That means Alonso's move (E2) backfired."}]
    assert mvp._validate_no_internal_tags(tagged)
    assert mvp._validate_no_internal_tags([{"content": "Clean sentence."}]) == []


def test_club_binding_flags_wrong_club_attribution():
    """Regression (2026-09-12): Guardian article listed the CL game counts of the
    managers of Man City (6), Man United (1), Liverpool (0), then separately
    Guardiola's 191. A slide rendered it 'Manchester United's Pep Guardiola'."""
    mvp = _load_mvp()
    source = (
        "Inter's Cristian Chivu has 10 Champions League games under his belt, which "
        "is more than the managers of Manchester City (six), Manchester United (one) "
        "and Liverpool (none). Ancelotti leads on 218 Champions League games, ahead "
        "of Alex Ferguson (206), Guardiola and Arsene Wenger (both 191)."
    )
    slides = [{"content": "join Manchester United's Pep Guardiola as a one-game manager."}]
    errors = mvp._club_binding_errors(slides, source)
    assert errors, "wrong-club attribution must be flagged"
    assert "ROLE_BINDING_S1" in errors[0]


def test_club_binding_allows_correct_attribution():
    mvp = _load_mvp()
    source = "Manchester City manager Pep Guardiola won the competition twice."
    slides = [{"content": "Manchester City's Pep Guardiola is in the draw again."}]
    assert mvp._club_binding_errors(slides, source) == []


def test_club_binding_ignores_plain_hallucination():
    """A person absent from the source is grounding_check's job, not this gate."""
    mvp = _load_mvp()
    source = "Arsenal drew with Chelsea on Sunday."
    slides = [{"content": "Arsenal's Zinedine Zebra scored twice."}]
    assert mvp._club_binding_errors(slides, source) == []


if __name__ == "__main__":
    test_claim_audit_accepts_source_claim_and_records_url()
    test_claim_audit_rejects_unsourced_fee()
    print("mock grounding candidates: PASS")
    print("example passing draft: PSG signed Torres from Barcelona. The club confirmed the transfer on Tuesday.")
    print("evaluator gate: APPROVE only")
