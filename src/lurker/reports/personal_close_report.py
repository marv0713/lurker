from __future__ import annotations

from collections.abc import Mapping

from lurker.domain.personal_close import (
    CorporateAction,
    FirstBullishQuality,
    MovingAverageFact,
    PersonalReportFacts,
    PersonalStockReportFact,
)


TREND_LABELS = {
    "long_structure_weakened": "长期结构转弱",
    "testing_ma200": "正在考验 MA200",
    "long_medium_strong": "中长期强势",
    "long_up_medium_pullback": "长期向上、中期回踩",
    "medium_repair": "中期修复",
    "mixed": "多周期分化",
    "data_insufficient": "数据不足",
}
SPRING_LABELS = {
    "none": "无弹簧形态",
    "compressed_watch": "缩量回踩观察",
    "first_bullish_confirmed": "首阳确认",
    "weak_excluded": "弱弹簧排除",
    "unknown": "不可判断",
}
ACTION_STATUS = {"expected": "预计", "confirmed": "已确认"}
QUALITY_LABELS = {"micro": "微弱首阳", "standard": "标准首阳", "strong": "强首阳"}
DIRECTION_SYMBOLS = {"up": "↑", "down": "↓", "flat": "→"}


def _pct(value: float) -> str:
    return f"{value:+.1%}"


def _ma_line(fact: MovingAverageFact | None) -> str:
    if fact is None:
        return "不可用"
    position = "上方" if fact.distance_pct >= 0 else "下方"
    return f"{position} {_pct(fact.distance_pct)} {DIRECTION_SYMBOLS[fact.direction]}"


def _spring_line(spring: Mapping[str, object] | None) -> str:
    if spring is None:
        return "不可用"
    state = str(spring.get("state", "unknown"))
    prefix = "港股实验弹簧：" if spring.get("experimental") else "A股正式弹簧："
    result = prefix + SPRING_LABELS.get(state, state)
    distance = spring.get("ma20_distance_pct")
    ratio = spring.get("volume_compression_ratio")
    touches = spring.get("support_touch_count_60d")
    details: list[str] = []
    if isinstance(distance, (int, float)):
        details.append(f"距MA20 {_pct(float(distance))}")
    if isinstance(ratio, (int, float)):
        details.append(f"缩量比 {float(ratio):.1%}")
    if isinstance(touches, int):
        details.append(f"近60日回踩 {touches} 次")
    reasons = spring.get("reasons")
    if isinstance(reasons, list) and reasons:
        details.append("原因 " + "/".join(str(reason) for reason in reasons))
    return result + ("｜" + "｜".join(details) if details else "")


def _quality_line(quality: FirstBullishQuality | None) -> str | None:
    if quality is None:
        return None
    return (
        f"首阳质量：实体比例 {_pct(quality.entity_ratio)}｜"
        f"当日涨跌 {_pct(quality.daily_return)}｜{QUALITY_LABELS[quality.label]}"
    )


def _actions_line(fact: PersonalStockReportFact) -> str:
    if fact.actions:
        rendered = "；".join(_render_action(action) for action in fact.actions)
        return f"未来两周：{rendered}"
    if fact.action_coverage_complete:
        return "未来两周：暂无已知事件"
    return "未来两周：公司行动日历不完整"


def _render_action(action: CorporateAction) -> str:
    detail = f"{action.primary_date.isoformat()} {action.summary}（{ACTION_STATUS[action.status]}）"
    supplemental: list[str] = []
    if action.record_date is not None:
        supplemental.append(f"登记日 {action.record_date.isoformat()}")
    if action.payment_date is not None:
        supplemental.append(f"派息日 {action.payment_date.isoformat()}")
    return detail + ("［" + "，".join(supplemental) + "］" if supplemental else "")


def _stock_conclusion(fact: PersonalStockReportFact) -> str:
    trend = "数据不足" if fact.trend is None else TREND_LABELS[fact.trend.label]
    spring = "弹簧不可用"
    if fact.spring is not None:
        spring = SPRING_LABELS.get(str(fact.spring.get("state", "unknown")), "不可判断")
    return f"{trend}；{spring}。"


def _is_incomplete(facts: PersonalReportFacts) -> bool:
    for fact in (*facts.holdings, *facts.watchlist):
        if (
            fact.issues
            or not fact.action_coverage_complete
            or fact.trend is None
            or fact.trend.label == "data_insufficient"
        ):
            return True
    return bool(facts.issues)


def _one_line(facts: PersonalReportFacts) -> str:
    holdings = facts.holdings
    weak = sum(
        fact.trend is not None
        and fact.trend.label in {"long_structure_weakened", "testing_ma200"}
        for fact in holdings
    )
    weak_springs = sum(
        fact.spring is not None and fact.spring.get("state") == "weak_excluded"
        for fact in holdings
    )
    confirmed = sum(
        fact.config.market == "cn"
        and fact.spring is not None
        and fact.spring.get("state") == "first_bullish_confirmed"
        for fact in holdings
    )
    action_count = sum(len(fact.actions) for fact in (*holdings, *facts.watchlist))
    hk_observations = sum(
        fact.config.market == "hk"
        and fact.spring is not None
        and fact.spring.get("state") in {"compressed_watch", "first_bullish_confirmed"}
        for fact in (*holdings, *facts.watchlist)
    )

    clauses: list[str] = []
    if weak or weak_springs:
        clauses.append(f"持仓风险优先：{weak} 只长期结构承压，{weak_springs} 只弱弹簧排除")
    elif confirmed:
        clauses.append(f"{confirmed} 只持仓出现 A 股正式弹簧首阳确认")
    else:
        clauses.append("持仓未见优先级更高的长期结构或正式弹簧风险")
    if action_count:
        clauses.append(f"未来 14 日有 {action_count} 项重要公司行动")
    if hk_observations:
        clauses.append(f"另有 {hk_observations} 只港股实验弹簧观察")
    result = "；".join(clauses) + "。"
    if _is_incomplete(facts):
        result += "部分数据不完整，详见数据质量。"
    return result


def _render_stock(fact: PersonalStockReportFact) -> list[str]:
    title = f"### {fact.config.name}（{fact.config.symbol}）"
    as_of = fact.as_of.isoformat() if fact.as_of is not None else "不可用"
    close = f"{fact.adjusted_close:.2f}" if fact.adjusted_close is not None else "不可用"
    lines = [title, "", f"结论：{_stock_conclusion(fact)}", ""]
    if not fact.market_open:
        lines.extend([f"今日休市，数据截止至 {as_of}", ""])
    lines.append(f"- 收盘：{close}｜行情日期：{as_of}")
    trend = fact.trend
    lines.extend(
        [
            f"- MA5：{_ma_line(trend.ma5 if trend else None)}",
            f"- MA20：{_ma_line(trend.ma20 if trend else None)}",
            f"- MA200：{_ma_line(trend.ma200 if trend else None)}",
            f"- 趋势：{TREND_LABELS[trend.label] if trend else '数据不足'}",
            f"- 弹簧：{_spring_line(fact.spring)}",
        ]
    )
    quality = _quality_line(fact.bullish_quality)
    if quality:
        lines.append(f"- {quality}")
    lines.append(f"- {_actions_line(fact)}")
    return lines


def render_personal_close_report(facts: PersonalReportFacts) -> str:
    lines = [
        "# 个人持仓与观察池盘后简报",
        "",
        f"报告日期：{facts.report_date.isoformat()}",
        "",
        f"一句话结论：{_one_line(facts)}",
        "",
    ]
    for heading, group in (("持仓", facts.holdings), ("观察池", facts.watchlist)):
        lines.extend([f"## {heading}", ""])
        if not group:
            lines.extend(["- 无", ""])
            continue
        for fact in group:
            lines.extend(_render_stock(fact))
            lines.append("")

    quality_lines: list[str] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for issue in (
        *facts.issues,
        *(issue for fact in (*facts.holdings, *facts.watchlist) for issue in fact.issues),
    ):
        key = (issue.code, issue.symbol, issue.market)
        if key not in seen:
            seen.add(key)
            target = issue.symbol or issue.market or "全局"
            quality_lines.append(f"- {target}：{issue.message}")
    for fact in (*facts.holdings, *facts.watchlist):
        if fact.unsupported_event_types:
            quality_lines.append(
                f"- {fact.config.name}（{fact.config.symbol}）：公司行动未覆盖 "
                + ", ".join(fact.unsupported_event_types)
            )
    lines.extend(["## 数据质量", ""])
    lines.extend(quality_lines or ["- 未发现已知数据质量问题"])
    return "\n".join(lines).rstrip() + "\n"
