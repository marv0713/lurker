def passes_hk_filters(
    *,
    price_hkd: float,
    avg_turnover_hkd: float,
    min_price_hkd: float = 1.0,
    min_avg_turnover_hkd: float = 20_000_000,
) -> bool:
    return price_hkd >= min_price_hkd and avg_turnover_hkd >= min_avg_turnover_hkd


def passes_us_filters(
    *,
    market_cap_usd: float,
    avg_turnover_usd: float,
    min_market_cap_usd: float = 2_000_000_000,
    min_avg_turnover_usd: float = 10_000_000,
) -> bool:
    return market_cap_usd >= min_market_cap_usd and avg_turnover_usd >= min_avg_turnover_usd
