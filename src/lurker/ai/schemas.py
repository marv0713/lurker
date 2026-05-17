from typing import Literal

from pydantic import BaseModel, Field


class AIAttributionResult(BaseModel):
    classification: Literal["产业趋势型", "事件驱动型", "题材炒作型", "证据不足型"]
    reason_summary: str = Field(min_length=1)
    evidence: list[Literal["新闻", "公告", "财报", "订单", "政策"]]
    risk_flags: list[str]
    upgrade_recommendation: Literal["升级", "降级", "观察", "证据不足"]
    missing_evidence: list[str]
