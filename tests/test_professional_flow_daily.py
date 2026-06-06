from lurker.application.professional_flow_daily import (
    classify_market_temperature,
    _detect_contradiction,
    _setup_score,
    _classify_sector_label,
    _trend_scores,
    run_professional_flow_daily,
)


# ---------------------------------------------------------------------------
# 修复 1：市场温度分类
# ---------------------------------------------------------------------------

def test_classify_market_temperature_detects_attack_mode():
    result = classify_market_temperature(
        market_flow={"main_net_inflow": 10.0, "super_large_net_inflow": 5.0},
        margin={"margin_balance_change": 1.0},
        core_etfs=[{"symbol": "510300.SH", "turnover_expansion": 1.5}],
    )
    assert result == "进攻"


def test_classify_market_temperature_defense_when_all_negative():
    result = classify_market_temperature(
        market_flow={"main_net_inflow": -10.0, "super_large_net_inflow": -5.0},
        margin={},
        core_etfs=[],
    )
    assert result == "防守"


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
        "market_flow": {"main_net_inflow": -100.0, "super_large_net_inflow": -50.0},
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
        "margin": {},
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
    # 防守模式：2%候选为零
    assert report.main_candidates_count == 0
    assert "防守" in report.content_md


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


def test_professional_report_promotes_two_percent_candidate():
    """足够强的趋势分位（5只股票池，300308排最高）可以进入 2%候选。"""
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
    assert "附：职业资金雷达打分规则说明" not in report.content_md
    assert "综合得分 = 温度调整量" not in report.content_md
    assert report.main_candidates_count >= 1


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


def test_negative_score_candidate_is_not_shown_as_spring_setup():
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

    setup_section = report.content_md.split("## 弹簧买点观察", 1)[1].split("## 证伪/退潮提醒", 1)[0]
    assert "迈瑞医疗" not in setup_section


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
