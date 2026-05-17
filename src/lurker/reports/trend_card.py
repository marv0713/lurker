def render_list(items: list[str]) -> str:
    if not items:
        return "- 无"
    return "\n".join(f"- {item}" for item in items)


def render_trend_card(
    *,
    theme: str,
    status: str,
    stage: str,
    total_score: float,
    triggers: list[str],
    attribution: str,
    evidence: list[str],
    risks: list[str],
    next_checks: list[str],
) -> str:
    return f"""### {theme}

状态：{status}
阶段：{stage}
总分：{total_score}

触发信号：
{render_list(triggers)}

AI 归因：
{attribution}

证据：
{render_list(evidence)}

风险：
{render_list(risks)}

下一步验证：
{render_list(next_checks)}
"""
