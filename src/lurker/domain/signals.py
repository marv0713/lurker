def classify_double_bagger(period_return: float) -> str:
    if period_return >= 2.0:
        return "multi_bagger"
    if period_return >= 1.0:
        return "double"
    if period_return >= 0.8:
        return "near_double"
    return "none"


def score_stock_strength(metrics: dict[str, float | bool], config: dict | None = None) -> int:
    weights = {}
    if config and "stock_signal" in config and "weights" in config["stock_signal"]:
        weights = config["stock_signal"]["weights"]

    w_20d = weights.get("return_20d", 15)
    w_60d = weights.get("return_60d", 15)
    w_180d_mid = weights.get("return_120_180d", 15)
    w_180d_high = weights.get("double_bagger", 15)
    w_near_high = weights.get("near_52w_high", 10)
    w_mkt_str = weights.get("relative_market_strength", 10)
    w_sec_str = weights.get("relative_sector_strength", 10)
    w_turnover = weights.get("turnover_expansion", 10)

    score = 0

    if metrics.get("return_20d_percentile", 0) >= 0.90:
        score += w_20d
    if metrics.get("return_60d_percentile", 0) >= 0.90:
        score += w_60d
    if metrics.get("return_180d", 0) >= 0.30:
        score += w_180d_mid
    if metrics.get("return_180d", 0) >= 0.80:
        score += w_180d_high
    if metrics.get("near_52w_high") is True:
        score += w_near_high
    if metrics.get("relative_market_strength", 0) >= 0.05:
        score += w_mkt_str
    if metrics.get("relative_sector_strength", 0) >= 0.05:
        score += w_sec_str
    if metrics.get("turnover_expansion", 0) >= 1.5:
        score += w_turnover

    return score


def score_sector_breadth(metrics: dict[str, float | int | bool], config: dict | None = None) -> int:
    weights = {}
    if config and "sector_signal" in config and "weights" in config["sector_signal"]:
        weights = config["sector_signal"]["weights"]

    w_sec_strength = weights.get("sector_strength", 20)
    w_strong_stock = weights.get("strong_stock_count", 20)
    w_new_high = weights.get("new_high_ratio", 15)
    w_chain_diff = weights.get("chain_diffusion", 20)
    w_cross_mkt = weights.get("cross_market_mapping", 15)
    w_turnover_persist = weights.get("turnover_persistence", 10)

    score = 0

    if metrics.get("sector_outperformance") is True:
        score += w_sec_strength
    if metrics.get("strong_stock_count", 0) >= 3:
        score += w_strong_stock
    if metrics.get("new_high_ratio", 0) >= 0.15:
        score += w_new_high
    if metrics.get("chain_segments", 0) >= 2:
        score += w_chain_diff
    if metrics.get("cross_market_count", 0) >= 2:
        score += w_cross_mkt
    if metrics.get("turnover_persistent") is True:
        score += w_turnover_persist

    return score

