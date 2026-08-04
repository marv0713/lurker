from __future__ import annotations

from typing import Any

from lurker.reports.trend_card import render_list


def _format_money(value: float | int | None) -> str:
    if value is None:
        return "-"
    amount = float(value)
    if abs(amount) >= 100_000_000:
        return f"{amount / 100_000_000:.1f}亿"
    if abs(amount) >= 10_000:
        return f"{amount / 10_000:.1f}万"
    return f"{amount:.0f}"


_SPRING_STATE_LABELS = {
    "first_bullish_confirmed": "首阳确认",
    "compressed_watch": "压紧观察",
    "weak_excluded": "弱弹簧排除",
}


def _format_spring_reason(reason: str, item: dict[str, Any]) -> str:
    if reason == "ma20_broken":
        return "连续2日有效跌破 MA20"
    if reason == "third_support_test":
        return f"近60日第{item.get('support_touch_count_60d', 0)}次回踩"
    if reason == "volume_not_compressed":
        ratio = item.get("volume_compression_ratio")
        if ratio is None:
            return "回踩时缩量不足"
        return f"回踩时缩量不足（缩量比 {float(ratio):.0%}）"
    return "规则原因暂不可用"


def _format_spring_item(item: dict[str, Any]) -> str:
    state = str(item.get("state", ""))
    parts = [
        f"{item['name']} ({item['symbol']})："
        f"{_SPRING_STATE_LABELS.get(state, '暂不判断')}"
    ]
    reasons = item.get("reasons", [])
    if state == "weak_excluded" and reasons:
        reason_text = "；".join(
            _format_spring_reason(str(reason), item) for reason in reasons
        )
        parts.append(f"原因：{reason_text}")
    distance = item.get("ma20_distance_pct")
    if distance is not None:
        parts.append(f"距 MA20 {float(distance):+.1%}")
    ratio = item.get("volume_compression_ratio")
    if ratio is not None:
        parts.append(f"缩量比 {float(ratio):.0%}")
    touches = item.get("support_touch_count_60d")
    if touches:
        parts.append(f"近60日第{int(touches)}次回踩")
    return "｜".join(parts)


def _render_spring_items(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- 暂无"
    return "\n".join(f"- {_format_spring_item(item)}" for item in items)


def render_professional_flow_report(
    *,
    report_date: str,
    market_temperature: str,
    market_notes: list[str],
    sector_leaders: list[dict[str, Any]],
    stock_flow_leaders: list[dict[str, Any]],
    two_percent_candidates: list[dict[str, Any]],
    spring_scan: dict[str, list[dict[str, Any]]],
    invalidation_alerts: list[str],
    data_quality: list[str],
    conclusion: str | None = None,
) -> str:
    sector_lines = [
        (
            f"{item['name']}：主力净流入 "
            f"{_format_money(item.get('main_net_inflow'))}，"
            f"当日资金状态：{item.get('label', '主线')}"
        )
        for item in sector_leaders
    ]
    candidate_lines = [
        (
            f"{item['name']} ({item['symbol']})：总分 {item['score']:.1f}，"
            f"{item['label']}，主力净流入 {_format_money(item.get('main_net_inflow'))}"
        )
        for item in two_percent_candidates
    ]
    stock_lines = [
        (
            f"{item['name']} ({item['symbol']})："
            f"今日 {_format_money(item.get('main_net_inflow'))}，"
            f"5日 {_format_money(item.get('main_net_inflow_5d'))}，"
            f"10日 {_format_money(item.get('main_net_inflow_10d'))}"
        )
        for item in (stock_flow_leaders or [])
    ]
    spring_explanation = (
        "三态说明：压紧观察表示上升的 MA20 附近已缩量，但尚未出现首阳；"
        "首阳确认表示压紧后出现第一根阳线，仅代表形态确认；"
        "弱弹簧排除表示反复回踩、跌破支撑或缩量不足，暂不列入候选。"
    )
    if market_temperature == "防守":
        spring_explanation += "\n\n防守模式：三态结果仅供形态跟踪，不进入候选。"

    effective_conclusion = conclusion or f"今日状态：{market_temperature}。"
    return fr"""# 职业资金雷达日报

日期：{report_date}

## 一句话结论

{effective_conclusion}

## 市场资金温度

{render_list(market_notes)}

## 今日资金主线

{render_list(sector_lines)}

## 2%候选

{render_list(candidate_lines)}

## 核心股票资金流向

{render_list(stock_lines)}

## 弹簧三态扫描

{spring_explanation}

### 首阳确认

{_render_spring_items(spring_scan.get('confirmed', []))}

### 压紧观察

{_render_spring_items(spring_scan.get('watch', []))}

### 弱弹簧排除

{_render_spring_items(spring_scan.get('excluded', []))}

## 证伪/退潮提醒

{render_list(invalidation_alerts)}

## 数据质量

{render_list(data_quality)}
"""
