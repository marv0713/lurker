from dataclasses import dataclass
from typing import Any, Literal, Mapping, cast


Classification = Literal["产业趋势型", "事件驱动型", "题材炒作型", "证据不足型"]
EvidenceType = Literal["新闻", "公告", "财报", "订单", "政策"]
UpgradeRecommendation = Literal["升级", "降级", "观察", "证据不足"]

HARD_EVIDENCE = {"公告", "财报", "订单", "政策"}
VALID_CLASSIFICATIONS = {
    "产业趋势型",
    "事件驱动型",
    "题材炒作型",
    "证据不足型",
}
VALID_RECOMMENDATIONS = {"升级", "降级", "观察", "证据不足"}
VALID_EVIDENCE = {"新闻", "公告", "财报", "订单", "政策"}


@dataclass(frozen=True)
class AttributionResult:
    classification: Classification
    reason_summary: str
    evidence: list[EvidenceType]
    risk_flags: list[str]
    upgrade_recommendation: UpgradeRecommendation
    missing_evidence: list[str]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def attribution_result_from_mapping(
    data: Mapping[str, Any],
) -> AttributionResult:
    classification_value = data.get("classification", "证据不足型")
    if classification_value not in VALID_CLASSIFICATIONS:
        classification_value = "证据不足型"

    recommendation_value = data.get(
        "upgrade_recommendation",
        "证据不足",
    )
    if recommendation_value not in VALID_RECOMMENDATIONS:
        recommendation_value = "证据不足"

    raw_evidence = data.get("evidence", [])
    evidence = (
        [
            cast(EvidenceType, item)
            for item in raw_evidence
            if item in VALID_EVIDENCE
        ]
        if isinstance(raw_evidence, list)
        else []
    )

    return AttributionResult(
        classification=cast(Classification, classification_value),
        reason_summary=str(data.get("reason_summary", ""))[:200],
        evidence=evidence,
        risk_flags=_string_list(data.get("risk_flags")),
        upgrade_recommendation=cast(
            UpgradeRecommendation,
            recommendation_value,
        ),
        missing_evidence=_string_list(data.get("missing_evidence")),
    )


def score_ai_attribution(result: AttributionResult) -> int:
    score = 0

    if result.reason_summary:
        score += 20
    if result.classification == "产业趋势型":
        score += 20
    if len(result.evidence) >= 2:
        score += 15
    if HARD_EVIDENCE.intersection(result.evidence):
        score += 25
    if result.risk_flags:
        score += 10
    if result.classification != "题材炒作型":
        score += 10

    return score
