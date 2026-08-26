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
TRIGGER_STATE_LABELS = {
    "unknown": "不可判断",
    "support_holding": "支撑横住",
    "primed": "压紧就绪",
    "trigger_fired": "扳机扣动",
    "support_broken": "支撑跌破",
}
TRIGGER_REASON_LABELS = {
    "support_broken": "收盘跌破支撑",
    "insufficient_history": "历史日线不足",
    "turnover_unavailable": "成交额数据缺失",
    "invalid_trade_date": "交易日期无效",
    "duplicate_trade_date": "交易日期重复",
    "invalid_price_data": "价格数据无效",
    "invalid_volume_data": "成交量数据无效",
}
ACTION_STATUS = {"expected": "预计", "confirmed": "已确认"}
QUALITY_LABELS = {"micro": "微弱首阳", "standard": "标准首阳", "strong": "强首阳"}
DIRECTION_SYMBOLS = {"up": "↗", "down": "↘", "flat": "→"}
SPRING_REASON_LABELS = {
    "ma20_broken": "连续2日有效跌破 MA20",
    "third_support_test": "近60日第{touches}次回踩",
    "volume_not_compressed": "回踩时缩量不足",
    "hk_insufficient_turnover": "流动性不足",
    "hk_insufficient_positive_volume_days": "有效成交日不足",
    "hk_zero_volume_in_compression_window": "压紧窗口存在零成交量",
    "invalid_trade_date": "交易日期无效",
    "duplicate_trade_date": "交易日期重复",
    "insufficient_history": "历史日线不足",
    "invalid_price_data": "价格数据无效",
    "invalid_volume_data": "成交量数据无效",
}
ACTION_TYPE_LABELS = {
    "earnings": "财报披露",
    "dividend": "分红",
    "split": "拆股",
    "consolidation": "合股",
    "rights_issue": "配股/供股",
    "additional_issuance": "增发",
}
UNSUPPORTED_ACTION_ORDER = (
    "additional_issuance",
    "consolidation",
    "rights_issue",
    "split",
    "dividend",
    "earnings",
)
MARKET_LABELS = {"cn": "A股", "hk": "港股"}


def _pct(value: float) -> str:
    return f"{value:+.1%}"


def _ma_line(fact: MovingAverageFact | None) -> str:
    if fact is None:
        return "不可用"
    position = "上方" if fact.distance_pct >= 0 else "下方"
    return f"{position} {_pct(fact.distance_pct)} {DIRECTION_SYMBOLS[fact.direction]}"


def _spring_reason(reason: str, spring: Mapping[str, object]) -> str:
    label = SPRING_REASON_LABELS.get(reason)
    if label is None:
        return "规则原因暂不可用"
    if reason == "third_support_test":
        touches = spring.get("support_touch_count_60d", 0)
        return label.format(touches=touches)
    if reason == "volume_not_compressed":
        ratio = spring.get("volume_compression_ratio")
        if isinstance(ratio, (int, float)):
            return f"{label}（缩量比 {float(ratio):.0%}）"
    return label


def _spring_line(spring: Mapping[str, object] | None) -> str:
    if spring is None:
        return "不可用"
    state = str(spring.get("state", "unknown"))
    prefix = "港股实验弹簧：" if spring.get("experimental") else "A股正式弹簧："
    result = prefix + SPRING_LABELS.get(state, state)
    reasons = spring.get("reasons")
    reason_labels = (
        [_spring_reason(str(reason), spring) for reason in reasons]
        if isinstance(reasons, list)
        else []
    )
    if spring.get("experimental") and state == "unknown" and reason_labels:
        result += "：" + "；".join(reason_labels)
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
    if reason_labels and not (spring.get("experimental") and state == "unknown"):
        details.append("原因 " + "；".join(reason_labels))
    return result + ("｜" + "｜".join(details) if details else "")


def _quality_line(quality: FirstBullishQuality | None) -> str | None:
    if quality is None:
        return None
    return (
        f"首阳质量：实体比例 {_pct(quality.entity_ratio)}｜"
        f"当日涨跌 {_pct(quality.daily_return)}｜{QUALITY_LABELS[quality.label]}"
    )


def _turnover_yi(value: float) -> str:
    return f"{value / 1e8:.1f} 亿"


def _trigger_line(trigger: Mapping[str, object] | None) -> str | None:
    if trigger is None:
        return None
    state = str(trigger.get("state", "unknown"))
    label = TRIGGER_STATE_LABELS.get(state, state)
    result = f"扳机信号：{label}"
    if state == "unknown":
        reasons = trigger.get("reasons")
        labels = (
            [
                TRIGGER_REASON_LABELS.get(str(reason), "规则原因暂不可用")
                for reason in reasons
            ]
            if isinstance(reasons, list)
            else []
        )
        return result + ("（" + "；".join(labels) + "）" if labels else "")
    support = trigger.get("support")
    shrink = trigger.get("shrink")
    if isinstance(support, Mapping):
        low = support.get("low")
        high = support.get("high")
        window = support.get("window_days")
        min_close = support.get("min_close_in_window")
        zone_days = support.get("days_in_zone")
        details: list[str] = []
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            details.append(f"支撑 {float(low):.2f}-{float(high):.2f}")
        if isinstance(min_close, (int, float)) and isinstance(window, int):
            details.append(f"近{window}日最低收盘 {float(min_close):.2f}")
        if isinstance(zone_days, int) and isinstance(window, int):
            details.append(f"区间内 {zone_days}/{window} 日")
        if details:
            result += "｜" + "｜".join(details)
    if isinstance(shrink, Mapping) and isinstance(shrink.get("consecutive_days"), int):
        result += f"｜连续缩量 {shrink['consecutive_days']} 日"
        latest = shrink.get("latest_turnover")
        if isinstance(latest, (int, float)):
            result += f"（最新 {_turnover_yi(float(latest))}）"
    if state == "trigger_fired":
        trigger_day = trigger.get("trigger")
        if isinstance(trigger_day, Mapping):
            gain = trigger_day.get("gain_pct")
            turnover = trigger_day.get("turnover")
            trade_date = trigger_day.get("trade_date")
            parts: list[str] = []
            if isinstance(gain, (int, float)):
                parts.append(f"涨幅 {float(gain):+.1%}")
            if isinstance(turnover, (int, float)):
                parts.append(f"成交额 {_turnover_yi(float(turnover))}")
            if parts and isinstance(trade_date, str):
                parts.append(f"日期 {trade_date}")
            if parts:
                result += "｜扳机日 " + "、".join(parts)
    return result


def _entry_plan_lines(fact: PersonalStockReportFact) -> list[str]:
    trigger = fact.spring_trigger
    if not isinstance(trigger, Mapping) or trigger.get("state") != "trigger_fired":
        return []
    plan = trigger.get("entry_plan")
    if not isinstance(plan, Mapping):
        return []
    entry = plan.get("entry_reference")
    stop = plan.get("stop_price")
    if not isinstance(entry, (int, float)) or not isinstance(stop, (int, float)):
        return []
    return [
        f"- 参与参考：试探价 {float(entry):.2f}｜止损 {float(stop):.2f}"
        "（阳线低点下方；收盘跌破次日开盘无条件执行，两个隔夜止损）"
    ]


def _actions_line(fact: PersonalStockReportFact) -> str:
    if fact.actions:
        rendered = "；".join(_render_action(action) for action in fact.actions)
        return f"未来两周：{rendered}"
    if fact.action_coverage_complete:
        if fact.unsupported_event_types:
            return "未来两周：已支持类型暂无已知事件"
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
    long_risk = sum(
        fact.trend is not None and fact.trend.label in {"long_structure_weakened", "testing_ma200"}
        for fact in holdings
    )
    pullback = sum(
        fact.trend is not None
        and (
            (fact.trend.ma20 is not None and fact.trend.ma20.distance_pct < 0)
            or fact.trend.label in {"long_up_medium_pullback", "mixed"}
        )
        for fact in holdings
    )
    formal_confirmed_holdings = sum(
        fact.config.market == "cn"
        and fact.spring is not None
        and fact.spring.get("state") == "first_bullish_confirmed"
        for fact in holdings
    )
    formal_confirmed_all = sum(
        fact.config.market == "cn"
        and fact.spring is not None
        and fact.spring.get("state") == "first_bullish_confirmed"
        for fact in (*holdings, *facts.watchlist)
    )
    formal_compressed = sum(
        fact.config.market == "cn"
        and fact.spring is not None
        and fact.spring.get("state") == "compressed_watch"
        for fact in (*holdings, *facts.watchlist)
    )
    trigger_fired = sum(
        fact.spring_trigger is not None
        and fact.spring_trigger.get("state") == "trigger_fired"
        for fact in (*holdings, *facts.watchlist)
    )
    trigger_primed = sum(
        fact.spring_trigger is not None
        and fact.spring_trigger.get("state") == "primed"
        for fact in (*holdings, *facts.watchlist)
    )
    action_count = sum(len(fact.actions) for fact in (*holdings, *facts.watchlist))
    hk_observations = sum(
        fact.config.market == "hk"
        and fact.spring is not None
        and fact.spring.get("state") in {"compressed_watch", "first_bullish_confirmed"}
        for fact in (*holdings, *facts.watchlist)
    )

    clauses: list[str] = []
    if long_risk:
        clauses.append(f"{long_risk} 只持仓长期结构转弱或测试 MA200")
    if pullback:
        clauses.append(f"{pullback} 只持仓位于 MA20 下方或处于回踩/混合")
    if action_count:
        clauses.append(f"未来 14 日有 {action_count} 项重要公司行动")
    if formal_confirmed_holdings:
        clauses.append(f"{formal_confirmed_holdings} 只持仓出现 A 股正式弹簧首阳确认")
    remaining_confirmed = formal_confirmed_all - formal_confirmed_holdings
    if remaining_confirmed:
        clauses.append(f"{remaining_confirmed} 只 A 股观察标的出现正式弹簧首阳确认")
    if formal_compressed:
        clauses.append(f"{formal_compressed} 只 A 股标的进入正式弹簧压紧观察")
    if trigger_fired:
        clauses.append(f"{trigger_fired} 只标的弹簧扳机扣动")
    elif trigger_primed:
        clauses.append(f"{trigger_primed} 只标的压紧就绪、等待扳机")
    if hk_observations:
        clauses.append(f"另有 {hk_observations} 只港股实验弹簧观察")
    incomplete = _is_incomplete(facts)
    if clauses:
        result = "；".join(clauses) + "。"
    elif incomplete:
        result = "当前未发现可确认的优先重点。"
    else:
        result = "持仓趋势整体稳定，暂无正式弹簧确认。"
    if incomplete:
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
    trigger_line = _trigger_line(fact.spring_trigger)
    if trigger_line:
        lines.append(f"- {trigger_line}")
    lines.extend(_entry_plan_lines(fact))
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
    unsupported_by_market: dict[str, set[str]] = {}
    for fact in (*facts.holdings, *facts.watchlist):
        unsupported_by_market.setdefault(fact.config.market, set()).update(
            fact.unsupported_event_types
        )
    for market, unsupported in unsupported_by_market.items():
        if not unsupported:
            continue
        ordered = [item for item in UNSUPPORTED_ACTION_ORDER if item in unsupported]
        labels = "、".join(ACTION_TYPE_LABELS[item] for item in ordered)
        quality_lines.append(
            f"- {MARKET_LABELS.get(market, '其他市场')}：公司行动数据暂未覆盖{labels}"
        )
    lines.extend(["## 数据质量", ""])
    lines.extend(quality_lines or ["- 未发现已知数据质量问题"])
    return "\n".join(lines).rstrip() + "\n"
