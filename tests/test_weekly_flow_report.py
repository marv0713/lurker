import json

import pytest

from lurker.application.weekly_flow_report import (
    build_weekly_flow_report,
    build_weekly_flow_summary,
)


def _write_flow(path, date, *, temperature_flow, sectors, stocks, failures=None, margin=None):
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": f"{date}T00:00:00+00:00",
                "market": "cn",
                "market_flow": temperature_flow,
                "sector_flows": sectors,
                "stock_flows": stocks,
                "margin": margin or {},
                "core_etfs": {
                    "configured_symbols": ["510300.SH"],
                    "items": [],
                    "failures": [{"symbol": "510300.SH", "reason": "stub"}],
                    "generated_at": f"{date}T00:00:00+00:00",
                    "schema_version": 1,
                },
                "failures": failures or [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_build_weekly_flow_summary_exposes_structured_context(tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    for index, (day, main, super_large) in enumerate(
        [
            ("2026-07-27", 90_830_307_328.0, 79_009_112_064.0),
            ("2026-07-28", -108_693_266_432.0, -80_549_388_288.0),
            ("2026-07-29", -11_947_974_656.0, -2_129_911_808.0),
            ("2026-07-30", -78_993_215_488.0, -51_637_407_744.0),
            ("2026-07-31", 62_535_737_344.0, 69_993_807_872.0),
        ],
        start=1,
    ):
        _write_flow(
            flow_dir / f"{day}.json",
            day,
            temperature_flow={
                "main_net_inflow": main,
                "super_large_net_inflow": super_large,
            },
            sectors=[
                {
                    "name": "通信设备" if index < 5 else "机器人",
                    "main_net_inflow": 100.0,
                    "rank": 1,
                }
            ],
            stocks=[],
        )

    summary = build_weekly_flow_summary(
        flow_snapshot_dir=flow_dir,
        report_date="2026-07-31",
        is_trading_day=lambda day: True,
    )

    assert summary.availability == "available"
    assert summary.start_date == "2026-07-27"
    assert summary.end_date == "2026-07-31"
    assert summary.snapshot_count == 5
    assert summary.main_net_inflow_sum == pytest.approx(-46_268_411_904.0)
    assert summary.super_large_net_inflow_sum == pytest.approx(14_686_212_096.0)
    assert summary.temperature_counts == {"进攻": 0, "观察": 5, "防守": 0}
    assert summary.latest_etf_status == "unknown"
    assert summary.latest_margin_signal == "unknown"
    assert summary.continued_sectors == ("通信设备",)
    assert summary.new_sectors == ("机器人",)


@pytest.mark.parametrize(
    ("snapshot_count", "expected"),
    [(0, "unavailable"), (1, "partial"), (2, "partial"), (3, "available")],
)
def test_weekly_flow_summary_availability_requires_three_snapshots(
    tmp_path,
    snapshot_count,
    expected,
):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    for day in range(1, snapshot_count + 1):
        date_text = f"2026-07-{day:02d}"
        _write_flow(
            flow_dir / f"{date_text}.json",
            date_text,
            temperature_flow={"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
            sectors=[],
            stocks=[],
        )

    summary = build_weekly_flow_summary(
        flow_snapshot_dir=flow_dir,
        report_date="2026-07-31",
        is_trading_day=lambda day: True,
    )

    assert summary.availability == expected
    assert summary.snapshot_count == snapshot_count


def test_weekly_flow_summary_checks_latest_freshness_against_report_date(tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    path = flow_dir / "2026-07-30.json"
    _write_flow(
        path,
        "2026-07-30",
        temperature_flow={
            "trade_date": "2026-07-30",
            "main_net_inflow": 1.0,
            "super_large_net_inflow": 1.0,
        },
        sectors=[],
        stocks=[],
    )
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["core_etfs"] = {
        "configured_symbols": ["510300.SH"],
        "items": [
            {
                "symbol": "510300.SH",
                "name": "沪深300ETF",
                "trade_date": "2026-07-30",
                "current_turnover": 2.0,
                "avg_turnover_20d": 1.0,
                "turnover_expansion": 2.0,
                "shares": None,
                "shares_date": None,
                "status": "active",
                "source": "fixture",
                "availability": "turnover_only",
                "error": None,
            }
        ],
        "failures": [],
        "generated_at": "2026-07-30T16:00:00+08:00",
        "schema_version": 1,
    }
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    summary = build_weekly_flow_summary(
        flow_snapshot_dir=flow_dir,
        report_date="2026-07-31",
        is_trading_day=lambda day: True,
    )

    assert summary.latest_etf_status == "unknown"


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
    # Day 1: positive flow + unknown ETF/margin → observe
    # Day 2: negative flow + unknown ETF/margin → observe
    assert "进攻 0 天" in report.content_md
    assert "观察 2 天" in report.content_md
    assert "防守 0 天" in report.content_md
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


def test_weekly_report_excludes_cn_non_trading_day_snapshots(tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    for day, sector_name, inflow in [
        ("2026-06-15", "半导体", 10.0),
        ("2026-06-16", "半导体", 20.0),
        ("2026-06-17", "半导体", 30.0),
        ("2026-06-18", "半导体", 40.0),
        ("2026-06-19", "假日污染", 9999.0),
    ]:
        _write_flow(
            flow_dir / f"{day}.json",
            day,
            temperature_flow={"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
            sectors=[{"name": sector_name, "main_net_inflow": inflow, "rank": 1}],
            stocks=[],
        )

    report = build_weekly_flow_report(
        flow_snapshot_dir=flow_dir,
        report_date="2026-06-19",
        lookback_days=5,
        sector_limit=10,
        stock_limit=10,
    )

    assert "2026-06-15 至 2026-06-18" in report.content_md
    assert "假日污染" not in report.content_md
    assert "使用资金快照 4 份" in report.content_md


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


def test_empty_weekly_report_discloses_adjusted_date_in_data_quality(tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()

    report = build_weekly_flow_report(
        flow_snapshot_dir=flow_dir,
        report_date="2026-06-18",
        requested_date="2026-06-21",
        is_trading_day=lambda day: True,
    )

    assert "## 数据质量" in report.content_md
    assert "请求日期 2026-06-21，按最近交易日 2026-06-18 生成" in report.content_md


def test_weekly_sector_labels_keep_algorithm_and_add_time_scope(tmp_path):
    flow_dir = tmp_path / "flow_snapshots"
    flow_dir.mkdir()
    _write_flow(
        flow_dir / "2026-06-04.json",
        "2026-06-04",
        temperature_flow={"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
        sectors=[
            {"name": "延续板块", "main_net_inflow": 20.0, "rank": 1},
            {"name": "退潮板块", "main_net_inflow": 10.0, "rank": 2},
        ],
        stocks=[],
    )
    _write_flow(
        flow_dir / "2026-06-05.json",
        "2026-06-05",
        temperature_flow={"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
        sectors=[
            {"name": "延续板块", "main_net_inflow": 30.0, "rank": 1},
            {"name": "新主线板块", "main_net_inflow": 15.0, "rank": 2},
            {"name": "退潮板块", "main_net_inflow": -5.0, "rank": 3},
        ],
        stocks=[],
    )

    report = build_weekly_flow_report(
        flow_snapshot_dir=flow_dir,
        report_date="2026-06-05",
    )

    assert "- 延续板块：周度持续状态：延续，" in report.content_md
    assert "- 新主线板块：周度持续状态：新主线，" in report.content_md
    assert "- 退潮板块：周度持续状态：退潮，" in report.content_md
    assert "周度持续状态—延续：延续板块" in report.content_md
    assert "周度持续状态—新主线：新主线板块" in report.content_md
    assert "周度持续状态—退潮：退潮板块" in report.content_md
