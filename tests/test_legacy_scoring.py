from pathlib import Path

import pytest

import lurker.application.sector_scan as sector_scan
import lurker.application.signal_scan as signal_scan
from lurker.application.signal_scan import StockSignal
from lurker.config import load_scoring
from lurker.domain.signals import score_sector_breadth, score_stock_strength


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("weight", "metrics", "expected"),
    [
        ("return_20d", {"return_20d_percentile": 0.90}, 1),
        ("return_60d", {"return_60d_percentile": 0.90}, 2),
        ("return_180d", {"return_180d": 0.30}, 4),
        ("double_bagger", {"return_180d": 0.80}, 8),
    ],
)
def test_stock_weight_to_metric_mapping(weight, metrics, expected):
    weights = dict.fromkeys(
        ("return_20d", "return_60d", "return_180d", "double_bagger"),
        0,
    )
    weights[weight] = expected
    config = {"stock_signal": {"weights": weights}}
    assert score_stock_strength(metrics, config=config) == expected


@pytest.mark.parametrize(
    ("weight", "metrics", "expected"),
    [
        ("sector_strength", {"sector_outperformance": True}, 16),
        ("strong_stock_count", {"strong_stock_count": 3}, 32),
        ("cross_market_mapping", {"cross_market_count": 2}, 64),
    ],
)
def test_sector_weight_to_metric_mapping(weight, metrics, expected):
    weights = dict.fromkeys(
        ("sector_strength", "strong_stock_count", "cross_market_mapping"),
        0,
    )
    weights[weight] = expected
    config = {"sector_signal": {"weights": weights}}
    assert score_sector_breadth(metrics, config=config) == expected


def test_shipped_legacy_score_ceilings_stay_60_and_55():
    config = load_scoring(ROOT / "configs" / "scoring.yaml")
    assert score_stock_strength(
        {
            "return_20d_percentile": 1.0,
            "return_60d_percentile": 1.0,
            "return_180d": 2.0,
        },
        config=config,
    ) == 60
    assert score_sector_breadth(
        {
            "sector_outperformance": True,
            "strong_stock_count": 5,
            "cross_market_count": 2,
        },
        config=config,
    ) == 55


def test_signal_scan_passes_only_wired_metrics_and_classifies_from_180d(monkeypatch):
    captured = {}

    def fake_score(metrics, config=None):
        captured.update(metrics)
        return 60

    monkeypatch.setattr(signal_scan, "score_stock_strength", fake_score)
    rows = signal_scan.scan_signals(
        [
            {
                "symbol": "TEST.SZ",
                "market": "cn",
                "return_20d": 0.10,
                "return_60d": 0.20,
                "return_120d": 2.50,
                "return_180d": 0.85,
            }
        ],
        windows=[20, 60, 120, 180],
        threshold=0,
    )

    assert set(captured) == {
        "return_20d_percentile",
        "return_60d_percentile",
        "return_180d",
    }
    assert rows[0].returns["return_120d"] == 2.50
    assert rows[0].double_bagger_class == "near_double"


def test_sector_scan_passes_only_wired_metrics(monkeypatch):
    captured = {}

    def fake_score(metrics, config=None):
        captured.update(metrics)
        return 55

    monkeypatch.setattr(sector_scan, "score_sector_breadth", fake_score)
    signal = StockSignal("TEST.SZ", "cn", 60, "none")
    sector_scan.compute_theme_scores(
        [signal],
        {"TEST.SZ": ["theme"]},
        strong_threshold=45,
    )
    assert set(captured) == {
        "sector_outperformance",
        "strong_stock_count",
        "cross_market_count",
    }
