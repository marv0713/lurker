from lurker.application.professional_flow_daily import (
    classify_market_temperature,
    run_professional_flow_daily,
)


def test_classify_market_temperature_detects_attack_mode():
    result = classify_market_temperature(
        market_flow={"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        margin={"margin_balance_change": 1.0},
        core_etfs=[{"symbol": "510300.SH", "turnover_expansion": 1.5}],
    )

    assert result == "进攻"


def test_professional_report_promotes_two_percent_candidate():
    price_snapshot = {
        "windows": [20, 60, 120, 180],
        "snapshots": [
            {
                "symbol": "300308.SZ",
                "market": "cn",
                "latest_close": 100.0,
                "return_20d": 0.30,
                "return_60d": 0.60,
                "return_120d": 0.80,
                "return_180d": 1.00,
            },
            {
                "symbol": "300054.SZ",
                "market": "cn",
                "latest_close": 100.0,
                "return_20d": 0.05,
                "return_60d": 0.10,
                "return_120d": 0.15,
                "return_180d": 0.20,
            },
        ],
        "failures": [],
    }
    flow_snapshot = {
        "market_flow": {"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        "sector_flows": [
            {"name": "ai_infra", "category": "theme", "main_net_inflow": 100.0, "rank": 1}
        ],
        "stock_flows": [
            {
                "symbol": "300308.SZ",
                "name": "中际旭创",
                "main_net_inflow": 80.0,
                "super_large_net_inflow": 40.0,
                "main_net_inflow_5d": 200.0,
                "main_net_inflow_10d": 300.0,
            }
        ],
        "margin": {"margin_balance_change": 1.0},
        "core_etfs": [],
        "failures": [],
    }

    report = run_professional_flow_daily(
        price_snapshot=price_snapshot,
        flow_snapshot=flow_snapshot,
        theme_mapping={"300308.SZ": ["ai_infra"]},
        symbol_names={"300308.SZ": "中际旭创"},
        report_date="2026-06-04",
    )

    assert "职业资金雷达日报" in report.content_md
    assert "进攻" in report.content_md
    assert "2%候选" in report.content_md
    assert "中际旭创" in report.content_md
