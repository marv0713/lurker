from __future__ import annotations

from pathlib import Path

import pytest

from lurker.config import load_personal_watch


def _write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "personal_watch.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_personal_watch_preserves_group_order(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
defaults:
  hk_experimental_spring:
    min_avg_turnover_hkd_20d: 12000000
    min_positive_volume_ratio_60d: 0.96
holdings:
  - symbol: 300308.sz
    market: CN
    name: 中际旭创
  - symbol: 00700.HK
    market: hk
    name: 腾讯控股
watchlist:
  - symbol: 000001.SZ
    market: cn
    name: 平安银行
""",
    )

    config = load_personal_watch(path)

    assert [item.symbol for item in config.holdings] == ["300308.SZ", "00700.HK"]
    assert [item.name for item in config.holdings] == ["中际旭创", "腾讯控股"]
    assert [item.symbol for item in config.watchlist] == ["000001.SZ"]
    assert config.hk_experimental_spring.min_avg_turnover_hkd_20d == 12_000_000
    assert config.hk_experimental_spring.min_positive_volume_ratio_60d == 0.96


def test_personal_watch_defaults_hk_thresholds(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
holdings: []
watchlist:
  - {symbol: 00700.HK, market: hk, name: 腾讯控股}
""",
    )

    config = load_personal_watch(path)

    assert config.hk_experimental_spring.min_avg_turnover_hkd_20d == 10_000_000
    assert config.hk_experimental_spring.min_positive_volume_ratio_60d == 0.95


def test_personal_watch_requires_name(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
holdings:
  - {symbol: 300308.SZ, market: cn}
watchlist: []
""",
    )

    with pytest.raises(ValueError, match="personal stock name is required"):
        load_personal_watch(path)


def test_personal_watch_rejects_duplicate_across_groups(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
holdings:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
watchlist:
  - {symbol: 300308.sz, market: cn, name: 中际旭创}
""",
    )

    with pytest.raises(ValueError, match="duplicate personal stock symbol: 300308.SZ"):
        load_personal_watch(path)


def test_personal_watch_rejects_us_market(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
holdings: []
watchlist:
  - {symbol: NVDA, market: us, name: NVIDIA}
""",
    )

    with pytest.raises(ValueError, match="unsupported personal stock market: us"):
        load_personal_watch(path)


@pytest.mark.parametrize(
    ("symbol", "market"),
    [
        ("AAPL", "cn"),
        ("00700.HK", "cn"),
        ("300308.SZ", "hk"),
    ],
)
def test_personal_watch_rejects_symbol_market_mismatch(tmp_path, symbol, market):
    path = _write_yaml(
        tmp_path,
        f"holdings: []\nwatchlist:\n  - {{symbol: {symbol}, market: {market}, name: 测试}}\n",
    )

    with pytest.raises(ValueError, match="invalid personal stock symbol for market"):
        load_personal_watch(path)


@pytest.mark.parametrize("symbol", ["7.HK", "700.HK", "9988.HK", "09988.HK"])
def test_personal_watch_accepts_one_to_five_digit_hk_symbols(tmp_path, symbol):
    path = _write_yaml(
        tmp_path,
        f"holdings: []\nwatchlist:\n  - {{symbol: {symbol}, market: hk, name: 港股}}\n",
    )

    config = load_personal_watch(path)

    assert config.watchlist[0].symbol == symbol


def test_personal_watch_rejects_unknown_fields(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
holdings: []
watchlist:
  - {symbol: 00700.HK, market: hk, name: 腾讯控股, cost: 400}
""",
    )

    with pytest.raises(ValueError, match="unknown personal stock field: cost"):
        load_personal_watch(path)


def test_personal_watch_requires_at_least_one_stock(tmp_path):
    path = _write_yaml(tmp_path, "holdings: []\nwatchlist: []\n")

    with pytest.raises(ValueError, match="personal watch must contain at least one stock"):
        load_personal_watch(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("min_avg_turnover_hkd_20d", 0, "must be finite and positive"),
        ("min_positive_volume_ratio_60d", 1.1, "must be within \\(0, 1]"),
    ],
)
def test_personal_watch_validates_hk_thresholds(tmp_path, field, value, message):
    path = _write_yaml(
        tmp_path,
        f"""
defaults:
  hk_experimental_spring:
    {field}: {value}
holdings: []
watchlist:
  - {{symbol: 00700.HK, market: hk, name: 腾讯控股}}
""",
    )

    with pytest.raises(ValueError, match=message):
        load_personal_watch(path)


def test_personal_watch_parses_spring_trigger_defaults_and_per_stock_merge(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
defaults:
  spring_trigger:
    trigger_min_gain_pct: 0.03
holdings: []
watchlist:
  - symbol: 002001.SZ
    market: cn
    name: 新和成
    spring_trigger:
      support_low: 26.0
      support_high: 27.0
""",
    )

    config = load_personal_watch(path)

    trigger = config.watchlist[0].spring_trigger
    assert trigger is not None
    assert trigger.support_low == 26.0
    assert trigger.support_high == 27.0
    assert trigger.shrink_max_turnover == 1_000_000_000.0
    assert trigger.shrink_min_days == 2
    assert trigger.trigger_min_gain_pct == 0.03
    assert trigger.trigger_min_turnover == 1_500_000_000.0
    assert trigger.trigger_min_volume_ratio == 1.5
    assert trigger.support_window_days == 10
    assert trigger.trigger_active_days == 3


def test_personal_watch_spring_trigger_is_none_without_config(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
holdings: []
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创}
""",
    )

    config = load_personal_watch(path)

    assert config.watchlist[0].spring_trigger is None


def test_personal_watch_spring_trigger_requires_support_zone(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
holdings: []
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创, spring_trigger: {support_low: 30.0}}
""",
    )

    with pytest.raises(ValueError, match="support_high is required"):
        load_personal_watch(path)


def test_personal_watch_spring_trigger_rejects_support_high_below_low(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
holdings: []
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创, spring_trigger: {support_low: 30.0, support_high: 29.0}}
""",
    )

    with pytest.raises(ValueError, match="support_high must be greater than support_low"):
        load_personal_watch(path)


def test_personal_watch_spring_trigger_rejects_unknown_fields(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
holdings: []
watchlist:
  - {symbol: 300308.SZ, market: cn, name: 中际旭创, spring_trigger: {support_low: 30.0, support_high: 32.0, entry_price: 31.0}}
""",
    )

    with pytest.raises(ValueError, match="unknown personal stock spring_trigger field: entry_price"):
        load_personal_watch(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("shrink_min_days", 0, "must be an integer >= 1"),
        ("trigger_min_gain_pct", 0, "must be within \\(0, 1]"),
        ("trigger_min_volume_ratio", 1.0, "must be greater than 1"),
        ("support_window_days", 2, "must be an integer >= 3"),
    ],
)
def test_personal_watch_validates_spring_trigger_thresholds(tmp_path, field, value, message):
    path = _write_yaml(
        tmp_path,
        f"""
defaults:
  spring_trigger:
    {field}: {value}
holdings: []
watchlist:
  - {{symbol: 300308.SZ, market: cn, name: 中际旭创, spring_trigger: {{support_low: 30.0, support_high: 32.0}}}}
""",
    )

    with pytest.raises(ValueError, match=message):
        load_personal_watch(path)


def test_personal_watch_rejects_spring_trigger_on_hk(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
holdings: []
watchlist:
  - {symbol: 00700.HK, market: hk, name: 腾讯控股, spring_trigger: {support_low: 400.0, support_high: 420.0}}
""",
    )

    with pytest.raises(ValueError, match="spring_trigger is only supported for cn stocks"):
        load_personal_watch(path)
