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
