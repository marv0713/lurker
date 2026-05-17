from lurker.universe.filters import passes_hk_filters, passes_us_filters
from lurker.universe.seed_pool import build_seed_symbols, collect_seed_sources


def test_build_seed_symbols_deduplicates_by_market():
    themes = [
        {
            "id": "ai_infra",
            "markets": {
                "cn": {"seed_symbols": ["300308.SZ", "300502.SZ"]},
                "us": {"seed_symbols": ["NVDA", "AVGO"]},
                "hk": {"seed_symbols": ["0700.HK"]},
            },
        },
        {
            "id": "ai_infra_2",
            "markets": {
                "cn": {"seed_symbols": ["300308.SZ"]},
                "us": {"seed_symbols": ["NVDA", "ANET"]},
                "hk": {"seed_symbols": ["9988.HK"]},
            },
        },
    ]

    result = build_seed_symbols(themes)

    assert result["cn"] == ["300308.SZ", "300502.SZ"]
    assert result["us"] == ["ANET", "AVGO", "NVDA"]
    assert result["hk"] == ["0700.HK", "9988.HK"]


def test_collect_seed_sources_keeps_index_and_etf_boundaries():
    themes = [
        {
            "id": "ai_infra",
            "markets": {
                "cn": {
                    "seed_indexes": ["科创 50"],
                    "seed_etfs": ["人工智能 ETF"],
                    "seed_symbols": ["300308.SZ"],
                },
                "us": {
                    "seed_etfs": ["SMH"],
                    "seed_symbols": ["NVDA"],
                },
            },
        }
    ]

    result = collect_seed_sources(themes)

    assert result["cn"]["symbols"] == ["300308.SZ"]
    assert result["cn"]["indexes"] == ["科创 50"]
    assert result["cn"]["etfs"] == ["人工智能 ETF"]
    assert result["us"]["symbols"] == ["NVDA"]
    assert result["us"]["etfs"] == ["SMH"]


def test_hk_filters_remove_low_quality_names():
    assert passes_hk_filters(price_hkd=2.5, avg_turnover_hkd=30_000_000)
    assert not passes_hk_filters(price_hkd=0.8, avg_turnover_hkd=30_000_000)
    assert not passes_hk_filters(price_hkd=2.5, avg_turnover_hkd=5_000_000)


def test_us_filters_require_size_and_liquidity():
    assert passes_us_filters(market_cap_usd=5_000_000_000, avg_turnover_usd=20_000_000)
    assert not passes_us_filters(market_cap_usd=500_000_000, avg_turnover_usd=20_000_000)
