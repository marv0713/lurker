import pandas as pd

from lurker.signals.double_baggers import classify_double_bagger
from lurker.signals.stock_strength import calculate_returns, score_stock_strength


def test_calculate_returns_for_windows():
    prices = pd.Series([100, 110, 150, 190, 210], index=pd.date_range("2026-01-01", periods=5))

    result = calculate_returns(prices, windows=[1, 2, 4])

    assert round(result["return_1d"], 4) == 0.1053
    assert round(result["return_2d"], 4) == 0.4
    assert round(result["return_4d"], 4) == 1.1


def test_classify_double_bagger():
    assert classify_double_bagger(0.79) == "none"
    assert classify_double_bagger(0.85) == "near_double"
    assert classify_double_bagger(1.2) == "double"
    assert classify_double_bagger(2.1) == "multi_bagger"


def test_score_stock_strength_rewards_all_wired_signals():
    metrics = {
        "return_20d_percentile": 0.95,
        "return_60d_percentile": 0.93,
        "return_180d": 1.05,
    }

    score = score_stock_strength(metrics)

    assert score == 60
