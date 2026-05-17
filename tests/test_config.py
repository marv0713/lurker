from pathlib import Path

from lurker.config import load_markets, load_scoring, load_themes


ROOT = Path(__file__).resolve().parents[1]


def test_load_themes_contains_ai_infra():
    themes = load_themes(ROOT / "configs" / "themes.yaml")

    assert "ai_infra" in {theme["id"] for theme in themes}
    ai_infra = next(theme for theme in themes if theme["id"] == "ai_infra")
    assert ai_infra["markets"]["us"]["seed_symbols"] == ["NVDA", "AVGO", "ANET"]


def test_load_markets_has_three_market_profiles():
    markets = load_markets(ROOT / "configs" / "markets.yaml")

    assert set(markets) == {"cn", "us", "hk"}
    assert markets["cn"]["role"] == "primary_discovery"
    assert markets["hk"]["filters"]["min_avg_turnover_hkd"] == 20_000_000


def test_load_scoring_weights_sum_to_one():
    scoring = load_scoring(ROOT / "configs" / "scoring.yaml")

    weights = scoring["candidate_weights"]["stock_first"]
    assert sum(weights.values()) == 1.0
