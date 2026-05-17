def classify_double_bagger(period_return: float) -> str:
    if period_return >= 2.0:
        return "multi_bagger"
    if period_return >= 1.0:
        return "double"
    if period_return >= 0.8:
        return "near_double"
    return "none"


def score_stock_strength(metrics: dict[str, float | bool]) -> int:
    score = 0

    if metrics.get("return_20d_percentile", 0) >= 0.90:
        score += 15
    if metrics.get("return_60d_percentile", 0) >= 0.90:
        score += 15
    if metrics.get("return_180d", 0) >= 0.30:
        score += 15
    if metrics.get("return_180d", 0) >= 0.80:
        score += 15
    if metrics.get("near_52w_high") is True:
        score += 10
    if metrics.get("relative_market_strength", 0) >= 0.05:
        score += 10
    if metrics.get("relative_sector_strength", 0) >= 0.05:
        score += 10
    if metrics.get("turnover_expansion", 0) >= 1.5:
        score += 10

    return score


def score_sector_breadth(metrics: dict[str, float | int | bool]) -> int:
    score = 0

    if metrics.get("sector_outperformance") is True:
        score += 20
    if metrics.get("strong_stock_count", 0) >= 3:
        score += 20
    if metrics.get("new_high_ratio", 0) >= 0.15:
        score += 15
    if metrics.get("chain_segments", 0) >= 2:
        score += 20
    if metrics.get("cross_market_count", 0) >= 2:
        score += 15
    if metrics.get("turnover_persistent") is True:
        score += 10

    return score
