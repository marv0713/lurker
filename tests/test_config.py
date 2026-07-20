from pathlib import Path

import pytest

from lurker.config import load_markets, load_scoring, load_themes, load_watchlist


ROOT = Path(__file__).resolve().parents[1]


def test_load_themes_contains_ai_infra():
    themes = load_themes(ROOT / "configs" / "themes.yaml")

    assert "ai_infra" in {theme["id"] for theme in themes}
    ai_infra = next(theme for theme in themes if theme["id"] == "ai_infra")
    assert ai_infra["markets"]["cn"]["seed_symbols"] == ["300308.SZ", "300502.SZ"]


def test_load_markets_has_three_market_profiles():
    markets = load_markets(ROOT / "configs" / "markets.yaml")

    assert set(markets) == {"cn", "us", "hk"}
    assert markets["cn"]["role"] == "primary_discovery"
    assert markets["hk"]["filters"]["min_avg_turnover_hkd"] == 20_000_000


def test_load_scoring_weights_sum_to_one():
    scoring = load_scoring(ROOT / "configs" / "scoring.yaml")

    weights = scoring["candidate_weights"]["stock_first"]
    assert sum(weights.values()) == 1.0


def test_load_watchlist_merges_global_defaults_and_item_overrides(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults:
  enabled_alerts: [abnormal_volume, peak_drawdown, chronic_underperformance]
  volume_ratio: 3.0
  price_change: {cn: 0.05, hk: 0.05, us: 0.10}
  drawdown: 0.20
  underperformance_60d: 0.15
  cooldown_trading_days: 20
  worsening_step: 0.10
watchlist:
  - symbol: nvda
    market: us
    name: NVIDIA
    overrides:
      volume_ratio: 4.0
      enabled_alerts: [abnormal_volume]
""",
        encoding="utf-8",
    )

    config = load_watchlist(path)

    item = config.items[0]
    assert item.symbol == "NVDA"
    assert item.rules.volume_ratio == 4.0
    assert item.rules.price_change == 0.10
    assert item.rules.enabled_alerts == ("abnormal_volume",)
    assert item.rules.cooldown_trading_days == 20


def test_load_watchlist_rejects_duplicate_symbols(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults: {}
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
  - {symbol: 300308.sz, market: cn, name: 重复项}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate watchlist symbol"):
        load_watchlist(path)


def test_load_watchlist_rejects_unknown_alert_type(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults:
  enabled_alerts: [moving_average_cross]
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown alert type"):
        load_watchlist(path)
