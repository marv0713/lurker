from __future__ import annotations

from collections import defaultdict

from lurker.signals.anomaly import AlertType, AnomalyAlert


ALERT_LABELS = {
    AlertType.ABNORMAL_VOLUME: "🚨 巨量异动",
    AlertType.PEAK_DRAWDOWN: "⚠️ 高位回撤",
    AlertType.CHRONIC_UNDERPERFORMANCE: "📉 持续跑输",
}


def _alert_detail(alert: AnomalyAlert) -> str:
    if alert.alert_type is AlertType.ABNORMAL_VOLUME:
        return (
            f"今日放量 {alert.metrics['volume_ratio']:.2f} 倍，"
            f"涨跌幅 {alert.metrics['price_change'] * 100:.2f}%"
        )
    if alert.alert_type is AlertType.PEAK_DRAWDOWN:
        return f"从 250 日高点回撤 {abs(alert.metrics['drawdown']) * 100:.2f}%"
    return f"近 60 日跑输基准 {abs(alert.metrics['alpha_60d']) * 100:.2f}%"


def render_watchlist_alerts(
    *,
    report_date: str,
    alerts: list[AnomalyAlert],
    data_issues: list[str],
    checked_count: int,
) -> str:
    lines = [
        "# 自选股异常体检",
        "",
        f"报告日期：{report_date}",
        f"检查标的：{checked_count} 只",
        f"新异常：{len(alerts)} 条",
        "",
    ]
    if not alerts:
        lines.extend(["本次没有需要推送的新异常。", ""])
    else:
        grouped: dict[str, list[AnomalyAlert]] = defaultdict(list)
        for alert in alerts:
            grouped[alert.symbol.upper()].append(alert)
        for symbol in sorted(grouped):
            symbol_alerts = grouped[symbol]
            first = symbol_alerts[0]
            lines.extend([f"## {first.name}（{symbol}）", ""])
            for alert in sorted(symbol_alerts, key=lambda item: item.alert_type.value):
                lines.append(f"- {ALERT_LABELS[alert.alert_type]}：{_alert_detail(alert)}")
            observed_on = max(alert.observed_on for alert in symbol_alerts)
            lines.extend([f"- 数据截止日：{observed_on}", ""])

    lines.extend(["## 数据质量", ""])
    if data_issues:
        lines.extend(f"- {issue}" for issue in data_issues)
    else:
        lines.append("- 所有已启用检测均获得足够数据。")
    return "\n".join(lines).rstrip() + "\n"
