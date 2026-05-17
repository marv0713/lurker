"""sector_scan.py — 计算板块联动分（Sector Breadth）。

将个股信号根据主题归组，计算板块内的强势股数量、跨市场共振等特征，
生成主题的最终板块联动得分。
"""

from __future__ import annotations

from lurker.application.signal_scan import StockSignal
from lurker.domain.signals import score_sector_breadth


def compute_theme_scores(
    signals: list[StockSignal],
    theme_mapping: dict[str, list[str]],
    strong_threshold: int = 45,
) -> dict[str, int]:
    """计算各个主题的板块联动分。

    Args:
        signals: 扫描出的个股信号。
        theme_mapping: resolved_seed_pool 中的 symbol -> [theme_id] 映射。
        strong_threshold: 判定"强势股"的最低 stock_score 分数。

    Returns:
        {theme_id: sector_score}，未涉及的主题得分为 0。
    """
    theme_signals: dict[str, list[StockSignal]] = {}

    for signal in signals:
        themes = theme_mapping.get(signal.symbol, [])
        for th in themes:
            theme_signals.setdefault(th, []).append(signal)

    theme_scores: dict[str, int] = {}

    for theme_id, th_signals in theme_signals.items():
        strong_count = 0
        markets_with_strong = set()

        for s in th_signals:
            if s.stock_score >= strong_threshold:
                strong_count += 1
                markets_with_strong.add(s.market)

        metrics: dict[str, float | int | bool] = {
            "strong_stock_count": strong_count,
            "cross_market_count": len(markets_with_strong),
            # 占位：如果有较多强势股，认为整体跑赢
            "sector_outperformance": strong_count >= 5,
            # 其他指标后续接入更多数据时补充
            "new_high_ratio": 0.0,
            "chain_segments": 0,
            "turnover_persistent": False,
        }

        score = min(100, score_sector_breadth(metrics))
        theme_scores[theme_id] = score

    return theme_scores
