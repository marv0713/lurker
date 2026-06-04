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


def classify_market_temperature(
    *,
    market_flow: dict[str, Any],
    margin: dict[str, Any],
    core_etfs: list[dict[str, Any]],
) -> str:
    main_flow = _as_float(market_flow.get("main_net_inflow"))
    super_large_flow = _as_float(market_flow.get("super_large_net_inflow"))
    margin_change = _as_float(margin.get("margin_balance_change"))
    etf_active = any(_as_float(etf.get("turnover_expansion")) >= 1.2 for etf in core_etfs)

    if main_flow > 0 and super_large_flow > 0 and margin_change >= 0:
        return "进攻"
    if main_flow < 0 and super_large_flow < 0 and not etf_active:
        return "防守"
    return "观察"


def _percentile_rank(value: float, values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(1 for item in values if item < value) / len(values)


def _trend_scores(snapshots: list[dict[str, Any]]) -> dict[str, float]:
    cn_rows = [row for row in snapshots if row.get("market") == "cn"]
    return_20d = [_as_float(row.get("return_20d")) for row in cn_rows]
    return_60d = [_as_float(row.get("return_60d")) for row in cn_rows]
    scores: dict[str, float] = {}
    for row in cn_rows:
        p20 = _percentile_rank(_as_float(row.get("return_20d")), return_20d)
        p60 = _percentile_rank(_as_float(row.get("return_60d")), return_60d)
        long_return = max(_as_float(row.get("return_120d")), _as_float(row.get("return_180d")))
        scores[str(row["symbol"]).upper()] = min(100.0, p20 * 35 + p60 * 35 + max(long_return, 0) * 30)
    return scores


def _sector_score(symbol: str, theme_mapping: dict[str, list[str]], sector_flows: list[dict[str, Any]]) -> tuple[float, str | None]:
    themes = theme_mapping.get(symbol, [])
    if not themes:
        return 0.0, None
    by_name = {str(flow.get("name")): flow for flow in sector_flows}
    best_score = 0.0
    best_theme = None
    for theme in themes:
        flow = by_name.get(theme)
        if not flow:
            continue
        rank = int(flow.get("rank", 999))
        inflow = _as_float(flow.get("main_net_inflow"))
        score = 70.0 if rank <= 3 and inflow > 0 else 40.0 if inflow > 0 else 0.0
        if score > best_score:
            best_score = score
            best_theme = theme
    return best_score, best_theme


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
    return notes


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
    temperature = classify_market_temperature(
        market_flow=market_flow,
        margin=margin,
        core_etfs=core_etfs,
    )
    trend_scores = _trend_scores(price_snapshot.get("snapshots", []))
    candidates: list[dict[str, Any]] = []

    for flow in stock_flows:
        symbol = str(flow.get("symbol", "")).upper()
        if not symbol or symbol not in trend_scores:
            continue
        sector_score, theme = _sector_score(symbol, theme_mapping, sector_flows)
        flow_score = _stock_flow_score(flow)
        trend_score = trend_scores[symbol]
        setup_score = 60.0 if trend_score >= 50 and flow_score >= 50 else 20.0
        total = sector_score * 0.30 + flow_score * 0.35 + trend_score * 0.20 + setup_score * 0.15
        label = "2%候选" if sector_score >= 70 and flow_score >= 70 and trend_score >= 50 else "资金确认"
        candidates.append(
            {
                "symbol": symbol,
                "name": (symbol_names or {}).get(symbol) or flow.get("name") or symbol,
                "score": round(total, 1),
                "label": label,
                "main_net_inflow": _as_float(flow.get("main_net_inflow")),
                "theme": theme,
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    two_percent = [item for item in candidates if item["label"] == "2%候选"][:10]
    setup_watch = [item for item in candidates if item["label"] != "2%候选"][:10]
    sector_leaders = [
        {**flow, "label": "主线" if int(flow.get("rank", 999)) <= 3 else "扩散"}
        for flow in sector_flows[:10]
    ]
    data_quality = []
    for failure in flow_snapshot.get("failures", []):
        data_quality.append(f"{failure.get('source')}：{failure.get('reason')}")
    if not flow_snapshot:
        data_quality.append("缺少资金流快照，仅能输出空报告。")
    if not data_quality:
        data_quality.append("关键资金流数据已加载。")

    content = render_professional_flow_report(
        report_date=report_date,
        market_temperature=temperature,
        market_notes=_market_notes(market_flow, margin, temperature),
        sector_leaders=sector_leaders,
        two_percent_candidates=two_percent,
        setup_watch=setup_watch,
        invalidation_alerts=[],
        data_quality=data_quality,
    )
    return DailyReport(
        report_date=report_date,
        main_candidates_count=len(two_percent),
        content_md=content,
    )
