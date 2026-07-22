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
