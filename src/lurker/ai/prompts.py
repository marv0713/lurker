ATTRIBUTION_SYSTEM_PROMPT = """你是一个投资趋势雷达的归因助手。
你的任务不是给买卖建议，而是判断已触发的异动信号属于产业趋势、事件驱动、题材炒作还是证据不足。
请只输出符合 schema 的 JSON，不要输出任何其他文字。"""


def build_attribution_prompt(
    *,
    candidate_name: str,
    trigger_reason: str,
    source_summaries: list[str],
) -> str:
    joined_sources = "\n".join(f"- {summary}" for summary in source_summaries)
    return f"""候选：{candidate_name}
触发原因：{trigger_reason}

资料摘要：
{joined_sources}

请输出：
- classification: 产业趋势型、事件驱动型、题材炒作型、证据不足型之一
- reason_summary: 一句话归因
- evidence: 只能从 新闻、公告、财报、订单、政策 中选择
- risk_flags: 风险标签
- upgrade_recommendation: 升级、降级、观察、证据不足之一
- missing_evidence: 仍需验证的事项
"""


def build_attribution_prompt_from_signal(
    *,
    symbol: str,
    market: str,
    returns: dict[str, float],
    percentiles: dict[str, float],
    double_bagger_class: str,
    extra_sources: list[str] | None = None,
) -> str:
    """从 StockSignal 字段构建归因 prompt。

    第一版没有新闻/公告数据，只传价格维度信息。
    LLM 会根据代码、市场和涨幅分位数做初步判断，并明确说明证据不足。
    后续接入新闻抓取后，传入 extra_sources 补充文本上下文。
    """
    market_label = {"cn": "A 股", "us": "美股", "hk": "港股"}.get(market, market.upper())

    return_lines = []
    for key, val in sorted(returns.items()):
        window = key.replace("return_", "").replace("d", "")
        pct_key = f"{key}_percentile"
        pct = percentiles.get(pct_key)
        pct_str = f"（市场内分位数 {pct:.0%}）" if pct is not None else ""
        return_lines.append(f"  - {window} 日涨幅：{val * 100:.1f}%{pct_str}")

    returns_text = "\n".join(return_lines) if return_lines else "  - 无窗口收益数据"

    db_label = {
        "multi_bagger": "超级多倍股（>200%）",
        "double": "翻倍股（>100%）",
        "near_double": "准翻倍股（>80%）",
        "none": "未达翻倍",
    }.get(double_bagger_class, double_bagger_class)

    sources_section = ""
    if extra_sources:
        joined = "\n".join(f"- {s}" for s in extra_sources)
        sources_section = f"\n额外参考资料（新闻/公告）：\n{joined}\n"

    return f"""股票代码：{symbol}
所属市场：{market_label}
翻倍股分类：{db_label}

价格表现：
{returns_text}
{sources_section}
注意：当前没有新闻或公告数据，请基于市场知识对该股票做初步归因判断。
如果信息不足以做出明确判断，请将 classification 设为"证据不足型"，upgrade_recommendation 设为"证据不足"。

请以 JSON 格式输出以下字段：
- classification: "产业趋势型" | "事件驱动型" | "题材炒作型" | "证据不足型"
- reason_summary: 一句话归因（不超过 50 字）
- evidence: 列表，只能从 ["新闻", "公告", "财报", "订单", "政策"] 中选择
- risk_flags: 字符串列表，识别到的风险点
- upgrade_recommendation: "升级" | "降级" | "观察" | "证据不足"
- missing_evidence: 字符串列表，仍需验证的事项
"""
