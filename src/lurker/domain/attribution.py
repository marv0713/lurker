from dataclasses import dataclass
from typing import Literal


Classification = Literal["产业趋势型", "事件驱动型", "题材炒作型", "证据不足型"]
EvidenceType = Literal["新闻", "公告", "财报", "订单", "政策"]
UpgradeRecommendation = Literal["升级", "降级", "观察", "证据不足"]

HARD_EVIDENCE = {"公告", "财报", "订单", "政策"}


@dataclass(frozen=True)
class AttributionResult:
    classification: Classification
    reason_summary: str
    evidence: list[EvidenceType]
    risk_flags: list[str]
    upgrade_recommendation: UpgradeRecommendation
    missing_evidence: list[str]


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
