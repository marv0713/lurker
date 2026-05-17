from lurker.ai.attribution import score_ai_attribution
from lurker.ai.schemas import AIAttributionResult


def test_ai_attribution_schema_accepts_expected_payload():
    result = AIAttributionResult(
        classification="产业趋势型",
        reason_summary="AI 数据中心资本开支上修带动光模块需求。",
        evidence=["新闻", "公告", "财报"],
        risk_flags=["估值高"],
        upgrade_recommendation="升级",
        missing_evidence=["订单是否持续进入财报"],
    )

    assert result.classification == "产业趋势型"
    assert "财报" in result.evidence


def test_score_ai_attribution_rewards_hard_evidence():
    result = AIAttributionResult(
        classification="产业趋势型",
        reason_summary="多家公司订单和财报共同验证需求。",
        evidence=["新闻", "公告", "财报", "订单"],
        risk_flags=["估值高", "客户集中"],
        upgrade_recommendation="升级",
        missing_evidence=["云厂商下一季度资本开支指引"],
    )

    assert score_ai_attribution(result) >= 80
