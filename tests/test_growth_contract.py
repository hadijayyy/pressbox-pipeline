import importlib.util
from pathlib import Path


PIPELINE = Path(__file__).parent.parent / "pressbox-mvp.py"


def _load_mvp():
    spec = importlib.util.spec_from_file_location("pressbox_growth", PIPELINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_megastar_boost_requires_title_led_central_story():
    mvp = _load_mvp()
    assert mvp._megastar_centrality("Messi makes decision", "Messi makes decision. Messi explained the decision.") == 1
    assert mvp._megastar_centrality("Transfer roundup mentions Messi", "The roundup mentions Messi once.") == 0
    assert mvp._content_pillar({"title": "Messi goals record"}) == "Stat-Bomb"


def test_pillar_pressure_penalizes_overrepresented_pillar():
    mvp = _load_mvp()
    recent = [{"pillar": "Stat-Bomb"}] * 8 + [{"pillar": "Nostalgia"}] * 2
    assert mvp._pillar_pressure("Stat-Bomb", recent) < 0
    assert mvp._pillar_pressure("Hot Take", recent) > 0


def test_prompt_has_grounded_binary_cta_and_serial_format_contract():
    text = PIPELINE.read_text()
    assert "Binary CTA" in text
    assert "Right call or coward move?" in text
    assert "serial_format" in text
    assert "Do not force a binary question" in text


def test_track_metadata_has_growth_fields():
    mvp = _load_mvp()
    slides = [{"content": "A complete source-backed sentence."} for _ in range(6)]
    slides[0]["_score"] = 80
    mvp.POSTED = "/tmp/test-growth-posted.json"
    mvp.track_post("Messi decision", "https://example.test", "bbc", "id", "url", slides=slides, pillar="Hot Take", serial_format="Football Power Files")
    data = __import__("json").loads(Path(mvp.POSTED).read_text())
    assert data["topics"][-1]["pillar"] == "Hot Take"
    assert data["topics"][-1]["serial_format"] == "Football Power Files"
    Path(mvp.POSTED).unlink(missing_ok=True)


if __name__ == "__main__":
    test_megastar_boost_requires_title_led_central_story()
    test_pillar_pressure_penalizes_overrepresented_pillar()
    print("growth contract tests: PASS")
