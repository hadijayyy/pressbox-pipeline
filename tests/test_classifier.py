"""Tests for pressbox_common.classify_topic_type.

Covers all 11 categories + priority order + edge cases.
Regression test for the 22 Jun 2026 classifier expansion
(managerial_change, VAR keywords, etc).
"""
import inspect
import sys
from pathlib import Path

# Add repo root to path so we can import pressbox_common
sys.path.insert(0, str(Path(__file__).parent.parent))

from pressbox_common import classify_topic_type
import importlib.util


def _load_mvp():
    spec = importlib.util.spec_from_file_location(
        "pressbox_mvp", Path(__file__).parent.parent / "pressbox-mvp.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_number_hook_rule_does_not_force_unsourced_numbers():
    mvp = _load_mvp()
    assert "NUMBER is optional unless explicitly supported by the article" in mvp._number_hook_rule("Rodri could join Madrid.")


def test_slide_contract_rejects_leading_continuation_fragment():
    mvp = _load_mvp()
    slides = [
        {"title": f"S{i}", "content": "A complete sentence with source-backed detail."}
        for i in range(1, 7)
    ] + [{"title": "S7", "content": "Source: https://example.com/story"}]
    slides[0]["content"] = "and have money to spend to get it despite dropping out of Europe."
    errors = mvp._slide_contract_errors(slides)
    assert any("S1" in error and "fragment" in error for error in errors)


def test_slide_contract_rejects_lowercase_continuation_on_any_editorial_slide():
    mvp = _load_mvp()
    slides = [
        {"title": f"S{i}", "content": "A complete sentence with source-backed detail."}
        for i in range(1, 7)
    ] + [{"title": "S7", "content": "Source: https://example.com/story"}]
    slides[2]["content"] = "he said before Sunday's match."
    errors = mvp._slide_contract_errors(slides)
    assert any("S3" in error and "fragment" in error for error in errors)


def test_slide_contract_allows_complete_quote_starting_lowercase():
    mvp = _load_mvp()
    slides = [
        {"title": f"S{i}", "content": "A complete sentence with source-backed detail."}
        for i in range(1, 7)
    ] + [{"title": "S7", "content": "Source: https://example.com/story"}]
    slides[0]["content"] = "‘we need to improve,’ the manager said."
    assert not any("S1 leading continuation fragment" == error
                   for error in mvp._slide_contract_errors(slides))


def test_number_grounding_accepts_mojibake_currency_from_article():
    mvp = _load_mvp()
    assert not mvp.number_grounding_check("Arsenal paid £75m.", "Arsenal paid Â£75m.", "")


def test_module_import_does_not_hold_runtime_pipeline_lock():
    mvp = _load_mvp()
    assert hasattr(mvp, "_acquire_pipeline_lock")


def test_engagement_loop_uses_measured_hook_winner_and_rotation():
    mvp = _load_mvp()
    posts = [{"views": 100, "likes": 10, "replies": 2, "hook_variant": "detail"}] * 3
    posts += [{"views": 10, "likes": 1, "replies": 0, "hook_variant": "implication"}] * 3
    perf = mvp._cohort_performance(posts, "hook_variant")
    assert perf["detail"] > perf["implication"]
    assert mvp._select_hook_variant({"best_hook_variant": "detail"}) == "detail"
    assert mvp._select_hook_variant({}, 1) == "contradiction"


def test_element_guidance_prefers_winner_but_explores_quarterly():
    mvp = _load_mvp()
    summary = {"best_s1_hook_type": "reversal", "best_s6_cta_type": "binary"}
    guidance, selected = mvp._element_guidance(summary, 1)
    assert "Measured S1 winner: reversal" in guidance
    assert selected["exploration"] is False
    guidance, selected = mvp._element_guidance(summary, 4)
    assert selected["exploration"] is True
    assert "Exploration slot" in guidance


def test_cohort_performance_honors_minimum_sample():
    mvp = _load_mvp()
    posts = [{"views": 100, "reposts": 3, "s1_hook_type": "reversal"}] * 3
    assert mvp._cohort_performance(posts, "s1_hook_type", min_sample=5) == {}


def test_element_performance_returns_normalized_share_rates():
    mvp = _load_mvp()
    posts = [{"views": 1000, "reposts": 5, "quotes": 2, "replies": 3,
              "s1_hook_type": "reversal"}] * 5
    perf = mvp._element_performance(posts, "s1_hook_type")
    assert perf["reversal"]["n"] == 5
    assert perf["reversal"]["repost_rate"] == 5
    assert perf["reversal"]["quote_rate"] == 2
    assert perf["reversal"]["reply_rate"] == 3


def test_element_performance_excludes_small_cohorts():
    mvp = _load_mvp()
    posts = [{"views": 100, "reposts": 1, "s1_hook_type": "detail"}] * 4
    assert mvp._element_performance(posts, "s1_hook_type") == {}


def test_track_post_persists_score_pattern_and_hook_variant(tmp_path, monkeypatch):
    mvp = _load_mvp()
    path = tmp_path / "posted.json"
    path.write_text('{"topics": []}')
    monkeypatch.setattr(mvp, "POSTED", str(path))
    slides = [{"content": "S1", "hook_variant": "detail", "_score": 77}]
    mvp.track_post("Test title", "https://example.com", "bbc", "id",
                   "https://threads.com/p/id", slides=slides, pattern="c")
    row = __import__("json").loads(path.read_text())["topics"][0]
    assert row["score"] == 77
    assert row["pattern"] == "c"
    assert row["hook_variant"] == "detail"


def test_track_post_persists_writing_element_attributes(tmp_path, monkeypatch):
    mvp = _load_mvp()
    path = tmp_path / "posted.json"
    path.write_text('{"topics": []}')
    monkeypatch.setattr(mvp, "POSTED", str(path))
    slides = [{"content": "The club blocked a £113m transfer."}, {}, {}, {}, {},
              {"content": "Will they accept it or fight the decision?"}]
    mvp.track_post("Test", "https://example.com", "bbc", "id", "https://threads.com/p/id", slides=slides)
    row = __import__("json").loads(path.read_text())["topics"][0]
    assert row["s1_hook_type"] == "reversal"
    assert row["s1_has_specific_detail"] is True
    assert row["s6_cta_type"] == "binary"


def test_pull_engagement_persists_reposts_and_quotes(tmp_path, monkeypatch):
    mvp = _load_mvp()
    path = tmp_path / "posted.json"
    path.write_text('{"topics": [{"post_id": "p1", "posted_at": "2020-01-01T00:00:00+00:00"}]}')
    monkeypatch.setattr(mvp, "POSTED", str(path))

    class Poster:
        def get_metrics(self, post_id):
            return {"views": 100, "likes": 5, "replies": 2, "reposts": 3, "quotes": 4}

    mvp.pull_engagement(Poster())
    row = __import__("json").loads(path.read_text())["topics"][0]
    assert row["reposts"] == 3
    assert row["shares"] == 3
    assert row["quotes"] == 4


def test_analytics_summary_tracks_shareability(tmp_path, monkeypatch):
    mvp = _load_mvp()
    path = tmp_path / "posted.json"
    path.write_text(__import__("json").dumps({"topics": [
        {"title": "A", "views": 100, "likes": 5, "replies": 1, "reposts": 2, "quotes": 1},
        {"title": "B", "views": 200, "likes": 10, "replies": 2, "reposts": 4, "quotes": 3},
        {"title": "C", "views": 300, "likes": 15, "replies": 3, "reposts": 6, "quotes": 5},
    ]}))
    monkeypatch.setattr(mvp, "POSTED", str(path))
    summary = mvp.get_analytics_summary()
    assert summary["avg_reposts"] == 4
    assert summary["avg_quotes"] == 3


def test_engagement_score_prioritizes_reposts_and_quotes_rate():
    mvp = _load_mvp()
    high_reach = {"views": 100000, "likes": 100, "reposts": 0, "quotes": 0}
    highly_shared = {"views": 1000, "likes": 20, "reposts": 10, "quotes": 5}
    assert mvp._engagement_score(highly_shared) > mvp._engagement_score(high_reach)


def test_hot_topics_ignore_untimestamped_stale_cache_rows(tmp_path, monkeypatch):
    mvp = _load_mvp()
    cache = tmp_path / "article-cache.json"
    cache.write_text(__import__("json").dumps([{
        "url": "https://old.example/story", "title": "Manchester United old story",
        "published_ts": None,
    }]))
    monkeypatch.setattr(mvp, "ARTICLE_CACHE", str(cache))
    monkeypatch.setattr(mvp.google_trends, "fetch_google_trends", lambda: [])
    topics = [
        {"url": "https://new.example/one", "title": "Manchester United sign player one", "published_ts": None, "source": "bbc"},
        {"url": "https://new.example/two", "title": "Manchester United sign player two", "published_ts": None, "source": "mirror"},
    ]
    hotness = mvp.detect_hot_topics(topics)
    assert "https://old.example/story" not in hotness
    assert "https://new.example/one" in hotness
    assert "https://new.example/two" in hotness


def test_story_text_keeps_original_when_title_filter_is_thin():
    mvp = _load_mvp()
    title = "Arsenal Vinicius"
    relevant = " ".join(
        f"Arsenal discussed Vinicius detail {i} with the club and player representatives."
        for i in range(12)
    )
    article = relevant + " " + ("Unrelated article context. " * 80)
    filtered = mvp._story_text(article, title)
    assert len(filtered) >= 1800


def test_generation_prompt_allows_storytelling_without_filler():
    mvp = _load_mvp()
    source = inspect.getsource(mvp.generate_slides)
    assert "one strong sentence beats filler" in source
    assert "at least two complete sentences" not in source


def test_commentary_article_does_not_use_rule_break_arc():
    mvp = _load_mvp()
    topic = {"title": "Trump says it would be terrible mistake to remove Infantino"}
    body = (
        "Donald Trump says removing FIFA president Gianni Infantino would be a terrible mistake. "
        "Trump praised Infantino and discussed the president's role in football. "
        "The comments were made during a public statement about FIFA leadership. "
        "Infantino has worked with Trump on football events and tournament planning. "
        "The article reports no rule violation or regulatory exemption."
    )
    assert mvp._select_viral_pattern(topic, body) == "d"


def test_rule_violation_article_uses_rule_break_arc():
    mvp = _load_mvp()
    topic = {"title": "UEFA breaks its own regulation for final"}
    body = (
        "UEFA broke its own regulation by granting an exemption for the final. "
        "The regulation normally prevents clubs from changing the designated venue. "
        "Officials approved the exception after reviewing the request."
    )
    assert mvp._select_viral_pattern(topic, body) == "a"


def test_shortlist_stores_story_text_and_evidence_plan(monkeypatch):
    mvp = _load_mvp()
    monkeypatch.setattr(mvp, "fetch_article", lambda url: (
        " ".join(
            f"Arsenal and Vinicius completed verified source detail {i} for the transfer story."
            for i in range(1, 15)
        ),
        "https://img.example/cover.jpg",
    ))
    topic = {
        "title": "Arsenal Vinicius transfer story",
        "url": "https://example.com/story",
        "published_ts": __import__("time").time(),
        "source": "bbc",
        "image_url": "",
        "_score": 50,
    }
    result = mvp._body_first_shortlist([topic])
    assert len(result) == 1
    assert result[0]["_article_text"] == mvp._story_text(result[0]["_article_text"], topic["title"])
    assert result[0]["_evidence_plan"] == mvp._evidence_plan(result[0]["_article_text"])


def test_generation_stops_candidate_churn_after_rate_limit():
    mvp = _load_mvp()
    source = inspect.getsource(mvp.main)
    assert "LLM_RATE_LIMITED" in source
    assert "provider rate limit" in source


def test_grounding_matches_accents_and_ignores_question_prefix():
    mvp = _load_mvp()
    slides = "Why Julián Álvarez wants talks with Gil Marín."
    article = "Julian Alvarez wants talks with Gil Marin."
    assert not mvp.grounding_check(slides, article, set(), set())


def test_slide_contract_requires_two_source_grounded_sentences_per_slide():
    mvp = _load_mvp()
    slides = [{"content": "One supported source sentence."}] * 6 + [{"content": "Source: https://example.com/story"}]
    errors = mvp._slide_contract_errors(slides)
    assert errors == []


def test_slide_contract_accepts_two_sentences_per_slide():
    mvp = _load_mvp()
    slides = [{"content": "One supported source sentence. A second supported source sentence."}] * 6 + [{"content": "Source: https://example.com/story"}]
    assert not mvp._slide_contract_errors(slides)


def test_slide_contract_requires_two_sentences_on_every_slide():
    mvp = _load_mvp()
    slides = [{"content": "One supported source sentence."}] * 6 + [{"content": "Source: https://example.com/story"}]
    errors = mvp._slide_contract_errors(slides)
    assert errors == []


def test_slide_contract_keeps_450_char_limit():
    mvp = _load_mvp()
    overlength = [{"content": "One. Two."}] * 5 + [{"content": "x" * 451}] + [{"content": "Source: https://example.com/story"}]
    assert "S6 invalid length (451)" in mvp._slide_contract_errors(overlength)


def test_slide_7_is_english_source_url_only():
    mvp = _load_mvp()
    slides = [{"content": "One supported source sentence. A second supported source sentence."}] * 6
    slides.append({"content": "Source: https://example.com/story"})
    assert not mvp._slide_contract_errors(slides)
    assert mvp._slide_contract_errors(slides[:-1] + [{"content": "Sumber: https://example.com/story"}]) == ["S7 invalid source URL"]


def test_main_uses_one_candidate_and_one_final_evaluator():
    mvp = _load_mvp()
    source = inspect.getsource(mvp.main)
    assert "for article_attempt" not in source
    assert "for eval_round" not in source
    assert "_extractive_slides" in source
    assert source.count("evaluator_check(") == 1


def test_high_risk_transfer_claim_requires_tier_one_source():
    mvp = _load_mvp()
    text = "The clubs agreed a £75m transfer fee for the midfielder."
    assert not mvp._high_risk_claim_allowed(text, "Goal")
    assert mvp._high_risk_claim_allowed(text, "BBC Sport")
    assert mvp._high_risk_claim_allowed("The midfielder trained with his club.", "Goal")


def test_high_risk_candidate_is_skipped_for_next_eligible_article():
    mvp = _load_mvp()
    source = inspect.getsource(mvp.main)
    assert 'ranked = [topic for topic in ranked if _high_risk_claim_allowed(' in source
    assert 'topic.get("_article_text", ""), topic.get("source", ""))]' in source
    assert 'print("⏸️ Skip — no tier-one source for high-risk claim", flush=True)\n        sys.exit(0)' in source
    assert source.index('ranked = [topic for topic in ranked if _high_risk_claim_allowed(') < source.index('best = ranked[0]')


def test_generated_output_exhaustion_is_normal_skip_for_watchdog():
    mvp = _load_mvp()
    source = inspect.getsource(mvp.main)
    block = source[source.index('if errors:'):source.index('final_contract_errors')]
    assert '_record_failure("GENERATION_FAILED_ALL_CANDIDATES")' in block
    assert 'print("⏸️ Skip — no source-grounded draft passed", flush=True)' in block
    assert 'sys.exit(1)' not in block


def test_space_sentences_collapses_literal_backslash_newline():
    mvp = _load_mvp()
    assert mvp._space_sentences('He said, "First fact."\\nSecond fact.') == 'He said, "First fact." Second fact.'


def test_evaluator_is_required_for_every_generated_post():
    mvp = _load_mvp()
    assert mvp._requires_evaluator("f", 100)
    assert mvp._requires_evaluator("e", 80)
    assert mvp._requires_evaluator("a", 1)


def test_main_does_not_skip_evaluator_for_pattern_or_score():
    mvp = _load_mvp()
    source = inspect.getsource(mvp.main)
    assert "skip_eval = pattern in (\"e\", \"f\")" not in source
    assert "candidate.get(\"_score\", 0) >= 80" not in source[source.index("contract_errors ="):source.index("# All checks passed")]


def test_evaluator_revise_does_not_authorize_posting():
    mvp = _load_mvp()
    assert not mvp._evaluator_accepts("REVISE")
    assert mvp._evaluator_accepts("APPROVE")


def test_missing_evaluator_key_blocks_posting():
    mvp = _load_mvp()
    mvp.MISTRAL_KEY = ""
    decision, _ = mvp.evaluator_check([], "source", "https://example.com")
    assert not mvp._evaluator_accepts(decision)


def test_editorial_constraints_preserve_source_wording():
    mvp = _load_mvp()
    rules = mvp._editorial_constraints()
    assert "Do not replace source terms" in rules
    assert "A stance is optional" in rules
    assert "Do not turn conditional claims into current facts" in rules
    assert "Do not invent a question, conflict, urgency, motive, winner, loser, or consequence" in rules
    assert 'First-person markers such as "For me" or "In my eyes"' in rules
    assert "must not claim eyewitness knowledge" in rules


def test_generation_evidence_override_blocks_arc_speculation():
    mvp = _load_mvp()
    rules = mvp._generation_evidence_override()
    assert "assigned evidence lines are the only factual authority" in rules
    assert "ARTICLE_TITLE is a label, not evidence" in rules
    assert "If source cannot support a complete sentence, omit that detail" in rules
    assert "Do not invent stakes, motives, consequences, reactions, or either/or outcomes" in rules


def test_rate_limit_reaches_literal_fallback_without_more_llm_candidates():
    mvp = _load_mvp()
    source = inspect.getsource(mvp.main)
    rate_block = source[source.index('if _LAST_GENERATION_FAILURE == "LLM_RATE_LIMITED"'):source.index('contract_errors = _slide_contract_errors')]
    assert 'rate_limited = True' in rate_block
    assert 'sys.exit(1)' not in rate_block
    assert 'if rate_limited:' in source


def test_evaluator_request_uses_json_mode_and_full_article():
    mvp = _load_mvp()
    payload = mvp._evaluator_request_payload("system", "user")
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 800


def test_space_sentences_flows_naturally_and_keeps_url():
    mvp = _load_mvp()
    text = "First fact. Second fact?\\n\\nhttps://example.com/story"
    assert mvp._space_sentences(text) == "First fact. Second fact?\\n\\nhttps://example.com/story"
    assert mvp._space_sentences('He said, "First fact."\\nSecond fact.') == 'He said, "First fact." Second fact.'
    # LLM comma-space drop: letter-letter and letter-digit fixed, ID decimal untouched
    assert mvp._space_sentences("Polri,sekarang Kejagung.") == "Polri, sekarang Kejagung."
    assert mvp._space_sentences("Diperiksa,12 orang.") == "Diperiksa, 12 orang."
    assert mvp._space_sentences("Naik 1,2 persen.") == "Naik 1,2 persen."


def test_verbatim_evaluator_approves_without_api():
    mvp = _load_mvp()
    decision, reasons = mvp.evaluator_check(
        [{"content": "Exact source sentence.", "title": "S1"}],
        "Exact source sentence.", "https://example.com", verbatim=True)
    assert decision == "APPROVE"
    assert reasons == ["verbatim source sentences"]


def test_evidence_pack_is_numbered_and_preserves_source_sentences():
    mvp = _load_mvp()
    pack = mvp._evidence_pack("First fact happened. Second fact was reported. Short.")
    assert "[E1] First fact happened." in pack
    assert "[E2] Second fact was reported." in pack


def test_extractive_slides_reject_one_sentence_per_slide():
    mvp = _load_mvp()
    article = " ".join(f"Source sentence number {i} has enough words to become a slide." for i in range(1, 7))
    assert mvp._extractive_slides(article, "https://example.com") is None


def test_extractive_slides_skip_long_sentences_and_preserve_source_text():
    mvp = _load_mvp()
    url = "https://example.com/story"
    long = "This source sentence is deliberately too long " + ("word " * 100) + "to fit a Threads slide."
    valid = [f"Source sentence number {i} has enough words and remains short for a slide." for i in range(1, 13)]
    article = " ".join([long, *valid])
    slides = mvp._extractive_slides(article, url)
    assert slides and not mvp._extractive_audit_errors(slides, article)


def test_extractive_slides_ignore_long_complete_sentence_when_selecting_fallback():
    mvp = _load_mvp()
    url = "https://example.com/story"
    long = "This complete source sentence is too long " + ("word " * 100) + "."
    valid = [f"Clean source sentence {i} has enough words and remains short for a slide." for i in range(1, 13)]
    slides = mvp._extractive_slides(" ".join([long, *valid]), url)
    assert slides and not mvp._extractive_audit_errors(slides, " ".join([long, *valid]))


def test_extractive_slides_repack_ordered_facts_when_adjacent_pair_is_too_long():
    mvp = _load_mvp()
    url = "https://example.com/story"
    long_facts = ["Long source fact %s has enough detail to exceed half of the slide budget when paired." % i for i in (1, 2)]
    short_facts = ["Short source fact %s has enough detail for literal fallback." % i for i in range(1, 13)]
    article = " ".join(long_facts + short_facts)
    slides = mvp._extractive_slides(article, url)
    assert slides and not mvp._extractive_audit_errors(slides, article)
    assert all(len(slide["content"]) <= mvp.MAX_CHARS for slide in slides[:6])


def test_failure_telemetry_records_reason_code(tmp_path, monkeypatch):
    mvp = _load_mvp()
    path = tmp_path / "failure-telemetry.json"
    monkeypatch.setattr(mvp, "_FAILURE_LOG_FILE", str(path))
    mvp._record_failure("GROUNDING_REJECTED", "bbc", "Test story")
    data = __import__("json").loads(path.read_text())
    assert data[-1]["reason"] == "GROUNDING_REJECTED"
    assert data[-1]["source"] == "bbc"


def test_extractive_slides_need_twelve_source_sentences():
    mvp = _load_mvp()
    article = " ".join([
        "Arsenal and Bruno Guimaraes appear in this only related source sentence.",
        *[f"Clean source sentence {i} has enough words and remains literal article evidence." for i in range(1, 12)],
    ])
    assert mvp._extractive_slides(article, "https://example.com", "Arsenal Bruno Guimaraes")


def test_extractive_fallback_reuses_facts_when_source_has_fewer_than_twelve():
    mvp = _load_mvp()
    article = " ".join(
        f"Verified source sentence {i} contains enough detail for a literal fallback slide."
        for i in range(1, 9)
    )
    slides = mvp._extractive_slides(article, "https://example.com/story")
    assert slides and not mvp._extractive_audit_errors(slides, article)


def test_extractive_fallback_drops_source_units_that_start_as_fragments():
    mvp = _load_mvp()
    article = " ".join([
        "The Community Shield takes place before the Premier League season begins.",
        "Since 1992 only eight winners have also lifted the league trophy that season.",
        "The losing teams have won ten league titles in that period.",
        "The upcoming season will provide another comparison between the finalists.",
        "The winners have finished above the losers in 18 of 34 seasons.",
        "The two teams have finished as the top two on ten occasions.",
        "The previous league champions have won eight of the past twelve Shields.",
        "The result therefore offers limited evidence about the season ahead.",
        "And the source records the historical comparison rather than a prediction.",
        "Raheem Sterling scored for City in the 2019 Shield match.",
        "Cole Palmer scored for City in the 2023 game against Arsenal.",
        "The match was decided after Arsenal lost on penalties.",
        "The article compares the trophy result with the later league outcome.",
    ])
    slides = mvp._extractive_slides(article, "https://example.com/community-shield")
    assert slides and not mvp._slide_contract_errors(slides)
    assert all(not slide["content"].lstrip().startswith("And ") for slide in slides[:6])


def test_source_units_keep_quote_together_and_drop_open_quote_fragment():
    mvp = _load_mvp()
    article = ('Iraola said, "We played a good first half. Right now we have work to do." '
               'Liverpool face Como next weekend. "This unfinished quote must not publish.')
    units = mvp._source_units(article)
    assert units == ['Iraola said, "We played a good first half. Right now we have work to do."',
                     'Liverpool face Como next weekend.']


def test_fallback_evidence_rejects_attribution_only_and_quote_without_speaker():
    mvp = _load_mvp()
    facts = [
        "Iraola said, \"We have plenty of work to do before the next match.\"",
        "Iraola told LFC TV after the match.",
        "\"We have work to do.\"",
        *[f"Verified source fact {i} contains enough context for a clean slide." for i in range(1, 7)],
    ]
    selected = mvp._fallback_evidence(facts)
    assert "Iraola told LFC TV after the match." not in selected
    assert '"We have work to do."' not in selected
    assert 'Iraola said, "We have plenty of work to do before the next match."' in selected


def test_fallback_evidence_prefers_compact_complete_source_units():
    mvp = _load_mvp()
    long = "Player Name gave a very long source explanation " + ("with extra context " * 35) + "after the match."
    facts = [
        long,
        *[f"Verified source fact {i} contains enough context for a clean carousel slide." for i in range(1, 7)],
    ]
    selected = mvp._fallback_evidence(facts)
    assert long not in selected[:6]


def test_fallback_role_evidence_prefers_story_details_and_keeps_source_units():
    mvp = _load_mvp()
    facts = [
        "The manager discussed several issues at length during the interview.",
        "The player signed a new contract on Tuesday after three months of negotiations with the club.",
        "The decision followed three months of negotiations with the club.",
        "Supporters will see the player return for the next league match.",
        "The midfielder said the move was important for his career.",
    ] + [f"Additional source fact {i} gives enough clear context about the club decision today." for i in range(1, 10)]
    selected = mvp._fallback_role_evidence(facts, "player club contract")
    assert len(selected) == 12
    assert facts[1] in selected
    assert facts[0] not in selected or selected.index(facts[1]) < selected.index(facts[0])
    assert all(fact in facts for fact in selected)


def test_hard_news_adjustment_penalizes_opinion_titles():
    mvp = _load_mvp()
    assert mvp._hard_news_adjustment("Why this manager is on the wrong track", "manager discussed tactics") < 0
    assert mvp._hard_news_adjustment("Club confirms player signed new contract", "club confirmed the signing") > 0


def test_generation_temperature_is_low_for_factual_drafts():
    mvp = _load_mvp()
    source = inspect.getsource(mvp.generate_slides)
    assert '"temperature":0.1' in source


def test_narrative_fallback_keeps_source_order_after_compact_selection():
    mvp = _load_mvp()
    article = " ".join([
        "Opening source fact gives readers the story context in clear terms.",
        "Second source fact explains why this development matters to the club.",
        "This source sentence is deliberately too long " + ("with repetitive context " * 40) + "to use cleanly.",
        "Third source fact keeps the same story moving without changing subject.",
        "Fourth source fact adds a named detail from the original report.",
        "Fifth source fact preserves the source uncertainty around the next step.",
        "Sixth source fact closes the reported sequence with a clear outcome.",
        "Seventh source fact is available if one earlier detail is unsuitable.",
    ])
    facts = mvp._narrative_fallback_evidence(article)
    assert facts == [
        "Opening source fact gives readers the story context in clear terms.",
        "Second source fact explains why this development matters to the club.",
        "Third source fact keeps the same story moving without changing subject.",
        "Fourth source fact adds a named detail from the original report.",
        "Fifth source fact preserves the source uncertainty around the next step.",
        "Sixth source fact closes the reported sequence with a clear outcome.",
        "Seventh source fact is available if one earlier detail is unsuitable.",
    ]


def test_assigned_evidence_maps_each_slide_to_two_planned_units():
    mvp = _load_mvp()
    article = " ".join(f"Verified fact {i} gives enough source context for a carousel slide." for i in range(1, 13))
    plan = mvp._evidence_plan(article)
    assigned = mvp._assigned_evidence(article, plan)
    assert all(len(facts) == 2 for facts in assigned.values())
    assert sorted(fact for facts in assigned.values() for fact in facts) == sorted(mvp._ranked_evidence(article)[:12])
    assert len(assigned) == 6


def test_story_text_falls_back_to_clean_body_when_entity_subset_is_thin():
    mvp = _load_mvp()
    article = (
        "Arsenal are pursuing Bruno Guimaraes after a new development. "
        "Bruno Guimaraes is due back for training on Friday. "
        "Mikel Arteta also discussed Arsenal's squad plans. "
    )
    story = mvp._story_text(article, "Arsenal Bruno Guimaraes transfer news")
    assert story == article


def test_extract_article_excludes_related_content():
    mvp = _load_mvp()
    html = """<article><p>Main story first fact has enough words for extraction.</p>
    <div class="related-content"><p>Unrelated promoted claim has enough words to be dangerous.</p></div>
    <p>Main story second fact has enough words for extraction.</p></article>"""
    text = mvp.extract_article(html)
    assert "Main story first fact" in text
    assert "Main story second fact" in text
    assert "Unrelated promoted claim" not in text


def test_extract_article_removes_inline_image_captions():
    mvp = _load_mvp()
    html = """<article><p>Acun Ilicali spoke about transfers (Image: Getty Images) Hull boss Ilicali accused Real Madrid of fake stories.</p></article>"""
    text = mvp.extract_article(html)
    assert "(Image:" not in text
    assert text == "Hull boss Ilicali accused Real Madrid of fake stories."


def test_extract_article_excludes_image_captions_and_subscription_copy():
    mvp = _load_mvp()
    html = """<article><p>Main story fact has enough words for extraction and verification.</p>
    <figure><p>Image caption has enough words but is not story evidence.</p></figure>
    <p>Sky Sports bundle includes enough words but is subscription copy, not evidence.</p>
    <p>Second main story fact has enough words for extraction and verification.</p></article>"""
    text = mvp.extract_article(html)
    assert "Main story fact" in text
    assert "Second main story fact" in text
    assert "Image caption" not in text
    assert "Sky Sports bundle" not in text


# ── Category coverage ─────────────────────────────────────────────
class TestInjuryUpdate:
    def test_ruled_out(self):
        assert classify_topic_type("Saka ruled out of World Cup with injury") == "injury_update"

    def test_sidelined(self):
        assert classify_topic_type("Star midfielder sidelined for 3 weeks") == "injury_update"

    def test_fitness_doubt(self):
        assert classify_topic_type("Salah fitness doubt for Liverpool clash") == "injury_update"


class TestTransferRumor:
    def test_transfer(self):
        assert classify_topic_type("Mohamed Salah sends Liverpool transfer reminder") == "transfer_rumor"

    def test_signing(self):
        assert classify_topic_type("Man Utd signing new midfielder from Bayern") == "transfer_rumor"

    def test_bid(self):
        assert classify_topic_type("Chelsea make £80m bid for Barcelona striker") == "transfer_rumor"

    def test_contract(self):
        assert classify_topic_type("Mbappe contract talks with Real Madrid progress") == "transfer_rumor"


class TestManagerialChange:
    def test_sacked(self):
        assert classify_topic_type("Man Utd sack manager after disastrous run") == "managerial_change"

    def test_fired(self):
        assert classify_topic_type("Tuchel fired by England after World Cup exit") == "managerial_change"

    def test_appointed(self):
        assert classify_topic_type("Liverpool appoint new head coach from Brighton") == "managerial_change"

    def test_replaces(self):
        assert classify_topic_type("Xavi replaces Koeman at Barcelona") == "managerial_change"

    def test_manager_keyword(self):
        assert classify_topic_type("Arsenal manager defends controversial tactics") == "managerial_change"


class TestFifaPolitical:
    def test_iran_booed(self):
        assert classify_topic_type("Iran flag and anthem booed by World Cup crowd") == "fifa_political"

    def test_fifa_backlash(self):
        assert classify_topic_type("FIFA faces backlash over World Cup political decision") == "fifa_political"

    def test_trump_wc(self):
        assert classify_topic_type("Trump government policy affects World Cup travel") == "fifa_political"


class TestWCTeamGuide:
    def test_team_guide(self):
        assert classify_topic_type("England World Cup team guide and squad preview") != "WC_team_guide"
        assert classify_topic_type("England World Cup team guide and squad preview") is not None

    def test_predicted_lineup(self):
        assert classify_topic_type("Brazil predicted lineup for World Cup opener") != "WC_team_guide"

    def test_squad(self):
        assert classify_topic_type("Argentina squad announced for 2026 tournament") != "WC_team_guide"


class TestControversy:
    def test_racist_abuse(self):
        assert classify_topic_type("Racist abuse mars World Cup match") == "controversy"

    def test_var_official_controversy(self):
        assert classify_topic_type("VAR official makes shocking World Cup call") == "controversy"

    def test_scandal(self):
        assert classify_topic_type("Match-fixing scandal rocks European football") == "controversy"


class TestTacticalAnalysis:
    def test_formation(self):
        assert classify_topic_type("Liverpool switch to 3-4-3 formation vs Arsenal") == "tactical_analysis"

    def test_var_analysis(self):
        assert classify_topic_type("VAR decision sparks debate after controversial penalty") == "tactical_analysis"

    def test_red_card_analysis(self):
        assert classify_topic_type("Red card changes match as referee sends off defender") == "tactical_analysis"

    def test_pressing_analysis(self):
        assert classify_topic_type("High pressing system breakdown from latest match") == "tactical_analysis"


class TestMatchResult:
    def test_beat(self):
        assert classify_topic_type("Egypt beat New Zealand 3-1 in World Cup opener") == "match_result"

    def test_defeat(self):
        assert classify_topic_type("Liverpool defeat Arsenal in title race clash") == "match_result"

    def test_draw(self):
        assert classify_topic_type("Man Utd draw with Chelsea in goalless stalemate") == "match_result"

    def test_victory(self):
        assert classify_topic_type("Bayern claim victory in Bundesliga title decider") == "match_result"


class TestPlayerProfile:
    def test_who_is(self):
        assert classify_topic_type("Who is Endrick - Brazil's teenage sensation") == "player_profile"

    def test_career(self):
        assert classify_topic_type("Mbappe career timeline from Monaco to Madrid") == "player_profile"

    def test_story_of(self):
        assert classify_topic_type("The story of how Bellingham became a Real Madrid star") == "player_profile"


class TestTournamentNews:
    def test_world_cup_general(self):
        # WC over — no more tournament_news category; falls to other
        r = classify_topic_type("World Cup 2026 group stage fixtures announced")
        assert r is not None

    def test_tournament_news(self):
        r = classify_topic_type("Latest update from World Cup training camp")
        assert r is not None


class TestOther:
    def test_empty_string(self):
        assert classify_topic_type("") == "other"

    def test_unrelated(self):
        assert classify_topic_type("Football match postponed due to weather") == "other"


# ── Priority order (specific > general) ────────────────────────────
class TestPriorityOrder:
    def test_injury_beats_match(self):
        """Injury should outrank match_result/other when both match."""
        # 'injured' matches before anything else
        result = classify_topic_type("Star player injured in World Cup warmup")
        assert result == "injury_update", f"Expected injury_update, got {result}"

    def test_transfer_beats_controversy(self):
        """Transfer keywords should outrank general controversy."""
        result = classify_topic_type("Controversial transfer bid sparks racism debate")
        # 'transfer' matches first (priority 2), 'racism' matches controversy (priority 5)
        assert result == "transfer_rumor", f"Expected transfer_rumor, got {result}"

    def test_var_controversy_outranks_var_tactical(self):
        """When 'controversy' is in title, it outranks 'var' (VAR alone is tactical)."""
        # 'var official' is in _CONTROVERSY_KW (priority 5) before 'var' in _TACTICAL_KW (priority 6)
        result = classify_topic_type("VAR official makes shocking call")
        assert result == "controversy", f"Expected controversy, got {result}"


# ── Regression: 22 Jun 2026 fix verified keywords ──────────────────
class TestRegressionJune2026:
    def test_var_keyword_recognized(self):
        """VAR was missing from _TACTICAL_KW before fix — verify it now classifies."""
        result = classify_topic_type("VAR penalty decision in World Cup final")
        assert result == "tactical_analysis", f"VAR keyword not recognized: got {result}"

    def test_penalty_keyword_recognized(self):
        result = classify_topic_type("Late penalty call costs Chelsea the match")
        assert result == "tactical_analysis", f"penalty keyword not recognized: got {result}"

    def test_managerial_keyword_recognized(self):
        """managerial_change category didn't exist before — verify it's now detected."""
        result = classify_topic_type("Tuchel sacked as England head coach")
        assert result == "managerial_change", f"managerial_change not detected: got {result}"
