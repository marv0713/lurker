from __future__ import annotations

from typing import Any

from lurker.reports.models import DailyReport


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
    "unknown": "暂不可用",
}


def _number(value: Any, suffix: str = "") -> str:
    if value is None:
        return "暂不可用"
    return f"{float(value):,.2f}{suffix}"


def _status(value: Any) -> str:
    return _STATUS_LABELS.get(str(value), "状态异常")


def render_monthly_macro_flow_report(
    snapshot: dict[str, Any],
    analysis: dict[str, Any],
) -> DailyReport:
    observation = analysis["report_mode"] == "data_observation"
    conclusion = (
        "数据不足，仅展示观察事实，不形成趋势结论。"
        if observation
        else f"本月状态：{analysis['market_state']}。"
    )
    household = analysis["household"]
    nonbank = analysis["nonbank"]
    money = analysis["money_supply"]
    leverage = analysis["leverage"]
    failures = analysis["failures"]
    quality_lines = (
        [
            f"- {item['source']}：{item['reason']}"
            for item in failures
        ]
        if failures
        else ["- 所有必要数据均通过契约校验。"]
    )
    source_lines = [
        f"- {item.get('url', item.get('source', '暂不可用'))}；"
        f"数据截止：{item.get('data_date', '暂不可用')}；"
        f"获取：{item.get('retrieved_at', '暂不可用')}；"
        f"哈希：{item.get('sha256', '暂不可用')}"
        for item in analysis["sources"]
    ]
    if not source_lines:
        source_lines = ["- 暂无可用来源元数据。"]

    content = "\n".join(
        [
            "# 宏观流动性月报",
            "",
            f"报告月份：{analysis['report_month']}",
            f"生成时间：{snapshot['generated_at']}",
            f"宏观数据截止月：{analysis['macro_month'] or '暂不可用'}",
            f"杠杆数据截止日："
            f"{leverage.get('trade_date') or '暂不可用'}",
            "",
            "## 一句话结论",
            "",
            conclusion,
            "",
            "## 牛市进度条",
            "",
            f"- 市场状态：{analysis['market_state'] or '暂不形成结论'}",
            "",
            "## 居民存款趋势",
            "",
            f"- 截止月：{analysis['macro_month'] or '暂不可用'}",
            "- 来源：中国人民银行《金融机构人民币信贷收支表》",
            f"- 状态：{_status(household['status'])}",
            f"- 当前余额：{_number(household.get('current'), '亿元')}",
            f"- 上月余额："
            f"{_number(household.get('previous_month'), '亿元')}",
            f"- 同比：{_number(household.get('yoy_pct'), '%')}",
            f"- 同比变化："
            f"{_number(household.get('yoy_change_pp'), '个百分点')}",
            "",
            "## 非银存款",
            "",
            f"- 截止月：{analysis['macro_month'] or '暂不可用'}",
            "- 来源：中国人民银行《金融机构人民币信贷收支表》",
            f"- 状态：{_status(nonbank['status'])}",
            f"- 当前余额：{_number(nonbank.get('current'), '亿元')}",
            f"- 上月余额："
            f"{_number(nonbank.get('previous_month'), '亿元')}",
            f"- 环比变化额："
            f"{_number(nonbank.get('mom_amount'), '亿元')}",
            f"- 环比：{_number(nonbank.get('mom_pct'), '%')}",
            "",
            "## M1-M2 活钱指标",
            "",
            f"- 截止月：{analysis['macro_month'] or '暂不可用'}",
            "- 来源：AkShare macro_china_money_supply",
            f"- 状态：{_status(money['status'])}",
            f"- 当前 M1 同比："
            f"{_number(money.get('current_m1_yoy_pct'), '%')}",
            f"- 当前 M2 同比："
            f"{_number(money.get('current_m2_yoy_pct'), '%')}",
            f"- 上月 M1 同比："
            f"{_number(money.get('previous_m1_yoy_pct'), '%')}",
            f"- 上月 M2 同比："
            f"{_number(money.get('previous_m2_yoy_pct'), '%')}",
            f"- 当前剪刀差："
            f"{_number(money.get('current_spread_pp'), '个百分点')}",
            f"- 较上月变化："
            f"{_number(money.get('spread_delta_pp'), '个百分点')}",
            "",
            "## 杠杆水位",
            "",
            f"- 截止日："
            f"{leverage.get('trade_date') or '暂不可用'}",
            f"- 上月杠杆基准日："
            f"{leverage.get('previous_trade_date') or '暂不可用'}",
            "- 来源：沪深融资余额与沪深交易所市场概况",
            f"- 状态：{_status(leverage['status'])}",
            f"- 当前融资余额："
            f"{_number(leverage.get('current_financing_balance'), '元')}",
            f"- 上月融资余额："
            f"{_number(leverage.get('previous_financing_balance'), '元')}",
            f"- A 股流通市值："
            f"{_number(leverage.get('a_share_circ_mv'), '元')}",
            f"- 融资余额/流通市值："
            f"{_number(leverage.get('ratio_pct'), '%')}",
            f"- 融资余额月增速："
            f"{_number(leverage.get('monthly_growth_pct'), '%')}",
            "",
            "## 数据质量",
            "",
            *quality_lines,
            "",
            "### 来源",
            "",
            *source_lines,
            "",
        ]
    )
    return DailyReport(
        report_date=analysis["report_month"],
        main_candidates_count=0,
        content_md=content,
    )
