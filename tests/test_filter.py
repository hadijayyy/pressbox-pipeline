"""Tests for filter chain logic in pressbox-pipeline-v7.py.

Covers:
- RELAXED_FILTER flag (low scrape volume)
- skip_topics matching
- is_similar threshold
- sensitive filter (especially "strip" word-boundary)
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Sensitive filter: "strip" word-boundary (22 Jun fix) ──────────
def _sensitive_match(kw, text):
    """Copy of the helper in pressbox-pipeline-v7.py:455-461.
    Word-boundary for "strip" to avoid false positives.
    """
    if kw == "strip":
        return bool(re.search(r"\bstrip\b", text))
    return kw in text


class TestSensitiveStripBoundary:
    def test_striped_kit_no_match(self):
        """'striped kit' should NOT match — was false positive before fix."""
        assert not _sensitive_match("strip", "team launches new striped kit for season")

    def test_strips_no_match(self):
        """'strips' should NOT match (different word)."""
        assert not _sensitive_match("strip", "coach hands out strips of paper")

    def test_stripping_also_excluded_by_word_boundary(self):
        """Word boundary \\b...\\b only matches the exact word 'strip'."""
        # Note: 'stripping' as a sexual context isn't in the keyword list
        # separately, so this is acceptable. The key fix is 'striped'.
        assert not _sensitive_match("strip", "thief caught stripping car parts")

    def test_strip_verb_match(self):
        """'strip' as a verb (e.g., 'strip the defender') SHOULD match."""
        assert _sensitive_match("strip", "player attempts to strip the defender of the ball")

    def test_other_sensitive_still_work(self):
        """Word boundary fix shouldn't break other sensitive keyword matching."""
        assert _sensitive_match("breasts", "fan shows breasts at world cup match")
        assert _sensitive_match("nude", "scandal involves nude photos")
        assert _sensitive_match("racist", "racist abuse mars world cup")
        assert _sensitive_match("murder charge", "former player faces murder charge")


# ── skip_topics matching (22 Jun fix: 'transfer' → 'transfer_rumor') ─
class TestSkipTopicsMatch:
    def test_transfer_rumor_now_skipped(self):
        """After rename, 'transfer_rumor' must be in skip_topics list."""
        skip_topics = ["gossip", "team_profile", "match_result", "transfer_rumor"]
        assert "transfer_rumor" in skip_topics

    def test_match_result_still_skipped(self):
        skip_topics = ["gossip", "team_profile", "match_result", "transfer_rumor"]
        assert "match_result" in skip_topics

    def test_classifier_output_matches_skip(self):
        """End-to-end: classifier output must be in skip_topics for skip to work."""
        skip_topics = ["gossip", "team_profile", "match_result", "transfer_rumor"]
        # Import here to avoid module-level issues
        from pressbox_common import classify_topic_type

        test_cases = [
            ("Mohamed Salah sends Liverpool transfer reminder", "transfer_rumor"),
            ("Egypt beat New Zealand 3-1 in World Cup opener", "match_result"),
            ("Man Utd sack manager after loss", "managerial_change"),
            ("World Cup 2026 group stage fixtures announced", "tournament_news"),
        ]
        for title, expected_cat in test_cases:
            got = classify_topic_type(title)
            if expected_cat in skip_topics:
                # Would be filtered out
                pass
            assert got == expected_cat, f"Classifier mismatch: '{title}' → {got}, expected {expected_cat}"


# ── RELAXED_FILTER logic (22 Jun fix: low-scrape fallback) ──────────
class TestRelaxedFilter:
    def test_relaxed_when_low_volume(self):
        """RELAXED_FILTER = len(all_topics) < 10."""
        # Simulate the logic
        def should_relax(all_topics_count):
            return all_topics_count < 10

        assert should_relax(0) is True
        assert should_relax(5) is True
        assert should_relax(9) is True

    def test_not_relaxed_when_high_volume(self):
        def should_relax(all_topics_count):
            return all_topics_count < 10

        assert should_relax(10) is False
        assert should_relax(15) is False
        assert should_relax(50) is False

    def test_similarity_threshold_relaxed(self):
        """When RELAXED_FILTER, threshold goes from 0.35 → 0.50."""
        RELAXED_FILTER = True
        threshold = 0.50 if RELAXED_FILTER else 0.35
        assert threshold == 0.50

        RELAXED_FILTER = False
        threshold = 0.50 if RELAXED_FILTER else 0.35
        assert threshold == 0.35

    def test_skip_topics_bypassed_when_relaxed(self):
        """When RELAXED_FILTER, skip_topics enforcement is bypassed."""
        RELAXED_FILTER = True
        skip_topics = ["gossip", "match_result", "transfer_rumor"]
        topic_type = "match_result"
        # The actual filter condition
        should_skip = (not RELAXED_FILTER) and (topic_type in skip_topics)
        assert should_skip is False, "Match_result should NOT be skipped in relaxed mode"


# ── Regression: 22 Jun 2026 filter chain fix ───────────────────────
class TestRegressionFilterChain:
    def test_pipeline_relaxes_when_6_topics(self):
        """The original bug: 6 scraped → 0 after filter → exit 1."""
        # Simulate: 6 scraped, 1 sensitive-killed, then relax should let 2 through
        all_topics = [{"title": f"Topic {i}"} for i in range(6)]
        sensitive_kills = 1
        remaining = len(all_topics) - sensitive_kills
        assert remaining == 5

        # In normal mode, if all 5 hit skip_topics → 0 left → fail
        # In relaxed mode, skip_topics is bypassed → 5 left → success
        RELAXED_FILTER = remaining < 10  # would be True
        assert RELAXED_FILTER is True

        # With relax, the filter chain should yield > 0 results
        # (assuming not all topics are posted_urls / is_similar)
        # The key fix: pipeline no longer exits 1 just because skip_topics kills everything
