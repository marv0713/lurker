from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lurker.reports.models import DailyReport
from lurker.reports.professional_flow_report import render_professional_flow_report


@dataclass
class ProfessionalCandidate:
    symbol: str
    name: str
    score: float
    label: str
    main_net_inflow: float


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 修复 1 + 辅助：市场温度
# ---------------------------------------------------------------------------

def classify_market_temperature(
    *,
    market_flow: dict[str, Any],
    margin: dict[str, Any],
    core_etfs: list[dict[str, Any]],
) -> str:
    """三档市场温度：进攻 / 观察 / 防守"""
    main_flow = _as_float(market_flow.get("main_net_inflow"))
    super_large_flow = _as_float(market_flow.get("super_large_net_inflow"))
    margin_change_value = margin.get("margin_balance_change")
    margin_supportive = margin_change_value is not None and _as_float(margin_change_value) >= 0
    etf_active = any(_as_float(etf.get("turnover_expansion")) >= 1.2 for etf in core_etfs)

    if main_flow > 0 and super_large_flow > 0 and (margin_supportive or etf_active):
        return "进攻"
    if main_flow < 0 and super_large_flow < 0 and not etf_active:
        return "防守"
    return "观察"


def _market_regime_adjustment(temperature: str) -> float:
    """市场温度对总分的直接调整量（百分制）"""
    if temperature == "进攻":
        return 10.0
    if temperature == "防守":
        return -15.0
    return 0.0


def _two_percent_thresholds(temperature: str) -> tuple[float, float, float]:
    """
    返回 (sector_min, flow_min, trend_min) 三档门槛。
    防守状态下不允许任何标的进入 2%候选（返回无法达到的值）。
    """
    if temperature == "进攻":
        return 70.0, 70.0, 65.0
    if temperature == "观察":
        # 观察期：流入门槛提高，且后面还要检查 5d 持续性
        return 70.0, 80.0, 65.0
    # 防守：禁止升级
    return 9999.0, 9999.0, 9999.0


# ---------------------------------------------------------------------------
# 修复 7：趋势得分 + 回撤惩罚
# ---------------------------------------------------------------------------

def _percentile_rank(value: float, values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(1 for item in values if item < value) / len(values)


def _trend_scores(snapshots: list[dict[str, Any]]) -> dict[str, float]:
    """计算趋势得分（百分制），含回撤惩罚。"""
    cn_rows = [row for row in snapshots if row.get("market") == "cn"]
    return_20d = [_as_float(row.get("return_20d")) for row in cn_rows]
    return_60d = [_as_float(row.get("return_60d")) for row in cn_rows]
    scores: dict[str, float] = {}
    for row in cn_rows:
        r20 = _as_float(row.get("return_20d"))
        r60 = _as_float(row.get("return_60d"))
        p20 = _percentile_rank(r20, return_20d)
        p60 = _percentile_rank(r60, return_60d)
        long_return = max(_as_float(row.get("return_120d")), _as_float(row.get("return_180d")))
        raw_score = min(100.0, p20 * 35 + p60 * 35 + max(long_return, 0) * 30)

        # 修复 7：回撤惩罚 —— 用 (return_60d - return_20d) / return_60d 代理相对回撤幅度
        # 只在 60D 正收益时才计算，避免除零或对空头趋势误判
        if r60 > 0:
            relative_retracement = max((r60 - r20) / r60, 0.0)
            if relative_retracement > 0.70:
                raw_score *= 0.70   # 重惩：回调幅度超过涨幅 70%
            elif relative_retracement > 0.45:
                raw_score *= 0.85   # 轻惩：回调幅度 45–70%


        scores[str(row["symbol"]).upper()] = raw_score
    return scores


# ---------------------------------------------------------------------------
# 修复 4：板块得分 + 分化/退潮标签
# ---------------------------------------------------------------------------

def _classify_sector_label(rank: int, inflow: float) -> str:
    """四档板块标签。"""
    if rank <= 3 and inflow > 0:
        return "主线"
    if rank <= 10 and inflow > 0:
        return "扩散"
    if inflow < 0 and rank <= 10:
        return "分化"
    return "退潮"


def _sector_score(
    symbol: str,
    theme_mapping: dict[str, list[str]],
    sector_flows: list[dict[str, Any]],
) -> tuple[float, str | None, str | None]:
    """返回 (sector_score, best_theme_name, sector_label)"""
    themes = theme_mapping.get(symbol, [])
    if not themes:
        return 0.0, None, None
    by_name = {str(flow.get("name")): flow for flow in sector_flows}
    best_score = 0.0
    best_theme = None
    best_label = None

    # 定义主题 ID 到官方行业板块名称的映射关系
    theme_to_sectors = {
        "ai_infra": ["通信设备", "电子", "元件", "光学光电子", "印制电路板", "半导体", "电子设备", "计算机设备", "软件开发", "IT服务", "通信"],
        "innovative_drugs": ["医药生物", "化学制药", "生物制品", "医疗器械", "中药", "医药商业", "医疗服务"]
    }

    for theme in themes:
        # 如果 theme 直接匹配官方行业名（例如测试用例或其它情况）
        sectors_to_check = [theme]
        # 如果 theme 是已知的主题 ID，则获取其对应的官方行业名列表进行检查
        if theme in theme_to_sectors:
            sectors_to_check.extend(theme_to_sectors[theme])

        for sector in sectors_to_check:
            flow = by_name.get(sector)
            if not flow:
                continue
            rank = int(flow.get("rank", 999))
            inflow = _as_float(flow.get("main_net_inflow"))
            label = _classify_sector_label(rank, inflow)
            if label == "主线":
                score = 70.0
            elif label == "扩散":
                score = 40.0
            elif label == "分化":
                score = 15.0
            else:  # 退潮
                score = 0.0
            if score > best_score or (score == best_score and best_theme is None):
                best_score = score
                best_theme = theme
                best_label = label
    return best_score, best_theme, best_label


def _ebb_sectors(sector_flows: list[dict[str, Any]], theme_mapping: dict[str, list[str]]) -> list[str]:
    """返回所有退潮板块名称，用于证伪提醒。仅关注我们关注的主题相关的板块。"""
    theme_to_sectors = {
        "ai_infra": ["通信设备", "电子", "元件", "光学光电子", "印制电路板", "半导体", "电子设备", "计算机设备", "软件开发", "IT服务", "通信"],
        "innovative_drugs": ["医药生物", "化学制药", "生物制品", "医疗器械", "中药", "医药商业", "医疗服务"]
    }

    # 找出当前 universe 中所有被映射到的 theme ID
    active_themes = set()
    for themes in theme_mapping.values():
        active_themes.update(themes)

    # 收集所有相关的官方板块名称
    related_sectors = set()
    for theme in active_themes:
        if theme in theme_to_sectors:
            related_sectors.update(theme_to_sectors[theme])
        else:
            # 兼容测试中的自定义板块名
            related_sectors.add(theme)

    result = []
    for flow in sector_flows:
        name = str(flow.get("name", ""))
        if name not in related_sectors:
            continue
        rank = int(flow.get("rank", 999))
        inflow = _as_float(flow.get("main_net_inflow"))
        if _classify_sector_label(rank, inflow) == "退潮":
            result.append(name)
    return result


# ---------------------------------------------------------------------------
# 修复 2：个股资金流打分 + 背离检测
# ---------------------------------------------------------------------------

def _stock_flow_score(flow: dict[str, Any]) -> float:
    score = 0.0
    if _as_float(flow.get("main_net_inflow")) > 0:
        score += 25
    if _as_float(flow.get("super_large_net_inflow")) > 0:
        score += 25
    if _as_float(flow.get("main_net_inflow_5d")) > 0:
        score += 20
    if _as_float(flow.get("main_net_inflow_10d")) > 0:
        score += 20
    return min(score, 100.0)


def _detect_contradiction(
    flow: dict[str, Any],
    trend_score: float,
    sector_score: float,
) -> str | None:
    """
    检测三种典型背离：
    - 强势未获资金确认：价格强但主力净流出
    - 资金试探：主力流入但趋势极弱
    - 跟风不足：板块主线但个股净流出
    返回 None 表示无背离。
    """
    main_inflow = _as_float(flow.get("main_net_inflow"))
    if trend_score >= 60 and main_inflow < 0:
        return "强势未获资金确认"
    if main_inflow > 0 and trend_score < 35:
        return "资金试探"
    if sector_score >= 70 and main_inflow < 0:
        return "跟风不足"
    return None


# ---------------------------------------------------------------------------
# 修复 3：Setup 分数用真实价格形态近似
# ---------------------------------------------------------------------------

def _setup_score(row: dict[str, Any]) -> float:
    """
    用日线数据近似判断形态/买点：
    - 60D 强势、20D 有回调（pullback）
    - 近期收益未破零（企稳）
    - 长线趋势延续
    - 止损距离合理
    """
    r20 = _as_float(row.get("return_20d"))
    r60 = _as_float(row.get("return_60d"))
    r120 = _as_float(row.get("return_120d"))

    score = 0.0

    # 60D 强势但 20D 有所回调（pullback：20D 小于 60D 的 60%）
    if r60 > 0 and r20 < r60 * 0.6:
        score += 30.0

    # 近期收益未破零（企稳）
    if r20 >= 0:
        score += 30.0

    # 长线趋势延续（120D 优于 60D）
    if r120 > r60 > 0:
        score += 20.0

    # 止损距离合理（20D 涨幅 0~15%，即还有安全距离但未过热）
    if 0.0 <= r20 <= 0.15:
        score += 20.0

    return min(score, 100.0)


# ---------------------------------------------------------------------------
# 板块内龙头检查（修复 5 的辅助）
# ---------------------------------------------------------------------------

def _is_sector_leader(
    symbol: str,
    theme: str | None,
    stock_flows: list[dict[str, Any]],
    theme_mapping: dict[str, list[str]],
    top_n: int = 3,
) -> bool:
    """检查该股是否在所属板块的流入标的中排前 top_n。"""
    if not theme:
        return False
    # 找出同板块所有股票（其 theme_mapping 包含该板块）
    same_theme_symbols = {
        sym for sym, themes in theme_mapping.items() if theme in themes
    }
    # 按主力净流入排序
    ranked = sorted(
        [f for f in stock_flows if str(f.get("symbol", "")).upper() in same_theme_symbols],
        key=lambda f: _as_float(f.get("main_net_inflow")),
        reverse=True,
    )
    leaders = {str(f.get("symbol", "")).upper() for f in ranked[:top_n]}
    return symbol in leaders


# ---------------------------------------------------------------------------
# 市场温度备注
# ---------------------------------------------------------------------------

def _market_notes(market_flow: dict[str, Any], margin: dict[str, Any], temperature: str) -> list[str]:
    notes = [f"市场温度：{temperature}"]
    if market_flow:
        notes.append(
            "大盘主力净流入 "
            f"{_as_float(market_flow.get('main_net_inflow')):.0f}，超大单 "
            f"{_as_float(market_flow.get('super_large_net_inflow')):.0f}"
        )
    if margin:
        notes.append(f"两融余额变化 {_as_float(margin.get('margin_balance_change')):.0f}")
    if temperature == "防守":
        notes.append("⚠️ 防守模式：所有标的降级至观察，仅极少数超强确认标的保留候选资格。")
    elif temperature == "观察":
        notes.append("⚠️ 观察模式：需更高个股资金确认（5日持续流入）方可进入 2%候选。")
    return notes


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_professional_flow_daily(
    *,
    price_snapshot: dict[str, Any],
    flow_snapshot: dict[str, Any] | None,
    theme_mapping: dict[str, list[str]],
    symbol_names: dict[str, str] | None = None,
    report_date: str,
) -> DailyReport:
    flow_snapshot = flow_snapshot or {}
    market_flow = flow_snapshot.get("market_flow", {})
    margin = flow_snapshot.get("margin", {})
    core_etfs = flow_snapshot.get("core_etfs", [])
    sector_flows = flow_snapshot.get("sector_flows", [])
    stock_flows = flow_snapshot.get("stock_flows", [])

    # 修复 1：市场温度
    temperature = classify_market_temperature(
        market_flow=market_flow,
        margin=margin,
        core_etfs=core_etfs,
    )
    regime_adj = _market_regime_adjustment(temperature)
    sector_min, flow_min, trend_min = _two_percent_thresholds(temperature)

    # 修复 7：趋势得分（含回撤惩罚）
    trend_scores = _trend_scores(price_snapshot.get("snapshots", []))
    # 建一个 symbol -> price_row 的映射（修复 3 需要）
    price_rows: dict[str, dict[str, Any]] = {
        str(row["symbol"]).upper(): row
        for row in price_snapshot.get("snapshots", [])
        if row.get("market") == "cn"
    }

    candidates: list[dict[str, Any]] = []
    invalidation_alerts: list[str] = []

    for flow in stock_flows:
        symbol = str(flow.get("symbol", "")).upper()
        if not symbol or symbol not in trend_scores:
            continue

        # 修复 4：板块得分 + 标签
        s_score, theme, sector_label = _sector_score(symbol, theme_mapping, sector_flows)

        # 修复 2：背离检测
        flow_score = _stock_flow_score(flow)
        trend_score = trend_scores[symbol]
        contradiction = _detect_contradiction(flow, trend_score, s_score)

        # 修复 3：Setup 分数
        price_row = price_rows.get(symbol, {})
        setup = _setup_score(price_row)

        # 总分（修复 1：加入温度调整）
        total = (
            regime_adj
            + s_score * 0.30
            + flow_score * 0.35
            + trend_score * 0.20
            + setup * 0.15
        )

        # 修复 5：2%候选门槛
        has_5d_inflow = _as_float(flow.get("main_net_inflow_5d")) > 0
        is_leader = _is_sector_leader(symbol, theme, stock_flows, theme_mapping)
        can_be_two_pct = (
            s_score >= sector_min
            and flow_score >= flow_min
            and trend_score >= trend_min
            and is_leader
            and contradiction is None  # 有背离的不能进 2%候选
            and (temperature != "观察" or has_5d_inflow)  # 观察期需 5d 持续
        )
        label = "2%候选" if can_be_two_pct else "资金确认"

        # 修复 6：收集证伪提醒
        if contradiction:
            name = (symbol_names or {}).get(symbol) or flow.get("name") or symbol
            invalidation_alerts.append(f"{name}（{symbol}）：{contradiction}")

        candidates.append(
            {
                "symbol": symbol,
                "name": (symbol_names or {}).get(symbol) or flow.get("name") or symbol,
                "score": round(total, 1),
                "label": label,
                "main_net_inflow": _as_float(flow.get("main_net_inflow")),
                "theme": theme,
                "sector_label": sector_label,
                "contradiction": contradiction,
                "setup_score": setup,
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    two_percent = [item for item in candidates if item["label"] == "2%候选"][:10]
    setup_watch = [
        item
        for item in candidates
        if item["label"] != "2%候选"
        and item["score"] >= 0
        and item.get("setup_score", 0) >= 60
        and item.get("contradiction") is None
        and temperature != "防守"
    ][:10]

    # 修复 4 + 6：退潮板块也加入证伪提醒
    for ebb_sector in _ebb_sectors(sector_flows, theme_mapping):
        invalidation_alerts.append(f"{ebb_sector}：资金退潮，板块失去主线地位")

    # 板块领导者列表（含四档标签）
    sector_leaders = []
    for flow in sector_flows[:10]:
        rank = int(flow.get("rank", 999))
        inflow = _as_float(flow.get("main_net_inflow"))
        sl = _classify_sector_label(rank, inflow)
        sector_leaders.append({**flow, "label": sl})

    # 数据质量
    data_quality: list[str] = []
    for failure in flow_snapshot.get("failures", []):
        data_quality.append(f"{failure.get('source')}：{failure.get('reason')}")
    if not flow_snapshot:
        data_quality.append("缺少资金流快照，仅能输出空报告。")
    if not data_quality:
        data_quality.append("关键资金流数据已加载。")

    # 一句话结论（不再只是温度字符串）
    two_pct_count = len(two_percent)
    if two_pct_count > 0:
        top_names = "、".join(item["name"] for item in two_percent[:3])
        conclusion = f"今日状态：{temperature}。共 {two_pct_count} 只标的满足 2%候选条件，领跑者：{top_names}。"
    else:
        conclusion = f"今日状态：{temperature}。暂无标的满足 2%候选条件，建议观望或布局弹簧买点。"

    content = render_professional_flow_report(
        report_date=report_date,
        market_temperature=temperature,
        market_notes=_market_notes(market_flow, margin, temperature),
        sector_leaders=sector_leaders,
        two_percent_candidates=two_percent,
        setup_watch=setup_watch,
        invalidation_alerts=invalidation_alerts,
        data_quality=data_quality,
        conclusion=conclusion,
    )
    return DailyReport(
        report_date=report_date,
        main_candidates_count=len(two_percent),
        content_md=content,
    )
