from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from lurker.application.weekly_flow_report import WeeklyFlowSummary


LatestDirection = Literal["supportive", "weakening", "mixed", "unknown"]

_STATUS_LABELS = {
    "relocation_signal": "存款搬家中",
    "deposit_dominant": "存款仍占主导",
    "rising": "增加",
    "flat": "持平",
    "falling": "减少",
    "improving": "改善",
    "worsening": "恶化",
    "healthy": "正常",
    "overheated": "过热",
    "unknown": "暂不判断",
}


@dataclass(frozen=True)
class MonthlyMarketAnalysis:
    stance: str
    market_stage: str
    latest_direction: LatestDirection
    core_reasons: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    constraining_evidence: tuple[str, ...]
    main_contradiction: str
    monthly_view: str
    weekly_view: str
    daily_view: str
    continued_sectors: tuple[str, ...]
    new_sectors: tuple[str, ...]
    ebb_sectors: tuple[str, ...]
    structure_judgment: str
    strengthening_conditions: tuple[str, ...]
    weakening_conditions: tuple[str, ...]
    invalidation_condition: str
    quality_notes: tuple[str, ...]


def combine_latest_direction(etf_status: str, margin_signal: str) -> LatestDirection:
    directions: set[str] = set()
    if etf_status == "active":
        directions.add("supportive")
    elif etf_status == "inactive":
        directions.add("weakening")
    if margin_signal == "supportive":
        directions.add("supportive")
    elif margin_signal == "weakening":
        directions.add("weakening")
    if len(directions) == 2:
        return "mixed"
    if "supportive" in directions:
        return "supportive"
    if "weakening" in directions:
        return "weakening"
    return "unknown"


def _amount_yi(value: float) -> str:
    return f"{value / 100_000_000:+,.1f} 亿元"


def _daily_view(summary: WeeklyFlowSummary, latest: LatestDirection) -> str:
    if latest == "mixed":
        return "ETF 与两融方向分化，最新层暂不提供方向确认。"
    if latest == "supportive":
        return "ETF 或两融给出有效支持信号，最新层偏强。"
    if latest == "weakening":
        return "ETF 或两融给出有效转弱信号，最新层偏弱。"
    return "ETF 与两融均暂不判断，最新层不提供方向确认。"


def analyze_monthly_market(
    monthly: dict[str, Any],
    weekly: WeeklyFlowSummary,
) -> MonthlyMarketAnalysis:
    household = str(monthly.get("household", {}).get("status", "unknown"))
    nonbank = str(monthly.get("nonbank", {}).get("status", "unknown"))
    money = str(monthly.get("money_supply", {}).get("status", "unknown"))
    leverage = str(monthly.get("leverage", {}).get("status", "unknown"))
    market_state = monthly.get("market_state")
    latest = combine_latest_direction(
        weekly.latest_etf_status,
        weekly.latest_margin_signal,
    )
    counts = weekly.temperature_counts
    attack_days = counts.get("进攻", 0)
    observe_days = counts.get("观察", 0)
    defense_days = counts.get("防守", 0)
    macro_supportive = market_state in {"牛市加速", "慢牛蓄力"}
    weekly_positive = (
        weekly.main_net_inflow_sum > 0
        and weekly.super_large_net_inflow_sum > 0
    )
    weekly_negative = (
        weekly.main_net_inflow_sum < 0
        and weekly.super_large_net_inflow_sum < 0
    )

    if leverage == "overheated":
        stance, stage = "防守", "杠杆过热"
    elif monthly.get("report_mode") != "classified" or weekly.availability != "available":
        stance, stage = "观察", "数据不足"
    elif (
        macro_supportive
        and weekly_positive
        and attack_days > defense_days
        and latest == "supportive"
    ):
        stance, stage = "进攻准备", "增量确认"
    elif (
        weekly_negative
        and defense_days > attack_days
        and latest == "weakening"
    ):
        stance, stage = "防守", "减量防守"
    else:
        stance, stage = "观察", "存量结构"

    supporting: list[str] = []
    constraining: list[str] = []
    if household == "relocation_signal":
        supporting.append("居民存款出现搬家信号")
    if nonbank == "rising":
        supporting.append("非银存款增加，机构承接增强")
    elif nonbank == "falling":
        constraining.append("非银存款减少，机构承接尚未确认")
    if money == "improving":
        supporting.append("M1-M2 剪刀差改善，资金活化增强")
    elif money == "worsening":
        constraining.append("M1-M2 剪刀差恶化，资金活化不足")
    if leverage == "overheated":
        constraining.append("杠杆水位触发过热红线")
    if weekly_positive:
        supporting.append("周度主力与超大单累计均为净流入")
    elif weekly_negative:
        constraining.append("周度主力与超大单累计均为净流出")
    if latest == "supportive":
        supporting.append("最新 ETF/两融合成信号偏强")
    elif latest == "weakening":
        constraining.append("最新 ETF/两融合成信号偏弱")

    if leverage == "overheated":
        contradiction = "杠杆水位已经过热，风险约束优先于其他资金信号。"
    elif stage == "数据不足":
        contradiction = "市场资金快照或月度必要数据不足，暂不能完成日周月交叉验证。"
    elif stage == "增量确认":
        contradiction = "月度背景与周、日资金同向，增量资金得到初步确认。"
    elif stage == "减量防守" and macro_supportive:
        contradiction = "宏观支持仍在，但周、日资金已经转弱，战术上先转入防守。"
    elif household == "relocation_signal" and nonbank == "falling" and money == "worsening":
        contradiction = "场外资金已经松动，但机构承接和资金活化尚未确认。"
    else:
        contradiction = "月度背景与高频资金尚未形成同向确认，市场仍以结构博弈为主。"

    monthly_view = (
        f"宏观状态为{market_state or '暂不形成结论'}；"
        f"居民存款为{_STATUS_LABELS.get(household, '状态异常')}，"
        f"非银存款{_STATUS_LABELS.get(nonbank, '状态异常')}，"
        f"M1-M2 {_STATUS_LABELS.get(money, '状态异常')}，"
        f"杠杆{_STATUS_LABELS.get(leverage, '状态异常')}。"
    )
    weekly_view = (
        f"{weekly.start_date or '暂无'} 至 {weekly.end_date or '暂无'} 共"
        f" {weekly.snapshot_count} 份快照；主力累计 {_amount_yi(weekly.main_net_inflow_sum)}，"
        f"超大单累计 {_amount_yi(weekly.super_large_net_inflow_sum)}；"
        f"进攻 {attack_days} 天 / 观察 {observe_days} 天 / 防守 {defense_days} 天。"
    )
    daily_view = _daily_view(weekly, latest)
    structure = (
        "结构行情"
        if weekly.continued_sectors or weekly.new_sectors or weekly.ebb_sectors
        else "暂不可判断"
    )
    reasons = (
        monthly_view,
        weekly_view,
        daily_view,
    )
    return MonthlyMarketAnalysis(
        stance=stance,
        market_stage=stage,
        latest_direction=latest,
        core_reasons=reasons,
        supporting_evidence=tuple(supporting),
        constraining_evidence=tuple(constraining),
        main_contradiction=contradiction,
        monthly_view=monthly_view,
        weekly_view=weekly_view,
        daily_view=daily_view,
        continued_sectors=weekly.continued_sectors,
        new_sectors=weekly.new_sectors,
        ebb_sectors=weekly.ebb_sectors,
        structure_judgment=structure,
        strengthening_conditions=(
            "周度主力与超大单同步转为净流入",
            "进攻天数超过防守天数，且 ETF/两融最新层转为支持",
        ),
        weakening_conditions=(
            "周度主力与超大单同步转为净流出",
            "防守天数超过进攻天数，且 ETF/两融最新层转弱",
        ),
        invalidation_condition="月度必要数据或最近五日有效快照不足时，当前方向判断失效。",
        quality_notes=weekly.quality_notes,
    )
