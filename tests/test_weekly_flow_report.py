import json

from lurker.application.weekly_flow_report import build_weekly_flow_report


def _write_flow(path, date, *, temperature_flow, sectors, stocks, failures=None):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": f"{date}T00:00:00+00:00",
                "market": "cn",
                "market_flow": temperature_flow,
                "sector_flows": sectors,
                "stock_flows": stocks,
                "margin": {},
                "core_etfs": [],
                "failures": failures or [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_build_weekly_flow_report_aggregates_available_snapshots(tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    _write_flow(
        flow_dir / "2026-06-04.json",
        "2026-06-04",
        temperature_flow={"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        sectors=[
            {"name": "通信设备", "main_net_inflow": 100.0, "rank": 1},
            {"name": "医药生物", "main_net_inflow": -20.0, "rank": 20},
        ],
        stocks=[{"symbol": "300308.SZ", "name": "中际旭创", "main_net_inflow": 30.0}],
    )
    _write_flow(
        flow_dir / "2026-06-05.json",
        "2026-06-05",
        temperature_flow={"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
        sectors=[
            {"name": "通信设备", "main_net_inflow": 80.0, "rank": 2},
            {"name": "机器人", "main_net_inflow": 50.0, "rank": 3},
        ],
        stocks=[{"symbol": "300308.SZ", "name": "中际旭创", "main_net_inflow": 40.0}],
    )

    report = build_weekly_flow_report(
        flow_snapshot_dir=flow_dir,
        report_date="2026-06-06",
        lookback_days=5,
        sector_limit=10,
        stock_limit=10,
    )

    assert "职业资金雷达周报" in report.content_md
    assert "2026-06-04 至 2026-06-05" in report.content_md
    assert "通信设备" in report.content_md
    assert "连续 2 天" in report.content_md
    assert "中际旭创" in report.content_md
    assert "防守 1 天" in report.content_md
    assert report.main_candidates_count == 2


def test_weekly_report_uses_latest_n_snapshots_not_calendar_window(tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    for day in ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]:
        _write_flow(
            flow_dir / f"{day}.json",
            day,
            temperature_flow={"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
            sectors=[{"name": day, "main_net_inflow": 10.0, "rank": 1}],
            stocks=[],
        )

    report = build_weekly_flow_report(
        flow_snapshot_dir=flow_dir,
        report_date="2026-06-07",
        lookback_days=5,
        sector_limit=10,
        stock_limit=10,
    )

    assert "2026-06-01 至 2026-06-05" in report.content_md


def test_weekly_report_limits_noise_and_filters_st_names(tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    sectors = [
        {"name": f"板块{i}", "main_net_inflow": 100.0 - i, "rank": i}
        for i in range(1, 15)
    ]
    stocks = [
        {"symbol": "000001.SZ", "name": "*ST噪音", "main_net_inflow": 999.0},
        {"symbol": "000002.SZ", "name": "退市噪音", "main_net_inflow": 998.0},
        *[
            {"symbol": f"300{i:03d}.SZ", "name": f"股票{i}", "main_net_inflow": 100.0 - i}
            for i in range(1, 8)
        ],
    ]
    _write_flow(
        flow_dir / "2026-06-05.json",
        "2026-06-05",
        temperature_flow={"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
        sectors=sectors,
        stocks=stocks,
    )

    report = build_weekly_flow_report(
        flow_snapshot_dir=flow_dir,
        report_date="2026-06-07",
        lookback_days=5,
        sector_limit=3,
        stock_limit=3,
    )

    sector_section = report.content_md.split("## 本周资金主线", 1)[1].split("## 主线变化", 1)[0]
    stock_section = report.content_md.split("## 核心股票资金流向", 1)[1].split("## 数据质量", 1)[0]
    assert sector_section.count("- ") == 3
    assert stock_section.count("- ") == 3
    assert "ST噪音" not in report.content_md
    assert "退市噪音" not in report.content_md


def test_weekly_report_keeps_midweek_leader_even_if_latest_day_turns_negative(tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    _write_flow(
        flow_dir / "2026-06-03.json",
        "2026-06-03",
        temperature_flow={"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
        sectors=[{"name": "机器人", "main_net_inflow": 100.0, "rank": 1}],
        stocks=[],
    )
    _write_flow(
        flow_dir / "2026-06-04.json",
        "2026-06-04",
        temperature_flow={"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
        sectors=[{"name": "机器人", "main_net_inflow": 100.0, "rank": 1}],
        stocks=[],
    )
    _write_flow(
        flow_dir / "2026-06-05.json",
        "2026-06-05",
        temperature_flow={"main_net_inflow": -1.0, "super_large_net_inflow": -1.0},
        sectors=[{"name": "机器人", "main_net_inflow": -20.0, "rank": 20}],
        stocks=[],
    )

    report = build_weekly_flow_report(
        flow_snapshot_dir=flow_dir,
        report_date="2026-06-07",
        lookback_days=5,
        sector_limit=10,
        stock_limit=10,
    )

    assert "机器人" in report.content_md
    assert "退潮" in report.content_md


def test_build_weekly_flow_report_handles_empty_snapshot_dir(tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()

    report = build_weekly_flow_report(
        flow_snapshot_dir=flow_dir,
        report_date="2026-06-06",
        lookback_days=5,
    )

    assert "职业资金雷达周报" in report.content_md
    assert "没有可用资金快照" in report.content_md
    assert report.main_candidates_count == 0
