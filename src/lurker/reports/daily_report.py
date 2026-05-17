from lurker.reports.trend_card import render_list


def render_daily_report(
    *,
    report_date: str,
    main_cards: list[str],
    secondary_leads: list[str],
    watchlist_changes: list[str],
    risk_alerts: list[str],
) -> str:
    main_content = "\n\n".join(main_cards) if main_cards else "今日无主候选。"
    return f"""# 大趋势雷达日报

日期：{report_date}

## 今日主候选

{main_content}

## 次级线索

{render_list(secondary_leads)}

## 观察池变化

{render_list(watchlist_changes)}

## 过热或证伪提醒

{render_list(risk_alerts)}
"""
