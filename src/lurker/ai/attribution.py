from lurker.ai.schemas import AIAttributionResult
from lurker.domain.attribution import score_ai_attribution as score_domain_attribution


def score_ai_attribution(result: AIAttributionResult) -> int:
    return score_domain_attribution(result)
