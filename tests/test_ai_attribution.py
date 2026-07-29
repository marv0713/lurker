from lurker.domain.attribution import (
    AttributionResult,
    attribution_result_from_mapping,
    score_ai_attribution,
)


def test_attribution_result_from_mapping_accepts_expected_payload():
    result = attribution_result_from_mapping(
        {
            "classification": "产业趋势型",
            "reason_summary": "AI 数据中心资本开支上修带动光模块需求。",
            "evidence": ["新闻", "公告", "伪证据"],
            "risk_flags": ["估值高"],
            "upgrade_recommendation": "升级",
            "missing_evidence": ["订单是否持续进入财报"],
        }
    )

    assert result.classification == "产业趋势型"
    assert result.evidence == ["新闻", "公告"]


def test_attribution_result_from_mapping_downgrades_unknown_enums():
    result = attribution_result_from_mapping(
        {
            "classification": "未知",
            "upgrade_recommendation": "未知",
        }
    )

    assert result.classification == "证据不足型"
    assert result.upgrade_recommendation == "证据不足"
    assert result.reason_summary == ""
    assert result.evidence == []


def test_attribution_result_from_mapping_tolerates_malformed_json_types():
    result = attribution_result_from_mapping(
        {
            "classification": ["产业趋势型"],
            "upgrade_recommendation": {"value": "升级"},
            "evidence": ["公告", ["财报"], {"value": "订单"}],
            "reason_summary": "保留可用字段",
        }
    )

    assert result.classification == "证据不足型"
    assert result.upgrade_recommendation == "证据不足"
    assert result.evidence == ["公告"]
    assert result.reason_summary == "保留可用字段"


def test_score_ai_attribution_rewards_hard_evidence():
    result = AttributionResult(
        classification="产业趋势型",
        reason_summary="多家公司订单和财报共同验证需求。",
        evidence=["新闻", "公告", "财报", "订单"],
        risk_flags=["估值高", "客户集中"],
        upgrade_recommendation="升级",
        missing_evidence=["云厂商下一季度资本开支指引"],
    )

    assert score_ai_attribution(result) >= 80
