from dataclasses import asdict, dataclass
from typing import Literal


TriggerType = Literal["stock_first", "sector_first"]
VisibilityTier = Literal["main", "secondary", "archive"]
AIRecommendation = Literal["升级", "降级", "观察", "证据不足"]


@dataclass(frozen=True)
class CandidateSignal:
    theme: str
    stock_score: float
    sector_score: float
    ai_score: float
    trigger_type: TriggerType
    ai_recommendation: AIRecommendation

    def to_dict(self) -> dict:
        return asdict(self)
