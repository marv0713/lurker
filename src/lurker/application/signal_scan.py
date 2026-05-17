"""signal_scan.py — 从行情快照批量计算个股强度信号。

从 price snapshot batch 的 snapshots 列表出发，对每只个股：
  1. 计算窗口收益的分位数（在同市场的所有个股中排名）
  2. 调用 domain.signals.score_stock_strength 得到信号分
  3. 过滤低于阈值的个股，返回 StockSignal 列表

调用方：application.run_daily
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lurker.domain.signals import classify_double_bagger, score_stock_strength


@dataclass
class StockSignal:
    symbol: str
    market: str
    stock_score: int
    double_bagger_class: str
    # 各窗口原始收益（e.g. return_20d -> 0.12）
    returns: dict[str, float] = field(default_factory=dict)
    # 各窗口分位数（e.g. return_20d_percentile -> 0.93）
    percentiles: dict[str, float] = field(default_factory=dict)
    # 新闻/公告等额外文本信息
    extra_sources: list[str] | None = None


def _extract_returns(snapshot: dict[str, Any], windows: list[int]) -> dict[str, float]:
    """从一条 snapshot 中提取各窗口原始收益。"""
    result: dict[str, float] = {}
    for w in windows:
        key = f"return_{w}d"
        val = snapshot.get(key)
        if val is not None:
            result[key] = float(val)
    return result


def _compute_percentiles(
    market_snapshots: list[dict[str, Any]], windows: list[int]
) -> dict[str, dict[str, float]]:
    """按市场内分位数排名，返回 {symbol: {return_Xd_percentile: float}}。"""
    if not market_snapshots:
        return {}

    # 收集各窗口的所有值用于排名
    window_values: dict[str, list[float]] = {}
    for w in windows:
        key = f"return_{w}d"
        vals = [float(s[key]) for s in market_snapshots if key in s and s[key] is not None]
        if vals:
            window_values[key] = sorted(vals)

    result: dict[str, dict[str, float]] = {}
    for snapshot in market_snapshots:
        symbol = snapshot["symbol"]
        result[symbol] = {}
        for w in windows:
            key = f"return_{w}d"
            if key not in window_values:
                continue
            val = snapshot.get(key)
            if val is None:
                continue
            val = float(val)
            sorted_vals = window_values[key]
            n = len(sorted_vals)
            # 分位数：低于自身的比例
            rank = sum(1 for v in sorted_vals if v < val)
            result[symbol][f"{key}_percentile"] = rank / n if n > 0 else 0.0
    return result


def scan_signals(
    snapshots: list[dict[str, Any]],
    windows: list[int],
    threshold: int = 60,
) -> list[StockSignal]:
    """从 price snapshot rows 批量计算个股信号。

    Args:
        snapshots: collect_price_snapshot_batch 返回的 snapshots 列表。
        windows: 计算收益的窗口列表（需与快照一致，e.g. [20, 60, 120, 180]）。
        threshold: 低于该信号分的个股被过滤，不进入候选流水线。

    Returns:
        通过阈值过滤的 StockSignal 列表，按信号分降序排列。
    """
    # 按市场分组，用于计算分位数
    by_market: dict[str, list[dict[str, Any]]] = {}
    for s in snapshots:
        market = s.get("market", "")
        by_market.setdefault(market, []).append(s)

    signals: list[StockSignal] = []

    for market, market_snapshots in by_market.items():
        percentile_map = _compute_percentiles(market_snapshots, windows)

        for snapshot in market_snapshots:
            symbol = snapshot["symbol"]
            raw_returns = _extract_returns(snapshot, windows)
            pcts = percentile_map.get(symbol, {})

            # 构造 score_stock_strength 所需 metrics
            metrics: dict[str, float | bool] = {
                **pcts,
                # 180d 收益用于翻倍股判断
                "return_180d": raw_returns.get("return_180d", 0.0),
                # 120d 也传入以兼容 double_bagger 分类逻辑
                "return_120d": raw_returns.get("return_120d", 0.0),
                # 以下字段快照里暂无，留 0 / False 占位，后续可补
                "near_52w_high": False,
                "relative_market_strength": 0.0,
                "relative_sector_strength": 0.0,
                "turnover_expansion": 0.0,
            }

            score = score_stock_strength(metrics)
            if score < threshold:
                continue

            # 翻倍股分类取最长可用窗口
            max_return = raw_returns.get(
                "return_180d",
                raw_returns.get("return_120d", 0.0),
            )
            db_class = classify_double_bagger(max_return)

            signals.append(
                StockSignal(
                    symbol=symbol,
                    market=market,
                    stock_score=score,
                    double_bagger_class=db_class,
                    returns=raw_returns,
                    percentiles=pcts,
                )
            )

    signals.sort(key=lambda s: s.stock_score, reverse=True)
    return signals
