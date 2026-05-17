from lurker.domain.models import VisibilityTier


WEIGHTS = {
    "stock_first": {"stock_score": 0.35, "sector_score": 0.35, "ai_score": 0.30},
    "sector_first": {"stock_score": 0.25, "sector_score": 0.45, "ai_score": 0.30},
}


def combine_candidate_scores(
    *,
    stock_score: float,
    sector_score: float,
    ai_score: float,
    trigger_type: str,
) -> float:
    weights = WEIGHTS[trigger_type]
    total = (
        stock_score * weights["stock_score"]
        + sector_score * weights["sector_score"]
        + ai_score * weights["ai_score"]
    )
    return round(total, 1)


def visibility_tier(*, total_score: float, ai_recommendation: str) -> VisibilityTier:
    if total_score >= 75 and ai_recommendation in {"升级", "观察"}:
        return "main"
    if total_score >= 50:
        return "secondary"
    return "archive"
