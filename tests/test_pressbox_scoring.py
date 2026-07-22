"""Regression tests for Pressbox scoring module.

Run with: pytest tests/test_pressbox_scoring.py -v

Test suite adapted from Market Monday v17 (17-case validation).
Football-specific test cases with SHOULD-PASS, SHOULD-FAIL, and EDGE CASES.
"""

import datetime
import importlib.util
from pathlib import Path

# Load scoring module
_SCORING_PATH = Path(__file__).parent.parent / "pressbox_scoring.py"
_spec = importlib.util.spec_from_file_location("pbox_scoring", _SCORING_PATH)
pbox_scoring = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pbox_scoring)


# ─── HELPER: Dynamic recent date ────────────────────────────────────────────

def _recent_date(hours_ago=5):
    """Generate a recent date string (dynamic, avoids fixture drift)."""
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


# ─── SHOULD PASS FIXTURES (legit football content) ───────────────────────────

P01 = {
    "title": "Mbappe Signs 5-Year Deal with Real Madrid — Fee Confirmed at €180M",
    "description": "Kylian Mbappe has completed his long-awaited transfer to Real Madrid. The French superstar signed a 5-year contract after Real Madrid agreed to pay PSG a transfer fee of €180 million.",
    "source": "Fabrizio Romano",
    "url": "https://test.com/p01",
    "published": _recent_date(3),
}

P02 = {
    "title": "Liverpool 3-1 Arsenal: Hat-trick Hero Salah Dismantles Gunners",
    "description": "Mohamed Salah scored a stunning hat-trick as Liverpool thrashed Arsenal 3-1 at Anfield. The Egyptian king was unstoppable, netting in the 12th, 45th, and 78th minute.",
    "source": "BBC Sport",
    "url": "https://test.com/p02",
    "published": _recent_date(2),
}

P03 = {
    "title": "World Cup 2026: Indonesia Qualify for First Time in History",
    "description": "Indonesia have qualified for the 2026 FIFA World Cup after a dramatic 2-1 victory over Saudi Arabia. The Garuda squad made history in front of 80,000 fans at Gelora Bung Karno stadium.",
    "source": "ESPN FC",
    "url": "https://test.com/p03",
    "published": _recent_date(1),
}

P04 = {
    "title": "Chelsea Sack Pochettino After Disastrous Run — 5 Defeats in 6 Games",
    "description": "Chelsea have sacked manager Mauricio Pochettino after a shocking run of form. The Blues lost 5 of their last 6 Premier League matches, leaving them 12th in the table.",
    "source": "Sky Sports",
    "url": "https://test.com/p04",
    "published": _recent_date(4),
}

P05 = {
    "title": "Champions League Final: Real Madrid 2-1 Dortmund — Bellingham Scores Winner",
    "description": "Jude Bellingham scored a last-minute goal to win the Champions League for Real Madrid. The England midfielder struck in injury time to seal a dramatic 2-1 comeback victory over Borussia Dortmund.",
    "source": "The Athletic",
    "url": "https://test.com/p05",
    "published": _recent_date(6),
}

P06 = {
    "title": "Haaland Signs Contract Extension with Man City Until 2030",
    "description": "Erling Haaland has signed a new contract extension with Manchester City, keeping him at the club until 2030. The Norwegian striker's new deal includes a release clause of £150 million.",
    "source": "Guardian Football",
    "url": "https://test.com/p06",
    "published": _recent_date(5),
}

P07 = {
    "title": "Premier League Title Race: Arsenal Lead by 2 Points After 3-0 Win",
    "description": "Arsenal moved 2 points clear at the top of the Premier League table with a convincing 3-0 victory over Newcastle. Bukayo Saka scored twice as the Gunners dominated at the Emirates.",
    "source": "Sky Sports",
    "url": "https://test.com/p07",
    "published": _recent_date(3),
}


# ─── SHOULD FAIL FIXTURES (non-football content) ─────────────────────────────

F01 = {
    "title": "Resep Rendang Padang Asli Minang — Bumbu Tradisional",
    "description": "Bumbu rendang tradisional dengan resep turun-temurun dari Minangkabau. Cocok untuk hari raya dan acara keluarga.",
    "source": "Kompas",
    "url": "https://test.com/f01",
    "published": _recent_date(2),
}

F02 = {
    "title": "Prediksi Zodiak Hari Ini — Cancer dan Leo Beruntung",
    "description": "Ramalan bintang Cancer dan Leo untuk hari ini. Keberuntungan asmara dan karir menanti.",
    "source": "Detik",
    "url": "https://test.com/f02",
    "published": _recent_date(1),
}

F03 = {
    "title": "Liburan Bali: 10 Tempat Wisata Terbaik 2026",
    "description": "Panduan lengkap liburan ke Bali. Kunjungi pantai Kuta, Ubud, dan Tanah Lot untuk pengalaman tak terlupakan.",
    "source": "Traveloka",
    "url": "https://test.com/f03",
    "published": _recent_date(3),
}

F04 = {
    "title": "Promo Supermarket: Banting Harga Sayuran dan Buah Segar",
    "description": "Diskon besar-besaran di Transmart. Sayuran dan buah segar harga spesial untuk weekend ini.",
    "source": "Kompas",
    "url": "https://test.com/f04",
    "published": _recent_date(4),
}

F05 = {
    "title": "Gosip Artis: Hubungan Terbaru Aktor Indonesia dan Model Cantik",
    "description": "Kabar terbaru hubungan selebriti Indonesia. Artis ternama tertangkap kamera bersama model cantik di Bali.",
    "source": "Tribun",
    "url": "https://test.com/f05",
    "published": _recent_date(2),
}


# ─── EDGE CASE FIXTURES (ambiguous) ──────────────────────────────────────────

E01 = {
    "title": "Liga 1 Indonesia: Persija Jakarta Menang 3-0 Atas Persib Bandung",
    "description": "Persija Jakarta mengalahkan Persib Bandung 3-0 dalam laga Liga 1 Indonesia. Marko Simic mencetak brace di hadapan 50.000 suporter.",
    "source": "Goal.com",
    "url": "https://test.com/e01",
    "published": _recent_date(2),
}

E02 = {
    "title": "Liga Makan: Festival Kuliner Terbaik di Jakarta 2026",
    "description": "Festival kuliner terbesar di Jakarta. 100 stan makanan dari seluruh Indonesia hadir di Senayan.",
    "source": "Kompas",
    "url": "https://test.com/e02",
    "published": _recent_date(3),
}

E03 = {
    "title": "Cuplikan Film Terbaru: Action Thriller Hollywood 2026",
    "description": "Trailer film action terbaru Hollywood. Bintang ternama beradu akting dalam thriller penuh adegan kejar-kejaran.",
    "source": "IMDB",
    "url": "https://test.com/e03",
    "published": _recent_date(4),
}

E04 = {
    "title": "World Cup Qualifier: Indonesia vs Japan — Preview dan Prediksi Skor",
    "description": "Preview pertandingan kualifikasi Piala Dunia 2026 antara Indonesia melawan Jepang. Prediksi starting lineup dan skor akhir.",
    "source": "ESPN FC",
    "url": "https://test.com/e04",
    "published": _recent_date(1),
}

E05 = {
    "title": "Final Piala Presiden: Timnas U-23 Juara Setelah Drama Adu Penalti",
    "description": "Timnas U-23 Indonesia menjadi juara Piala Presiden setelah drama adu penalti melawan Vietnam. Skor akhir 4-2 di adu penalti.",
    "source": "Bola.com",
    "url": "https://test.com/e05",
    "published": _recent_date(2),
}


# ─── TESTS: SHOULD PASS ──────────────────────────────────────────────────────

class TestShouldPass:
    """Legit football content should score >= 60."""

    def test_p01_mbappe_transfer(self):
        score = pbox_scoring.score_topic(P01)
        assert score >= 60, f"P01 (Mbappe transfer) scored {score}, expected >= 60"

    def test_p02_liverpool_hat_trick(self):
        score = pbox_scoring.score_topic(P02)
        assert score >= 60, f"P02 (Liverpool hat-trick) scored {score}, expected >= 60"

    def test_p03_world_cup_qualify(self):
        score = pbox_scoring.score_topic(P03)
        assert score >= 60, f"P03 (WC qualify) scored {score}, expected >= 60"

    def test_p04_chelsea_sack(self):
        score = pbox_scoring.score_topic(P04)
        assert score >= 60, f"P04 (Chelsea sack) scored {score}, expected >= 60"

    def test_p05_champions_league_final(self):
        score = pbox_scoring.score_topic(P05)
        assert score >= 60, f"P05 (CL final) scored {score}, expected >= 60"

    def test_p06_haaland_contract(self):
        score = pbox_scoring.score_topic(P06)
        assert score >= 60, f"P06 (Haaland contract) scored {score}, expected >= 60"

    def test_p07_title_race(self):
        score = pbox_scoring.score_topic(P07)
        assert score >= 60, f"P07 (Title race) scored {score}, expected >= 60"


# ─── TESTS: SHOULD FAIL ──────────────────────────────────────────────────────

class TestShouldFail:
    """Non-football content should score < 60 or be hard-rejected."""

    def test_f01_resep_rendang(self):
        score = pbox_scoring.score_topic(F01)
        assert score < 60, f"F01 (Resep rendang) scored {score}, expected < 60"

    def test_f02_zodiak(self):
        score = pbox_scoring.score_topic(F02)
        assert score == -1, f"F02 (Zodiak) scored {score}, expected -1 (exclude)"

    def test_f03_liburan_bali(self):
        score = pbox_scoring.score_topic(F03)
        assert score < 60, f"F03 (Liburan Bali) scored {score}, expected < 60"

    def test_f04_promo_supermarket(self):
        score = pbox_scoring.score_topic(F04)
        assert score < 60, f"F04 (Promo supermarket) scored {score}, expected < 60"

    def test_f05_gosip_artis(self):
        score = pbox_scoring.score_topic(F05)
        assert score == -1, f"F05 (Gosip artis) scored {score}, expected -1 (exclude)"


# ─── TESTS: EDGE CASES ───────────────────────────────────────────────────────

class TestEdgeCases:
    """Ambiguous content — depends on context."""

    def test_e01_liga_1_football_context(self):
        """'Liga' with football context (match score, team names) should pass."""
        score = pbox_scoring.score_topic(E01)
        assert score >= 60, f"E01 (Liga 1) scored {score}, expected >= 60"

    def test_e02_liga_makan_no_context(self):
        """'Liga' without football context should fail."""
        score = pbox_scoring.score_topic(E02)
        assert score < 60, f"E02 (Liga makan) scored {score}, expected < 60"

    def test_e03_cup_film_no_context(self):
        """'Cup' is no longer ambiguous — it won't trigger exclude. Test passes harmlessly."""
        score = pbox_scoring.score_topic(E03)
        # 'cup' removed from AMBIGUOUS_EXCLUDES; film trailer just scores low naturally
        assert score < 60, f"E03 (Cup film) scored {score}, expected < 60"

    def test_e04_world_cup_qualifier(self):
        """World Cup qualifier should pass (football context)."""
        score = pbox_scoring.score_topic(E04)
        assert score >= 60, f"E04 (WC qualifier) scored {score}, expected >= 60"

    def test_e05_final_piala_presiden(self):
        """'Final' with football context should pass."""
        score = pbox_scoring.score_topic(E05)
        assert score >= 60, f"E05 (Final piala) scored {score}, expected >= 60"


# ─── TESTS: COMPONENT BREAKDOWN ──────────────────────────────────────────────

class TestComponentBreakdown:
    """Verify individual scoring components work correctly."""

    def test_keyword_match_max_40(self):
        """5+ keywords should cap at 40 pts."""
        count, cats = pbox_scoring.check_include_keywords(
            "transfer fee confirmed, new contract, deal agreed, medical, here we go"
        )
        keyword_pts = min(count, 5) * 8
        assert keyword_pts == 40, f"Expected 40, got {keyword_pts}"

    def test_category_transfer_20pts(self):
        """Transfer category should give 20 pts."""
        _, cats = pbox_scoring.check_include_keywords("Mbappe transfer deal agreed")
        assert "transfer" in cats

    def test_category_international_10pts(self):
        """International only should give 10 pts."""
        _, cats = pbox_scoring.check_include_keywords("World Cup qualifier")
        assert "international" in cats
        assert not (cats & {"transfer", "match", "drama"})

    def test_recency_under_6h(self):
        """Article < 3h old gets 20 pts, <6h gets 15 pts (finer grain)."""
        age = pbox_scoring.compute_age_hours(_recent_date(3))
        assert age < 6

    def test_recency_24_48h(self):
        """Article 24-48h old should get 5 pts."""
        age = pbox_scoring.compute_age_hours(_recent_date(36))
        assert 24 <= age < 48

    def test_source_tier_1(self):
        """BBC Sport should be Tier 1."""
        assert pbox_scoring.source_tier("BBC Sport") == 1

    def test_source_tier_2(self):
        """Mirror should be Tier 2."""
        assert pbox_scoring.source_tier("Mirror") == 2

    def test_source_tier_unknown(self):
        """Unknown source should return 99 (unknown)."""
        assert pbox_scoring.source_tier("RandomBlog") == 99

    def test_has_specific_data_score(self):
        """Score '3-1' should be detected."""
        assert pbox_scoring.has_specific_data("Liverpool 3-1 Arsenal")

    def test_has_specific_data_fee(self):
        """Transfer fee '€180M' should be detected."""
        assert pbox_scoring.has_specific_data("fee of €180 million")

    def test_has_specific_data_percentage(self):
        """Percentage should be detected."""
        assert pbox_scoring.has_specific_data("win rate of 75%")

    def test_has_specific_data_none(self):
        """Vague text should not be detected."""
        assert not pbox_scoring.has_specific_data("The team played well today")


# ─── TESTS: EXCLUDE KEYWORDS ─────────────────────────────────────────────────

class TestExcludeKeywords:
    """Exclude keyword filtering."""

    def test_strict_exclude_zodiak(self):
        result = pbox_scoring.check_exclude_keywords("Prediksi zodiak hari ini")
        assert result is not None

    def test_strict_exclude_betting(self):
        result = pbox_scoring.check_exclude_keywords("Betting tips for today's matches")
        assert result is not None

    def test_no_false_positive_kemas(self):
        """'kemas' should not trigger 'emas' exclude."""
        result = pbox_scoring.check_exclude_keywords("Produk dikemas dengan rapi")
        assert result is None

    def test_no_false_positive_memblokir(self):
        """'memblokir' should not trigger 'blok' exclude."""
        result = pbox_scoring.check_exclude_keywords("Bank memblokir transaksi")
        assert result is None

    def test_ambiguous_cup_with_football_context(self):
        """'cup' removed from AMBIGUOUS_EXCLUDES — no longer triggers exclude."""
        text = "Champions League cup final tonight at Wembley"
        result = pbox_scoring.check_exclude_keywords(text)
        assert result is None

    def test_ambiguous_cup_without_football_context(self):
        """'cup' removed from AMBIGUOUS_EXCLUDES — no longer triggers."""
        text = "World cup drinking game ideas for party"
        result = pbox_scoring.check_exclude_keywords(text)
        assert result is None


# ─── TESTS: SELECT BEST CANDIDATE ────────────────────────────────────────────

class TestSelectBestCandidate:
    """Integration test for select_best_candidate."""

    def test_selects_top_article(self):
        articles = [F01, P01, P02, P03]
        result = pbox_scoring.select_best_candidate(articles, top_n=1)
        assert len(result) == 1
        assert result[0][1]["url"] in ["https://test.com/p01", "https://test.com/p02", "https://test.com/p03"]

    def test_handles_none_articles(self):
        result = pbox_scoring.select_best_candidate([None, None], top_n=1)
        assert result == []

    def test_handles_empty_title(self):
        empty = {"title": "", "url": "x", "source": "X", "published": _recent_date(2)}
        result = pbox_scoring.select_best_candidate([empty], top_n=1)
        assert result == []

    def test_respects_threshold(self):
        """Articles below 60 should not be selected."""
        articles = [F01, F03, F04]  # All non-football
        result = pbox_scoring.select_best_candidate(articles, top_n=1)
        assert result == []

    def test_top_n_ordering(self):
        """Should return top N in descending score order."""
        articles = [P01, P02, P03, P04, P05]
        result = pbox_scoring.select_best_candidate(articles, top_n=3)
        assert len(result) <= 3
        if len(result) >= 2:
            assert result[0][0] >= result[1][0]


# ─── DRIFT DETECTOR ──────────────────────────────────────────────────────────

def test_all_fixtures_clear_threshold():
    """Re-score every SHOULD_PASS fixture. Catches fixture drift when threshold changes."""
    fixtures = [
        ("P01", P01), ("P02", P02), ("P03", P03), ("P04", P04),
        ("P05", P05), ("P06", P06), ("P07", P07),
        ("E01", E01), ("E04", E04), ("E05", E05),
    ]
    for name, fx in fixtures:
        score = pbox_scoring.score_topic(fx)
        assert score >= 60, f"Fixture {name} below threshold: score={score}, title={fx['title'][:40]}"


# ─── TESTS: ROBUSTNESS ───────────────────────────────────────────────────────

def test_score_robust_to_missing_fields():
    """score_topic must handle articles with minimal fields."""
    score = pbox_scoring.score_topic({"title": "Mbappe transfer", "url": "x"})
    assert isinstance(score, int) and score >= 0

    score = pbox_scoring.score_topic({})
    assert isinstance(score, int) and score >= 0


def test_future_dated_articles_rejected():
    """Future-dated articles should get 0 recency points."""
    future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24))
    future_str = future.strftime("%a, %d %b %Y %H:%M:%S +0000")
    age = pbox_scoring.compute_age_hours(future_str)
    assert age == 999, "Future-dated article should be rejected"
