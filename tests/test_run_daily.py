"""tests/test_run_daily.py — 验证每日 pipeline 链路的核心逻辑。"""

from lurker.ai.attributor import StubAttributor
from lurker.application.run_daily import run_daily
from lurker.application.signal_scan import StockSignal, scan_signals


# ---------------------------------------------------------------------------
# signal_scan 测试
# ---------------------------------------------------------------------------


def _make_snapshots(n: int = 5, base_return: float = 0.5) -> list[dict]:
    """生成一批快照，最后 2 条涨幅明显高于其余。"""
    snapshots = []
    for i in range(n):
        multiplier = 3.0 if i >= n - 2 else 1.0
        snapshots.append(
            {
                "symbol": f"SYM{i:02d}.SZ",
                "market": "cn",
                "latest_close": 100.0,
                "return_20d": base_return * multiplier * (i + 1) / n,
                "return_60d": base_return * multiplier * (i + 1) / n,
                "return_120d": base_return * multiplier * (i + 1) / n,
                "return_180d": base_return * multiplier * (i + 1) / n,
            }
        )
    return snapshots


def test_scan_signals_returns_sorted_list():
    snapshots = _make_snapshots(n=10)
    signals = scan_signals(snapshots, windows=[20, 60, 120, 180], threshold=0)

    # 应该有信号返回
    assert len(signals) > 0
    # 按信号分降序
    scores = [s.stock_score for s in signals]
    assert scores == sorted(scores, reverse=True)


def test_scan_signals_threshold_filters_low_scores():
    """低于阈值的个股不应进入信号列表。"""
    snapshots = _make_snapshots(n=5, base_return=0.01)  # 极低涨幅
    signals = scan_signals(snapshots, windows=[20, 60, 120, 180], threshold=60)
    assert signals == []


def test_scan_signals_percentile_computed_per_market():
    """不同市场的分位数应独立计算。"""
    snapshots = [
        {"symbol": "CN01.SZ", "market": "cn", "latest_close": 100.0, "return_20d": 0.5},
        {"symbol": "CN02.SZ", "market": "cn", "latest_close": 100.0, "return_20d": 0.1},
        {"symbol": "US01", "market": "us", "latest_close": 100.0, "return_20d": 0.5},
        {"symbol": "US02", "market": "us", "latest_close": 100.0, "return_20d": 0.1},
    ]
    signals = scan_signals(snapshots, windows=[20], threshold=0)
    # 两个市场各取第一名（percentile=1.0 or near）
    cn_signals = [s for s in signals if s.market == "cn"]
    us_signals = [s for s in signals if s.market == "us"]
    assert len(cn_signals) == 2
    assert len(us_signals) == 2


# ---------------------------------------------------------------------------
# StubAttributor 测试
# ---------------------------------------------------------------------------


def test_stub_attributor_returns_evidence_insufficient():
    attributor = StubAttributor()
    signal = StockSignal(
        symbol="300308.SZ",
        market="cn",
        stock_score=75,
        double_bagger_class="near_double",
        returns={"return_180d": 0.85},
        percentiles={"return_20d_percentile": 0.95},
    )
    result, ai_score = attributor.attribute(signal)
    assert result.classification == "证据不足型"
    assert result.upgrade_recommendation == "证据不足"
    assert ai_score == StubAttributor.AI_SCORE


# ---------------------------------------------------------------------------
# run_daily 集成测试
# ---------------------------------------------------------------------------


def test_run_daily_with_no_signals_returns_report():
    """快照为空时，日报应包含"无信号"提示。"""
    batch = {
        "generated_at": "2026-05-17T00:00:00+00:00",
        "markets": ["cn"],
        "windows": [20, 60, 120, 180],
        "snapshots": [],
        "failures": [],
    }
    report = run_daily(snapshot_batch=batch, report_date="2026-05-17")
    assert "大趋势雷达日报" in report
    assert "无个股触发" in report


def test_run_daily_with_strong_signals_produces_main_candidates():
    """有强势个股时，日报应包含主候选区。"""
    snapshots = _make_snapshots(n=10, base_return=1.0)
    batch = {
        "generated_at": "2026-05-17T00:00:00+00:00",
        "markets": ["cn"],
        "windows": [20, 60, 120, 180],
        "snapshots": snapshots,
        "failures": [],
    }
    report = run_daily(
        snapshot_batch=batch,
        attributor=StubAttributor(),
        report_date="2026-05-17",
        signal_threshold=0,
    )
    assert "大趋势雷达日报" in report
    assert "主候选" in report


def test_run_daily_failures_appear_in_risk_alerts():
    """快照有 failures 时，风险提醒中应显示失败数量。"""
    batch = {
        "generated_at": "2026-05-17T00:00:00+00:00",
        "markets": ["cn"],
        "windows": [20, 60, 120, 180],
        "snapshots": [],
        "failures": [{"symbol": "FAIL01.SZ", "market": "cn", "reason": "timeout"}],
    }
    report = run_daily(snapshot_batch=batch, report_date="2026-05-17")
    assert "1 只标的行情获取失败" in report


def test_run_daily_shows_low_score_watch_samples_from_archive():
    snapshots = [
        {
            "symbol": "300308.SZ",
            "market": "cn",
            "latest_close": 100.0,
            "return_20d": 0.40,
            "return_60d": 0.80,
            "return_180d": 0.32,
        },
        {
            "symbol": "300054.SZ",
            "market": "cn",
            "latest_close": 100.0,
            "return_20d": 0.35,
            "return_60d": 0.60,
            "return_180d": 0.31,
        },
        {
            "symbol": "300003.SZ",
            "market": "cn",
            "latest_close": 100.0,
            "return_20d": -0.20,
            "return_60d": -0.30,
            "return_180d": -0.10,
        },
    ]
    batch = {
        "generated_at": "2026-05-18T00:00:00+00:00",
        "markets": ["cn"],
        "windows": [20, 60, 180],
        "snapshots": snapshots,
        "failures": [],
    }

    report = run_daily(
        snapshot_batch=batch,
        attributor=StubAttributor(),
        report_date="2026-05-18",
        signal_threshold=15,
        low_score_watch_limit=2,
    )

    assert "## 低分观察样本" in report
    assert "300308.SZ" in report
    assert "低分观察" in report


def test_run_daily_hides_suppressed_symbols_from_watch_samples():
    snapshots = [
        {
            "symbol": "300308.SZ",
            "market": "cn",
            "latest_close": 100.0,
            "return_20d": 0.40,
            "return_60d": 0.80,
            "return_180d": 0.32,
        },
        {
            "symbol": "300054.SZ",
            "market": "cn",
            "latest_close": 100.0,
            "return_20d": 0.35,
            "return_60d": 0.60,
            "return_180d": 0.31,
        },
        {
            "symbol": "300003.SZ",
            "market": "cn",
            "latest_close": 100.0,
            "return_20d": -0.20,
            "return_60d": -0.30,
            "return_180d": -0.10,
        },
    ]
    batch = {
        "generated_at": "2026-05-18T00:00:00+00:00",
        "markets": ["cn"],
        "windows": [20, 60, 180],
        "snapshots": snapshots,
        "failures": [],
    }

    report = run_daily(
        snapshot_batch=batch,
        attributor=StubAttributor(),
        report_date="2026-05-18",
        signal_threshold=15,
        low_score_watch_limit=2,
        suppressed_symbols={"300308.SZ"},
    )

    assert "300308.SZ (CN)" not in report
    assert "300054.SZ" in report
    assert "本地屏蔽列表" in report
