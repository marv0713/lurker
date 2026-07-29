from pathlib import Path

import pytest

from lurker.config import (
    MonthlyMacroConfig,
    load_core_etfs,
    load_markets,
    load_monthly_macro_config,
    load_scoring,
    load_themes,
    load_watchlist,
)


ROOT = Path(__file__).resolve().parents[1]


def _monthly_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "macro_monthly.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_monthly_macro_config_is_strict_and_typed(tmp_path):
    path = _monthly_yaml(
        tmp_path,
        """
schema_version: 1
pboc:
  credit_table_urls:
    "2025": "https://www.pbc.gov.cn/2025.htm"
    "2026": "https://www.pbc.gov.cn/2026.htm"
  allowed_hosts: [www.pbc.gov.cn]
  timeout_seconds: 30
  max_response_bytes: 10000000
thresholds:
  household_deposit_yoy_pct: 12
  leverage_ratio_pct: 4
  financing_monthly_growth_pct: 20
freshness:
  macro_max_lag_months: 2
  leverage_max_lag_trading_days: 3
""",
    )

    assert load_monthly_macro_config(path) == MonthlyMacroConfig(
        credit_table_urls={
            2025: "https://www.pbc.gov.cn/2025.htm",
            2026: "https://www.pbc.gov.cn/2026.htm",
        },
        allowed_hosts=("www.pbc.gov.cn",),
        timeout_seconds=30,
        max_response_bytes=10_000_000,
        household_deposit_yoy_pct=12.0,
        leverage_ratio_pct=4.0,
        financing_monthly_growth_pct=20.0,
        macro_max_lag_months=2,
        leverage_max_lag_trading_days=3,
    )


def test_monthly_macro_config_rejects_unknown_fields(tmp_path):
    path = _monthly_yaml(
        tmp_path,
        """
schema_version: 1
unknown: true
pboc:
  credit_table_urls:
    "2026": "https://www.pbc.gov.cn/2026.htm"
  allowed_hosts: [www.pbc.gov.cn]
  timeout_seconds: 30
  max_response_bytes: 10000000
thresholds:
  household_deposit_yoy_pct: 12
  leverage_ratio_pct: 4
  financing_monthly_growth_pct: 20
freshness:
  macro_max_lag_months: 2
  leverage_max_lag_trading_days: 3
""",
    )
    with pytest.raises(ValueError, match="unknown monthly macro top-level field"):
        load_monthly_macro_config(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("url", "http://www.pbc.gov.cn/2026.htm", "https"),
        ("url", "https://evil.example/2026.htm", "allowed_hosts"),
        ("year", '"26"', "four-digit year"),
        ("timeout", "false", "timeout_seconds"),
        ("lag", "-1", "macro_max_lag_months"),
    ],
)
def test_monthly_macro_config_rejects_invalid_values(
    tmp_path,
    field,
    value,
    message,
):
    text = """
schema_version: 1
pboc:
  credit_table_urls:
    YEAR: "URL"
  allowed_hosts: [www.pbc.gov.cn]
  timeout_seconds: TIMEOUT
  max_response_bytes: 10000000
thresholds:
  household_deposit_yoy_pct: 12
  leverage_ratio_pct: 4
  financing_monthly_growth_pct: 20
freshness:
  macro_max_lag_months: LAG
  leverage_max_lag_trading_days: 3
"""
    replacements = {
        "YEAR": '"2026"',
        "URL": "https://www.pbc.gov.cn/2026.htm",
        "TIMEOUT": "30",
        "LAG": "2",
    }
    target = {
        "url": "URL",
        "year": "YEAR",
        "timeout": "TIMEOUT",
        "lag": "LAG",
    }[field]
    replacements[target] = value
    for marker, replacement in replacements.items():
        text = text.replace(marker, replacement)
    with pytest.raises(ValueError, match=message):
        load_monthly_macro_config(_monthly_yaml(tmp_path, text))


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


def test_shipped_scoring_exposes_only_wired_dimensions():
    scoring = load_scoring(ROOT / "configs" / "scoring.yaml")
    assert set(scoring["stock_signal"]["weights"]) == {
        "return_20d",
        "return_60d",
        "return_180d",
        "double_bagger",
    }
    assert set(scoring["sector_signal"]["weights"]) == {
        "sector_strength",
        "strong_stock_count",
        "cross_market_mapping",
    }


def test_load_scoring_rejects_old_weight_key(tmp_path):
    old = tmp_path / "old.yaml"
    old.write_text(
        """
stock_signal:
  weights: {return_120_180d: 15}
sector_signal:
  weights: {sector_strength: 20}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="return_120_180d.*return_180d"):
        load_scoring(old)


def test_load_scoring_rejects_unknown_top_level_fields(tmp_path):
    path = tmp_path / "scoring.yaml"
    path.write_text(
        """
stock_signal:
  weights: {return_20d: 15}
sector_signal:
  weights: {sector_strength: 20}
ai_attribution:
  weights: {reason_clarity: 20}
candidate_weights:
  stock_first: {stock_score: 0.35, sector_score: 0.35, ai_score: 0.30}
  sector_first: {stock_score: 0.25, sector_score: 0.45, ai_score: 0.30}
""",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="unknown scoring top-level field: ai_attribution",
    ):
        load_scoring(path)


def test_load_scoring_rejects_unknown_and_invalid_weights(tmp_path):
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text(
        """
stock_signal:
  weights: {return_20d: 15, mystery: 10}
sector_signal:
  weights: {sector_strength: 20}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown stock_signal.weights field: mystery"):
        load_scoring(unknown)

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        """
stock_signal:
  weights: {return_20d: .nan}
sector_signal:
  weights: {sector_strength: 20}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="finite and non-negative"):
        load_scoring(invalid)


def test_load_core_etfs_uses_design_roles():
    configured = load_core_etfs(ROOT / "configs" / "core_etfs.yaml")

    assert {item["role"] for item in configured} == {
        "csi300",
        "csi500",
        "chinext",
        "csi_a500",
    }


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


def test_load_watchlist_rejects_unknown_override_field(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults: {}
watchlist:
  - symbol: 300308.SZ
    market: cn
    name: 中际旭创
    overrides:
      volume_rato: 4.0
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown watchlist override field: volume_rato"):
        load_watchlist(path)


def test_load_watchlist_rejects_unknown_default_field(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults:
  drawdwon: 0.25
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown watchlist default field: drawdwon"):
        load_watchlist(path)


def test_load_watchlist_rejects_unknown_item_field(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults: {}
watchlist:
  - {symbol: 300308.SZ, markets: cn, name: 中际旭创}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown watchlist item field: markets"):
        load_watchlist(path)


def test_load_watchlist_applies_distinct_market_price_change_defaults(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults:
  price_change: {cn: 0.05, hk: 0.05, us: 0.10}
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
  - {symbol: 0700.HK, market: hk, name: 腾讯控股}
  - {symbol: NVDA, market: us, name: NVIDIA}
""",
        encoding="utf-8",
    )

    config = load_watchlist(path)

    assert [item.rules.price_change for item in config.items] == [0.05, 0.05, 0.10]


def test_load_watchlist_rejects_non_finite_volume_ratio(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults:
  volume_ratio: .nan
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="volume_ratio must be finite and positive"):
        load_watchlist(path)


def test_load_watchlist_rejects_fractional_cooldown(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults:
  cooldown_trading_days: 1.9
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cooldown_trading_days must be a positive integer"):
        load_watchlist(path)


def test_load_watchlist_rejects_unknown_price_change_market(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults:
  price_change:
    cn: 0.05
    crypto: 0.20
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown price_change market: crypto"):
        load_watchlist(path)


def test_load_watchlist_validates_unused_market_price_change_default(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        """
defaults:
  price_change:
    cn: 0.05
    us: 2.0
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"price_change\.us must be within \(0, 1\]"):
        load_watchlist(path)
