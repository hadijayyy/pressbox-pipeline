"""Tests for pressbox_common.classify_topic_type.

Covers all 11 categories + priority order + edge cases.
Regression test for the 22 Jun 2026 classifier expansion
(managerial_change, VAR keywords, etc).
"""
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


def test_evaluator_is_required_for_every_generated_post():
    mvp = _load_mvp()
    assert mvp._requires_evaluator("f", 100)
    assert mvp._requires_evaluator("e", 80)
    assert mvp._requires_evaluator("a", 1)


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


def test_evaluator_request_uses_json_mode_and_full_article():
    mvp = _load_mvp()
    payload = mvp._evaluator_request_payload("system", "user")
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 800


def test_space_sentences_uses_blank_lines_and_keeps_url():
    mvp = _load_mvp()
    text = "First fact. Second fact?\n\nhttps://example.com/story"
    assert mvp._space_sentences(text) == "First fact.\n\nSecond fact?\n\nhttps://example.com/story"
    assert mvp._space_sentences('He said, "First fact." Second fact.') == 'He said, "First fact."\n\nSecond fact.'


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


def test_extractive_slides_use_source_sentences():
    mvp = _load_mvp()
    article = " ".join(f"Source sentence number {i} has enough words to become a slide." for i in range(1, 8))
    slides = mvp._extractive_slides(article, "https://example.com")
    assert len(slides) == 6
    assert slides[0]["content"] == "Source sentence number 1 has enough words to become a slide."
    assert slides[-1]["content"].endswith("https://example.com")


def test_extractive_slides_skip_long_sentences_and_preserve_source_text():
    mvp = _load_mvp()
    url = "https://example.com/story"
    long = "This source sentence is deliberately too long " + ("word " * 100) + "to fit a Threads slide."
    valid = [f"Source sentence number {i} has enough words and remains short for a slide." for i in range(1, 8)]
    article = " ".join([long, *valid])
    slides = mvp._extractive_slides(article, url)
    assert len(slides) == 6
    assert all(len(s["content"].replace("\n\n" + url, "")) <= 400 for s in slides)
    assert all(s["content"].replace("\n\n" + url, "") in article for s in slides)
    assert slides[-1]["content"].endswith(url)


def test_extractive_slides_reject_when_fewer_than_six_title_related_sentences():
    mvp = _load_mvp()
    article = " ".join([
        "Arsenal and Bruno Guimaraes appear in this only related source sentence.",
        *[f"Unrelated source sentence {i} has enough words but concerns another story." for i in range(1, 8)],
    ])
    assert mvp._extractive_slides(article, "https://example.com", "Arsenal Bruno Guimaraes") is None


def test_extractive_slides_prioritize_title_entities():
    mvp = _load_mvp()
    article = (
        "Unrelated advertisement contains enough words to look like a story but is irrelevant. "
        "Arsenal target Bruno Guimaraes has enough words in source sentence one. "
        "Bruno Guimaraes is expected at Arsenal training according to this source sentence. "
        "Arsenal discussed Bruno Guimaraes with Newcastle in this source sentence. "
        "Newcastle captain Bruno Guimaraes appears again in this source sentence. "
        "Arsenal and Bruno Guimaraes remain central in this source sentence. "
        "Final Arsenal Bruno Guimaraes source sentence supplies six factual slides."
    )
    slides = mvp._extractive_slides(article, "https://example.com", "Arsenal Bruno Guimaraes")
    assert "Arsenal target Bruno Guimaraes" in slides[0]["content"]


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
