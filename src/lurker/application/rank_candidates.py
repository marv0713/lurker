from lurker.domain.models import CandidateSignal
from lurker.domain.policies import combine_candidate_scores, visibility_tier


def coerce_candidate(candidate: CandidateSignal | dict) -> dict:
    if isinstance(candidate, CandidateSignal):
        return candidate.to_dict()
    return candidate.copy()


def rank_candidates(
    candidates: list[CandidateSignal | dict],
    main_limit: int = 10,
) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {"main": [], "secondary": [], "archive": []}

    for candidate in candidates:
        enriched = coerce_candidate(candidate)
        total_score = combine_candidate_scores(
            stock_score=enriched["stock_score"],
            sector_score=enriched["sector_score"],
            ai_score=enriched["ai_score"],
            trigger_type=enriched["trigger_type"],
        )
        enriched["total_score"] = total_score
        tier = visibility_tier(
            total_score=total_score,
            ai_recommendation=enriched["ai_recommendation"],
        )
        enriched["visibility_tier"] = tier
        buckets[tier].append(enriched)

    for bucket in buckets.values():
        bucket.sort(key=lambda item: item["total_score"], reverse=True)

    if len(buckets["main"]) > main_limit:
        overflow = buckets["main"][main_limit:]
        buckets["main"] = buckets["main"][:main_limit]
        buckets["secondary"].extend(overflow)
        buckets["secondary"].sort(key=lambda item: item["total_score"], reverse=True)

    return buckets
