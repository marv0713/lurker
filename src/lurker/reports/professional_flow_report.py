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


def render_professional_flow_report(
    *,
    report_date: str,
    market_temperature: str,
    market_notes: list[str],
    sector_leaders: list[dict[str, Any]],
    two_percent_candidates: list[dict[str, Any]],
    setup_watch: list[dict[str, Any]],
    invalidation_alerts: list[str],
    data_quality: list[str],
    conclusion: str | None = None,
) -> str:
    sector_lines = [
        f"{item['name']}：主力净流入 {_format_money(item.get('main_net_inflow'))}，{item.get('label', '主线')}"
        for item in sector_leaders
    ]
    candidate_lines = [
        (
            f"{item['name']} ({item['symbol']})：总分 {item['score']:.1f}，"
            f"{item['label']}，主力净流入 {_format_money(item.get('main_net_inflow'))}"
        )
        for item in two_percent_candidates
    ]
    setup_lines = [
        (
            f"{item['name']} ({item['symbol']})：总分 {item['score']:.1f}，"
            f"{item.get('label', '弹簧买点观察')}"
        )
        for item in setup_watch
    ]

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

## 弹簧买点观察

{render_list(setup_lines)}

## 证伪/退潮提醒

{render_list(invalidation_alerts)}

## 数据质量

{render_list(data_quality)}
"""
