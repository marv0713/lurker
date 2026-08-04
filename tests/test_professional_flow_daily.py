from datetime import datetime, timedelta, timezone

import pytest

from lurker.application.market_temperature import classify_market_temperature
from lurker.application.professional_flow_daily import (
    _build_spring_scan,
    _detect_contradiction,
    _market_notes,
    _setup_score,
    _classify_sector_label,
    _trend_scores,
    run_professional_flow_daily,
)
from lurker.ingest.etf_flows import CoreEtfBatch
from lurker.reports.professional_flow_report import render_professional_flow_report


# ---------------------------------------------------------------------------
# 修复 1：市场温度分类
# ---------------------------------------------------------------------------

def test_classify_market_temperature_detects_attack_mode():
    result = classify_market_temperature(
        market_flow={"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        etf_status="active",
        margin_signal="supportive",
    )
    assert result == "进攻"


def test_classify_market_temperature_defense_when_all_negative():
    # Old test used empty core_etfs + empty margin → expected defense.
    # New logic: empty → unknown → observe. Defense needs active negative confirmation.
    result = classify_market_temperature(
        market_flow={"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
        etf_status="inactive",
        margin_signal="weakening",
    )
    assert result == "防守"


def test_market_notes_show_margin_balance_and_skip_missing_change():
    notes = _market_notes(
        {"main_net_inflow": 1.0, "super_large_net_inflow": 2.0},
        {"margin_balance": 2_598_316_556_644.0},
        "观察",
    )

    assert "两融余额：2.60万亿元" in notes
    assert not any("较上一交易日" in note for note in notes)


def test_market_notes_show_margin_balance_change_when_available():
    notes = _market_notes(
        {"main_net_inflow": 1.0, "super_large_net_inflow": 2.0},
        {
            "margin_balance": 2_598_316_556_644.0,
            "margin_balance_change": -36_807_009_524.0,
        },
        "观察",
    )

    assert (
        "两融余额：2.60万亿元，较上一交易日减少368.1亿元（-1.40%）"
        in notes
    )


@pytest.mark.parametrize("invalid", [None, "bad", float("nan"), float("inf")])
def test_market_notes_skip_invalid_margin_balance(invalid):
    notes = _market_notes(
        {},
        {"margin_balance": invalid, "margin_balance_change": 1.0},
        "观察",
    )

    assert not any(note.startswith("两融余额：") for note in notes)


def test_market_notes_do_not_calculate_percentage_for_zero_balance():
    notes = _market_notes(
        {},
        {"margin_balance": 0.0, "margin_balance_change": 100_000_000.0},
        "观察",
    )

    margin_note = next(note for note in notes if note.startswith("两融余额："))
    assert margin_note == "两融余额：0.00万亿元，较上一交易日增加1.0亿元"
    assert "%" not in margin_note


def test_market_notes_round_margin_percentage_to_two_decimals():
    notes = _market_notes(
        {},
        {
            "margin_balance": 985_842_000_000.0,
            "margin_balance_change": -14_158_000_000.0,
        },
        "观察",
    )

    margin_note = next(note for note in notes if note.startswith("两融余额："))
    assert margin_note.endswith("（-1.42%）")


@pytest.mark.parametrize(
    ("signal", "label"),
    [
        ("supportive", "杠杆资金增加"),
        ("weakening", "杠杆资金回落"),
        ("overheated", "杠杆资金过热"),
        ("unknown", "暂不判断"),
    ],
)
def test_market_notes_translate_margin_signal(signal, label):
    notes = _market_notes({}, {}, "观察", margin_signal=signal)

    assert f"两融方向：{label}" in notes


def test_market_notes_show_active_etf_and_margin_signal():
    batch = CoreEtfBatch.from_dict(
        {
            "configured_symbols": ["510300.SH"],
            "items": [
                {
                    "symbol": "510300.SH",
                    "name": "沪深300ETF",
                    "trade_date": "2026-07-23",
                    "current_turnover": 135.0,
                    "avg_turnover_20d": 100.0,
                    "turnover_expansion": 1.35,
                    "shares": None,
                    "shares_date": None,
                    "status": "active",
                    "source": "fixture",
                    "availability": "turnover_only",
                    "error": None,
                }
            ],
            "failures": [],
            "generated_at": "2026-07-23T08:00:00+00:00",
            "schema_version": 1,
        }
    )

    notes = _market_notes(
        {"main_net_inflow": 1.0, "super_large_net_inflow": 2.0},
        {"margin_balance": 770.0},
        "进攻",
        etf_batch=batch,
        etf_status="active",
        margin_signal="supportive",
    )

    assert "核心 ETF：放量活跃（沪深300ETF 放量 1.35x）" in notes
    assert "两融方向：杠杆资金增加" in notes


def test_market_notes_explain_stale_successful_etf_collection():
    batch = CoreEtfBatch.from_dict(
        {
            "configured_symbols": ["510300.SH"],
            "items": [
                {
                    "symbol": "510300.SH",
                    "name": "沪深300ETF",
                    "trade_date": "2026-07-30",
                    "current_turnover": 100.0,
                    "avg_turnover_20d": 100.0,
                    "turnover_expansion": 1.0,
                    "shares": None,
                    "shares_date": None,
                    "status": "inactive",
                    "source": "fixture",
                    "availability": "turnover_only",
                    "error": None,
                }
            ],
            "failures": [],
            "generated_at": "2026-07-31T08:00:00+00:00",
            "schema_version": 1,
        }
    )

    notes = _market_notes(
        {},
        {},
        "观察",
        etf_batch=batch,
        etf_status="unknown",
        etf_freshness="stale",
        etf_cutoff="2026-07-30",
        expected_trade_date="2026-07-31",
    )

    assert (
        "核心 ETF：暂不判断（数据截止 2026-07-30，非当日；采集成功）"
        in notes
    )


def test_market_notes_disclose_partial_failure_and_stale_successes():
    batch = CoreEtfBatch.from_dict(
        {
            "configured_symbols": ["510300.SH", "510500.SH"],
            "items": [
                {
                    "symbol": "510300.SH",
                    "name": "沪深300ETF",
                    "trade_date": "2026-07-30",
                    "current_turnover": 100.0,
                    "avg_turnover_20d": 100.0,
                    "turnover_expansion": 1.0,
                    "shares": None,
                    "shares_date": None,
                    "status": "inactive",
                    "source": "fixture",
                    "availability": "turnover_only",
                    "error": None,
                }
            ],
            "failures": [{"symbol": "510500.SH", "reason": "timeout"}],
            "generated_at": "2026-07-31T08:00:00+00:00",
            "schema_version": 1,
        }
    )

    notes = _market_notes(
        {},
        {},
        "观察",
        etf_batch=batch,
        etf_status="unknown",
        etf_freshness="unknown",
        etf_cutoff="2026-07-30",
        expected_trade_date="2026-07-31",
    )

    assert (
        "核心 ETF：暂不判断（部分采集失败；成功数据截止 2026-07-30，且非当日）"
        in notes
    )


def test_market_notes_cover_remaining_etf_display_states():
    failed_batch = CoreEtfBatch(
        configured_symbols=["510300.SH"],
        items=[],
        failures=[{"symbol": "510300.SH", "reason": "timeout"}],
    )

    assert "核心 ETF：放量活跃" in _market_notes(
        {}, {}, "观察", etf_status="active"
    )
    assert "核心 ETF：未见明显放量（均低于 1.20x）" in _market_notes(
        {}, {}, "观察", etf_status="inactive"
    )
    assert "核心 ETF：暂不判断（全部采集失败）" in _market_notes(
        {}, {}, "观察", etf_batch=failed_batch
    )
    assert "核心 ETF：暂不判断（未采集或数据不足）" in _market_notes(
        {}, {}, "观察"
    )


def test_report_data_quality_lists_partial_etf_failure_and_freshness():
    report = run_professional_flow_daily(
        price_snapshot={"snapshots": []},
        flow_snapshot={
            "market_flow": {
                "trade_date": "2026-07-23",
                "main_net_inflow": 1.0,
                "super_large_net_inflow": 2.0,
                "availability": "fresh",
            },
            "sector_flows": [],
            "stock_flows": [],
            "margin": {
                "trade_date": "20260723",
                "margin_balance_change": 1.0,
                "availability": "fresh",
            },
            "core_etfs": {
                "configured_symbols": ["510300.SH", "510500.SH"],
                "items": [
                    {
                        "symbol": "510300.SH",
                        "name": "沪深300ETF",
                        "trade_date": "2026-07-23",
                        "current_turnover": 135.0,
                        "avg_turnover_20d": 100.0,
                        "turnover_expansion": 1.35,
                        "shares": None,
                        "shares_date": None,
                        "status": "active",
                        "source": "fixture",
                        "availability": "turnover_only",
                        "error": None,
                    }
                ],
                "failures": [{"symbol": "510500.SH", "reason": "provider timeout"}],
                "generated_at": "2026-07-23T08:00:00+00:00",
                "schema_version": 1,
            },
            "failures": [],
        },
        theme_mapping={},
        report_date="2026-07-23",
    )

    assert "核心 ETF：放量活跃（沪深300ETF 放量 1.35x）" in report.content_md
    assert "两融方向：杠杆资金增加" in report.content_md
    assert "核心 ETF：截止 2026-07-23，部分数据缺失" in report.content_md
    assert "核心 ETF 510500.SH：provider timeout" in report.content_md
    assert (
        "⚠️ 核心 ETF 部分采集失败；放量判断仅基于成功采集项。"
        in report.content_md
    )
    assert "⚠️ 部分数据非当日或采集不完整" not in report.content_md


def test_daily_report_renders_readable_stale_etf_and_margin_details():
    report = run_professional_flow_daily(
        price_snapshot={"snapshots": []},
        flow_snapshot={
            "market_flow": {
                "trade_date": "2026-07-31",
                "main_net_inflow": 62_535_737_344.0,
                "super_large_net_inflow": 69_993_807_872.0,
                "availability": "fresh",
            },
            "sector_flows": [],
            "stock_flows": [],
            "margin": {
                "trade_date": "20260730",
                "margin_balance": 2_598_316_556_644.0,
                "margin_balance_change": -36_807_009_524.0,
                "availability": "fresh",
            },
            "core_etfs": {
                "configured_symbols": ["510300.SH"],
                "items": [
                    {
                        "symbol": "510300.SH",
                        "name": "沪深300ETF",
                        "trade_date": "2026-07-30",
                        "current_turnover": 100.0,
                        "avg_turnover_20d": 100.0,
                        "turnover_expansion": 1.0,
                        "shares": None,
                        "shares_date": None,
                        "status": "inactive",
                        "source": "fixture",
                        "availability": "turnover_only",
                        "error": None,
                    }
                ],
                "failures": [],
                "generated_at": "2026-07-31T08:00:00+00:00",
                "schema_version": 1,
            },
            "failures": [],
        },
        theme_mapping={},
        report_date="2026-07-31",
        now=datetime(
            2026,
            7,
            31,
            17,
            30,
            tzinfo=timezone(timedelta(hours=8)),
        ),
        is_trading_day=lambda day: day.weekday() < 5,
    )

    assert (
        "两融余额：2.60万亿元，较上一交易日减少368.1亿元（-1.40%）"
        in report.content_md
    )
    assert "两融方向：杠杆资金回落" in report.content_md
    assert (
        "核心 ETF：暂不判断（数据截止 2026-07-30，非当日；采集成功）"
        in report.content_md
    )
    assert "核心 ETF：截止 2026-07-30，非当日数据" in report.content_md
    assert (
        "⚠️ 核心 ETF 数据截止 2026-07-30，非当日；"
        "今日 ETF 信号未参与判断。"
        in report.content_md
    )
    assert "⚠️ 部分数据非当日或采集不完整" not in report.content_md
    assert (
        "大盘资金：主力净流入 +625.4亿元；超大单净流入 +699.9亿元"
        in report.content_md
    )
    assert "62535737344" not in report.content_md
    assert "69993807872" not in report.content_md


def test_market_notes_formats_negative_and_zero_market_flow_in_billions():
    notes = _market_notes(
        {
            "main_net_inflow": -41_870_618_624.0,
            "super_large_net_inflow": 0.0,
        },
        {},
        "观察",
    )

    assert (
        "大盘资金：主力净流入 -418.7亿元；超大单净流入 +0.0亿元"
        in notes
    )


def test_market_temperature_defense_downgrades_candidates():
    """防守模式下，即使流入很强的标的也不应该出现在 2%候选里。"""
    price_snapshot = {
        "snapshots": [
            {
                "symbol": "300308.SZ",
                "market": "cn",
                "return_20d": 0.10,
                "return_60d": 0.50,
                "return_120d": 0.80,
                "return_180d": 1.00,
            }
        ]
    }
    flow_snapshot = {
        "market_flow": {"main_net_inflow": -100.0, "super_large_net_inflow": -50.0, "trade_date": "2026-06-04"},
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
        "margin": {"margin_balance_change": -5.0, "margin_signal": "weakening", "trade_date": "2026-06-04"},
        "core_etfs": {
            "configured_symbols": ["510300.SH"],
            "items": [
                {
                    "symbol": "510300.SH",
                    "name": "沪深300ETF",
                    "trade_date": "2026-06-04",
                    "current_turnover": 800_000_000.0,
                    "avg_turnover_20d": 1_000_000_000.0,
                    "turnover_expansion": 0.80,
                    "shares": None,
                    "shares_date": None,
                    "status": "inactive",
                    "source": "akshare_fund_etf_hist_em",
                    "availability": "turnover_only",
                    "error": None,
                }
            ],
            "failures": [],
            "generated_at": "2026-06-04T00:00:00+00:00",
            "schema_version": 1,
        },
        "failures": [],
    }
    report = run_professional_flow_daily(
        price_snapshot=price_snapshot,
        flow_snapshot=flow_snapshot,
        theme_mapping={"300308.SZ": ["ai_infra"]},
        symbol_names={"300308.SZ": "中际旭创"},
        report_date="2026-06-04",
    )
    # 防守模式：2%候选为零
    assert report.main_candidates_count == 0
    assert "防守" in report.content_md


def test_professional_daily_report_includes_core_stock_flows():
    price_snapshot = {
        "snapshots": [
            {
                "symbol": "300308.SZ",
                "market": "cn",
                "return_20d": 0.10,
                "return_60d": 0.50,
                "return_120d": 0.80,
            },
            {
                "symbol": "600498.SH",
                "market": "cn",
                "return_20d": 0.08,
                "return_60d": 0.30,
                "return_120d": 0.60,
            },
        ]
    }
    flow_snapshot = {
        "market_flow": {"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        "sector_flows": [{"name": "通信设备", "main_net_inflow": 100.0, "rank": 1}],
        "stock_flows": [
            {
                "symbol": "600498.SH",
                "name": "烽火通信",
                "main_net_inflow": 2174000000.0,
                "main_net_inflow_5d": 3000000000.0,
                "main_net_inflow_10d": 5000000000.0,
            },
            {
                "symbol": "300308.SZ",
                "name": "中际旭创",
                "main_net_inflow": 80000000.0,
                "main_net_inflow_5d": 200000000.0,
                "main_net_inflow_10d": 300000000.0,
            },
        ],
        "margin": {},
        "core_etfs": [],
        "failures": [],
    }

    report = run_professional_flow_daily(
        price_snapshot=price_snapshot,
        flow_snapshot=flow_snapshot,
        theme_mapping={"300308.SZ": ["通信设备"], "600498.SH": ["通信设备"]},
        symbol_names={"300308.SZ": "中际旭创", "600498.SH": "烽火通信"},
        report_date="2026-06-12",
    )

    assert "## 核心股票资金流向" in report.content_md
    stock_section = report.content_md.split("## 核心股票资金流向", 1)[1].split("## 弹簧三态扫描", 1)[0]
    assert "烽火通信 (600498.SH)" in stock_section
    assert "今日 21.7亿" in stock_section


# ---------------------------------------------------------------------------
# 修复 2：背离标签检测
# ---------------------------------------------------------------------------

def test_contradiction_strong_price_no_flow():
    result = _detect_contradiction(
        {"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
        trend_score=70.0,
        sector_score=70.0,
    )
    assert result == "强势未获资金确认"


def test_contradiction_inflow_weak_trend():
    result = _detect_contradiction(
        {"main_net_inflow": 10.0},
        trend_score=20.0,
        sector_score=0.0,
    )
    assert result == "资金试探"


def test_contradiction_sector_strong_stock_outflow():
    result = _detect_contradiction(
        {"main_net_inflow": -5.0},
        trend_score=50.0,
        sector_score=70.0,
    )
    assert result == "跟风不足"


def test_no_contradiction_when_clean():
    result = _detect_contradiction(
        {"main_net_inflow": 10.0},
        trend_score=70.0,
        sector_score=70.0,
    )
    assert result is None


def test_contradiction_labels_appear_in_invalidation_section():
    """背离标签要出现在报告的证伪/退潮提醒章节。"""
    price_snapshot = {
        "snapshots": [
            {
                "symbol": "000001.SZ",
                "market": "cn",
                "return_20d": 0.30,
                "return_60d": 0.40,
                "return_120d": 0.50,
                "return_180d": 0.60,
            }
        ]
    }
    flow_snapshot = {
        "market_flow": {"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        "sector_flows": [
            {"name": "银行", "category": "industry", "main_net_inflow": -50.0, "rank": 5}
        ],
        "stock_flows": [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "main_net_inflow": -20.0,  # 净流出 → 强势未获资金确认
                "super_large_net_inflow": -10.0,
                "main_net_inflow_5d": -50.0,
                "main_net_inflow_10d": -80.0,
            }
        ],
        "margin": {"margin_balance_change": 1.0},
        "core_etfs": [],
        "failures": [],
    }
    report = run_professional_flow_daily(
        price_snapshot=price_snapshot,
        flow_snapshot=flow_snapshot,
        theme_mapping={"000001.SZ": ["银行"]},
        report_date="2026-06-04",
    )
    assert "证伪" in report.content_md or "强势未获资金确认" in report.content_md


# ---------------------------------------------------------------------------
# 修复 3：Setup 分数
# ---------------------------------------------------------------------------

def test_setup_score_detects_pullback_and_stabilization():
    row = {
        "return_20d": 0.05,   # 小幅正收益（企稳）
        "return_60d": 0.40,   # 60D 强势
        "return_120d": 0.60,  # 长线更强
    }
    score = _setup_score(row)
    # 应该得到：回调+30，企稳+30，长线+20，止损距离+20 = 100
    assert score >= 80.0


def test_setup_score_low_for_broken_trend():
    row = {
        "return_20d": -0.20,  # 破位
        "return_60d": 0.10,
        "return_120d": 0.05,
    }
    score = _setup_score(row)
    # 企稳条件不满足，止损距离条件也不满足
    assert score <= 30.0


# ---------------------------------------------------------------------------
# 修复 4：板块标签分化/退潮
# ---------------------------------------------------------------------------

def test_sector_label_main_line():
    assert _classify_sector_label(rank=1, inflow=100.0) == "主线"


def test_sector_label_diffusion():
    assert _classify_sector_label(rank=8, inflow=50.0) == "扩散"


def test_sector_label_diverge():
    assert _classify_sector_label(rank=5, inflow=-10.0) == "分化"


def test_sector_label_ebb():
    assert _classify_sector_label(rank=15, inflow=-30.0) == "退潮"


def test_ebb_sector_appears_in_invalidation():
    """退潮板块应该出现在证伪提醒里。"""
    price_snapshot = {"snapshots": []}
    flow_snapshot = {
        "market_flow": {"main_net_inflow": 5.0, "super_large_net_inflow": 2.0},
        "sector_flows": [
            {"name": "煤炭", "category": "industry", "main_net_inflow": -200.0, "rank": 20}
        ],
        "stock_flows": [],
        "margin": {},
        "core_etfs": [],
        "failures": [],
    }
    report = run_professional_flow_daily(
        price_snapshot=price_snapshot,
        flow_snapshot=flow_snapshot,
        theme_mapping={},
        report_date="2026-06-04",
    )
    assert "煤炭" in report.content_md
    assert "退潮" in report.content_md


# ---------------------------------------------------------------------------
# 修复 5：2%候选门槛（trend >= 65 且是板块龙头）
# ---------------------------------------------------------------------------

def test_two_percent_requires_top_tier_trend():
    """trend_score 勉强过半（前50%）不够，必须达到前35%以上。"""
    # 设置两只股票：300308 趋势强，300054 趋势弱
    # 因为只有两只，300308 在 return_20d/return_60d 上都排第2（percentile=0.5），
    # 刚好卡在门槛附近，此时不应进入 2%候选。
    price_snapshot = {
        "snapshots": [
            {
                "symbol": "300308.SZ",
                "market": "cn",
                "return_20d": 0.10,
                "return_60d": 0.20,
                "return_120d": 0.30,
                "return_180d": 0.40,
            },
            {
                "symbol": "000001.SZ",
                "market": "cn",
                "return_20d": 0.05,
                "return_60d": 0.10,
                "return_120d": 0.15,
                "return_180d": 0.20,
            },
        ]
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
    # 只有两只股票，300308 percentile=0.5 → trend_score 不会达到 65
    assert report.main_candidates_count == 0


def test_professional_report_promotes_candidate_only_after_rollout_approval():
    """满足条件的标的只有在温度规则通过 rollout 后才能进入 2%候选。"""
    price_snapshot = {
        "windows": [20, 60, 120, 180],
        "snapshots": [
            {
                "symbol": "300308.SZ",
                "market": "cn",
                "latest_close": 100.0,
                "return_20d": 0.35,   # controlled retracement: (0.60-0.35)/0.60 = 0.42 < 0.45, no penalty
                "return_60d": 0.60,
                "return_120d": 0.80,
                "return_180d": 1.00,
            },
            {"symbol": "000002.SZ", "market": "cn", "return_20d": 0.02, "return_60d": 0.05, "return_120d": 0.07, "return_180d": 0.09},
            {"symbol": "000003.SZ", "market": "cn", "return_20d": 0.01, "return_60d": 0.03, "return_120d": 0.04, "return_180d": 0.05},
            {"symbol": "000004.SZ", "market": "cn", "return_20d": -0.01, "return_60d": 0.01, "return_120d": 0.02, "return_180d": 0.03},
            {"symbol": "000005.SZ", "market": "cn", "return_20d": -0.05, "return_60d": -0.02, "return_120d": 0.00, "return_180d": 0.01},
        ],
        "failures": [],
    }
    flow_snapshot = {
        "market_flow": {"main_net_inflow": 10.0, "super_large_net_inflow": 5.0, "trade_date": "2026-06-04"},
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
        "margin": {"margin_balance_change": 1.0, "margin_signal": "supportive", "trade_date": "2026-06-04"},
        "core_etfs": {
            "configured_symbols": ["510300.SH"],
            "items": [
                {
                    "symbol": "510300.SH",
                    "name": "沪深300ETF",
                    "trade_date": "2026-06-04",
                    "current_turnover": 3_000_000_000.0,
                    "avg_turnover_20d": 2_000_000_000.0,
                    "turnover_expansion": 1.5,
                    "shares": None,
                    "shares_date": None,
                    "status": "active",
                    "source": "akshare_fund_etf_hist_em",
                    "availability": "turnover_only",
                    "error": None,
                }
            ],
            "failures": [],
            "generated_at": "2026-06-04T00:00:00+00:00",
            "schema_version": 1,
        },
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
    assert "附：职业资金雷达打分规则说明" not in report.content_md
    assert "综合得分 = 温度调整量" not in report.content_md
    assert report.main_candidates_count >= 1

    degraded_report = run_professional_flow_daily(
        price_snapshot=price_snapshot,
        flow_snapshot=flow_snapshot,
        theme_mapping={"300308.SZ": ["ai_infra"]},
        symbol_names={"300308.SZ": "中际旭创"},
        report_date="2026-06-04",
        temperature_rollout_approved=False,
    )

    assert degraded_report.main_candidates_count == 0
    assert "市场温度规则尚未完成上线验收" in degraded_report.content_md


# ---------------------------------------------------------------------------
# 修复 6：证伪/退潮提醒自动填充（独立测试）
# ---------------------------------------------------------------------------

def test_invalidation_alerts_auto_populated_from_contradictions():
    """背离标签自动出现在证伪区，invalidation_alerts 不能永远为空。"""
    price_snapshot = {
        "snapshots": [
            {
                "symbol": "600036.SH",
                "market": "cn",
                "return_20d": 0.25,  # 趋势强（>= 60 分位需要多个标的对比，这里单独造场景）
                "return_60d": 0.30,
                "return_120d": 0.40,
                "return_180d": 0.50,
            },
            {"symbol": "600037.SH", "market": "cn", "return_20d": 0.01, "return_60d": 0.02, "return_120d": 0.03, "return_180d": 0.04},
            {"symbol": "600038.SH", "market": "cn", "return_20d": 0.00, "return_60d": 0.01, "return_120d": 0.01, "return_180d": 0.02},
        ]
    }
    flow_snapshot = {
        "market_flow": {"main_net_inflow": 5.0, "super_large_net_inflow": 2.0},
        "sector_flows": [
            {"name": "金融", "category": "industry", "main_net_inflow": 50.0, "rank": 1}
        ],
        "stock_flows": [
            {
                "symbol": "600036.SH",
                "name": "招商银行",
                "main_net_inflow": -30.0,  # 净流出 → 强势未获资金确认
                "super_large_net_inflow": -20.0,
                "main_net_inflow_5d": -50.0,
                "main_net_inflow_10d": -60.0,
            }
        ],
        "margin": {"margin_balance_change": 0.5},
        "core_etfs": [],
        "failures": [],
    }
    report = run_professional_flow_daily(
        price_snapshot=price_snapshot,
        flow_snapshot=flow_snapshot,
        theme_mapping={"600036.SH": ["金融"]},
        symbol_names={"600036.SH": "招商银行"},
        report_date="2026-06-04",
    )
    assert "强势未获资金确认" in report.content_md


def test_legacy_setup_proxy_is_not_shown_as_spring_state():
    price_snapshot = {
        "snapshots": [
            {
                "symbol": "300760.SZ",
                "market": "cn",
                "return_20d": -0.20,
                "return_60d": -0.10,
                "return_120d": -0.05,
                "return_180d": -0.01,
            },
            {
                "symbol": "300308.SZ",
                "market": "cn",
                "return_20d": 0.20,
                "return_60d": 0.30,
                "return_120d": 0.40,
                "return_180d": 0.50,
            },
        ]
    }
    flow_snapshot = {
        "market_flow": {"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
        "sector_flows": [],
        "stock_flows": [
            {
                "symbol": "300760.SZ",
                "name": "迈瑞医疗",
                "main_net_inflow": 10.0,
                "super_large_net_inflow": 0.0,
                "main_net_inflow_5d": 0.0,
                "main_net_inflow_10d": 0.0,
            }
        ],
        "margin": {},
        "core_etfs": [],
        "failures": [],
    }

    report = run_professional_flow_daily(
        price_snapshot=price_snapshot,
        flow_snapshot=flow_snapshot,
        theme_mapping={},
        symbol_names={"300760.SZ": "迈瑞医疗"},
        report_date="2026-06-04",
    )

    spring_section = report.content_md.split("## 弹簧三态扫描", 1)[1].split("## 证伪/退潮提醒", 1)[0]
    assert "迈瑞医疗" not in spring_section
    assert "## 弹簧买点观察" not in report.content_md


# ---------------------------------------------------------------------------
# 修复 7：趋势得分回撤惩罚
# ---------------------------------------------------------------------------

def test_trend_score_applies_heavy_drawdown_penalty():
    """60D 强但回调幅度超过涨幅 70%（相对回撤）应该被重惩（乘 0.7）。"""
    snapshots = [
        # 600036: r60=0.50, r20=0.10 → relative_retracement=(0.50-0.10)/0.50=0.80 > 0.70 → heavy penalty
        {"symbol": "600036.SH", "market": "cn", "return_20d": 0.10, "return_60d": 0.50, "return_120d": 0.60, "return_180d": 0.70},
        # 600037: r60=0.50, r20=0.45 → relative_retracement=0.10/0.50=0.20 → no penalty
        {"symbol": "600037.SH", "market": "cn", "return_20d": 0.45, "return_60d": 0.50, "return_120d": 0.60, "return_180d": 0.70},
    ]
    scores = _trend_scores(snapshots)
    # 600036 被重惩，分数应明显低于 600037
    assert scores["600036.SH"] < scores["600037.SH"]



def test_trend_score_no_penalty_for_controlled_drawdown():
    """回撤 <= 15% 不应该受到惩罚。"""
    snapshots = [
        {"symbol": "600036.SH", "market": "cn", "return_20d": 0.40, "return_60d": 0.50, "return_120d": 0.60, "return_180d": 0.70},
        {"symbol": "600037.SH", "market": "cn", "return_20d": 0.10, "return_60d": 0.11, "return_120d": 0.12, "return_180d": 0.13},
    ]
    scores = _trend_scores(snapshots)
    # 600036 回撤 = 0.10，无惩罚，分数应该高于 600037
    assert scores["600036.SH"] > scores["600037.SH"]


@pytest.mark.parametrize("label", ["主线", "扩散", "分化", "退潮"])
def test_daily_sector_labels_include_time_scope(label):
    report = render_professional_flow_report(
        report_date="2026-07-28",
        market_temperature="观察",
        market_notes=[],
        sector_leaders=[
            {"name": "测试板块", "main_net_inflow": 1.0, "label": label}
        ],
        stock_flow_leaders=[],
        two_percent_candidates=[],
        spring_scan={"confirmed": [], "watch": [], "excluded": []},
        invalidation_alerts=[],
        data_quality=[],
    )
    assert f"当日资金状态：{label}" in report


@pytest.mark.parametrize(
    "flow_patch",
    [
        {},
        {"stock_flows": "invalid"},
        {
            "stock_flows": [],
            "failures": [
                {"source": "stock_flows", "reason": "ReadTimeout"}
            ],
        },
    ],
)
def test_stock_flow_unavailable_warns_candidate_lists_are_incomplete(flow_patch):
    flow = {
        "market_flow": {"main_net_inflow": 1, "super_large_net_inflow": 1},
        "sector_flows": [],
        "margin": {},
        "core_etfs": [],
        "failures": [],
    }
    flow.update(flow_patch)
    report = run_professional_flow_daily(
        price_snapshot={"snapshots": []},
        flow_snapshot=flow,
        theme_mapping={},
        report_date="2026-07-28",
    )
    assert "个股资金流不可用" in report.content_md
    assert "空列表不代表确认没有机会" in report.content_md


def test_successful_empty_stock_flow_is_distinct_from_failure():
    report = run_professional_flow_daily(
        price_snapshot={"snapshots": []},
        flow_snapshot={
            "market_flow": {"main_net_inflow": 1, "super_large_net_inflow": 1},
            "sector_flows": [],
            "stock_flows": [],
            "margin": {},
            "core_etfs": [],
            "failures": [],
        },
        theme_mapping={},
        report_date="2026-07-28",
    )
    assert "本次个股资金流来源返回 0 条记录" in report.content_md
    assert "个股资金流不可用" not in report.content_md


def _spring_payload(
    state,
    *,
    distance=0.008,
    ratio=0.28,
    touches=1,
    reasons=None,
    break_distance=None,
):
    return {
        "rule_version": "ma20-v1",
        "state": state,
        "as_of": "2026-08-04",
        "ma20_distance_pct": distance,
        "volume_compression_ratio": ratio,
        "support_touch_count_60d": touches,
        "min_ma20_distance_2d_pct": break_distance,
        "reasons": list(reasons or []),
    }


def _spring_price_row(symbol, state, **spring_kwargs):
    return {
        "symbol": symbol,
        "market": "cn",
        "return_20d": 0.05,
        "return_60d": 0.10,
        "return_120d": 0.15,
        "return_180d": 0.20,
        "spring": _spring_payload(state, **spring_kwargs),
    }


def test_daily_report_renders_three_spring_states_and_explanation():
    price_snapshot = {
        "snapshots": [
            _spring_price_row("300001.SZ", "first_bullish_confirmed"),
            _spring_price_row("300002.SZ", "compressed_watch", ratio=0.20),
            _spring_price_row(
                "300003.SZ",
                "weak_excluded",
                distance=-0.04,
                ratio=0.46,
                touches=3,
                break_distance=-0.05,
                reasons=[
                    "ma20_broken",
                    "third_support_test",
                    "volume_not_compressed",
                ],
            ),
            _spring_price_row(
                "300004.SZ",
                "unknown",
                reasons=["invalid_volume_data"],
            ),
            _spring_price_row("300005.SZ", "none"),
            {
                "symbol": "NVDA",
                "market": "us",
                "spring": _spring_payload(
                    "unknown", reasons=["insufficient_history"]
                ),
            },
        ]
    }
    report = run_professional_flow_daily(
        price_snapshot=price_snapshot,
        flow_snapshot={
            "market_flow": {"main_net_inflow": 1.0, "super_large_net_inflow": 1.0},
            "sector_flows": [],
            "stock_flows": [],
            "margin": {},
            "core_etfs": [],
            "failures": [],
        },
        theme_mapping={},
        symbol_names={
            "300001.SZ": "首阳股票",
            "300002.SZ": "压紧股票",
            "300003.SZ": "弱弹簧股票",
            "300004.SZ": "数据不足股票",
            "300005.SZ": "无形态股票",
        },
        report_date="2026-08-04",
    )

    assert "## 弹簧三态扫描" in report.content_md
    assert "### 首阳确认" in report.content_md
    assert "### 压紧观察" in report.content_md
    assert "### 弱弹簧排除" in report.content_md
    assert "仅代表形态确认" in report.content_md
    assert "首阳股票 (300001.SZ)：首阳确认" in report.content_md
    assert "压紧股票 (300002.SZ)：压紧观察" in report.content_md
    assert "弱弹簧股票 (300003.SZ)：弱弹簧排除" in report.content_md
    assert "连续2日有效跌破 MA20" in report.content_md
    assert "近60日第3次回踩" in report.content_md
    assert "回踩时缩量不足（缩量比 46%）" in report.content_md
    assert "1 只成交量数据无效" in report.content_md
    assert "有效日线不足" not in report.content_md
    assert "数据不足股票" not in report.content_md
    assert "无形态股票" not in report.content_md
    assert "NVDA" not in report.content_md
    assert "## 弹簧买点观察" not in report.content_md
    assert "建议观望或布局弹簧买点" not in report.content_md


def test_spring_scan_sorts_present_score_before_missing_score_after_metric_ties():
    rows = [
        _spring_price_row("300002.SZ", "compressed_watch"),
        _spring_price_row("300001.SZ", "compressed_watch"),
    ]

    groups, quality = _build_spring_scan(
        rows,
        candidates=[{"symbol": "300002.SZ", "score": 0.0}],
        symbol_names={},
    )

    assert [item["symbol"] for item in groups["watch"]] == [
        "300002.SZ",
        "300001.SZ",
    ]
    assert quality == []


def test_spring_scan_weak_reason_order_is_deterministic():
    rows = [
        _spring_price_row(
            "300003.SZ",
            "weak_excluded",
            reasons=["volume_not_compressed"],
            ratio=0.80,
        ),
        _spring_price_row(
            "300002.SZ",
            "weak_excluded",
            reasons=["third_support_test"],
            touches=3,
        ),
        _spring_price_row(
            "300001.SZ",
            "weak_excluded",
            reasons=["ma20_broken"],
            break_distance=-0.05,
        ),
    ]

    groups, _quality = _build_spring_scan(rows, candidates=[], symbol_names={})

    assert [item["symbol"] for item in groups["excluded"]] == [
        "300001.SZ",
        "300002.SZ",
        "300003.SZ",
    ]


def test_spring_scan_limits_groups_and_keeps_empty_subsections():
    rows = [
        _spring_price_row(f"30{index:04d}.SZ", "compressed_watch")
        for index in range(12)
    ]

    groups, _quality = _build_spring_scan(rows, candidates=[], symbol_names={})

    assert len(groups["confirmed"]) == 0
    assert len(groups["watch"]) == 10
    assert len(groups["excluded"]) == 0


def test_spring_renderer_keeps_empty_groups_and_explains_defense_mode():
    report = render_professional_flow_report(
        report_date="2026-08-04",
        market_temperature="防守",
        market_notes=[],
        sector_leaders=[],
        stock_flow_leaders=[],
        two_percent_candidates=[],
        spring_scan={"confirmed": [], "watch": [], "excluded": []},
        invalidation_alerts=[],
        data_quality=[],
    )

    assert "防守模式：三态结果仅供形态跟踪，不进入候选。" in report
    spring_section = report.split("## 弹簧三态扫描", 1)[1].split(
        "## 证伪/退潮提醒", 1
    )[0]
    assert spring_section.count("- 暂无") == 3
