"""attributor.py — AI 归因接口定义与内置实现。

定义 Attributor Protocol，让 run_daily 只依赖接口，而不依赖具体 LLM。

内置实现：
  - StubAttributor：直接返回"证据不足型"占位归因，ai_score 固定为 30。
                    用于在没有真实 API Key 时跑通整条链路。
  - GeminiAttributor：通过 Gemini 的 OpenAI-compatible 接口调用 LLM 归因。
                      也可用于任何 OpenAI-compatible 端点（DeepSeek、本地 ollama 等）。

后续可新增：
  - 专门的 OpenAIAttributor、AnthropicAttributor 等，
    只要实现 Attributor Protocol 即可接入 run_daily，无需修改其他模块。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Protocol

from lurker.application.signal_scan import StockSignal
from lurker.domain.attribution import AttributionResult

logger = logging.getLogger(__name__)

# Gemini OpenAI-compatible 接口默认配置
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"

_VALID_CLASSIFICATIONS = {"产业趋势型", "事件驱动型", "题材炒作型", "证据不足型"}
_VALID_RECOMMENDATIONS = {"升级", "降级", "观察", "证据不足"}
_VALID_EVIDENCE = {"新闻", "公告", "财报", "订单", "政策"}


class Attributor(Protocol):
    """AI 归因接口。

    接受一个 StockSignal，返回结构化归因结果和对应的 ai_score（0-100）。
    """

    def attribute(self, signal: StockSignal) -> tuple[AttributionResult, int]:
        """对单只个股做归因。

        Returns:
            (AttributionResult, ai_score)
        """
        ...


class StubAttributor:
    """Stub 实现：返回"证据不足型"占位归因，ai_score 固定为 30。

    用于在无 LLM API Key 时跑通整条链路，验证 pipeline 正确性。
    整条链路流通后，替换为真实 Attributor 实现即可。
    """

    AI_SCORE = 30

    def attribute(self, signal: StockSignal) -> tuple[AttributionResult, int]:
        result = AttributionResult(
            classification="证据不足型",
            reason_summary=f"{signal.symbol} 已触发个股强度信号（分位数超阈值），尚无新闻/公告归因。",
            evidence=[],
            risk_flags=[],
            upgrade_recommendation="证据不足",
            missing_evidence=["新闻摘要", "公告"],
        )
        return result, self.AI_SCORE


def _parse_attribution_response(raw: str) -> dict:
    """从 LLM 返回的文本中提取 JSON。支持 markdown 代码块包裹的情况。"""
    text = raw.strip()
    # 去掉 ```json ... ``` 或 ``` ... ``` 包裹
    if text.startswith("```"):
        lines = text.split("\n")
        # 去掉首尾 ``` 行
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return json.loads(text)


def _build_attribution_result(data: dict) -> AttributionResult:
    """将 LLM 返回的 dict 构造为 AttributionResult，做字段校验和容错。"""
    classification = data.get("classification", "证据不足型")
    if classification not in _VALID_CLASSIFICATIONS:
        classification = "证据不足型"

    recommendation = data.get("upgrade_recommendation", "证据不足")
    if recommendation not in _VALID_RECOMMENDATIONS:
        recommendation = "证据不足"

    raw_evidence = data.get("evidence", [])
    evidence = [e for e in raw_evidence if e in _VALID_EVIDENCE]

    return AttributionResult(
        classification=classification,  # type: ignore[arg-type]
        reason_summary=str(data.get("reason_summary", ""))[:200],
        evidence=evidence,  # type: ignore[arg-type]
        risk_flags=[str(r) for r in data.get("risk_flags", [])],
        upgrade_recommendation=recommendation,  # type: ignore[arg-type]
        missing_evidence=[str(m) for m in data.get("missing_evidence", [])],
    )


def _score_from_result(result: AttributionResult) -> int:
    """从 AttributionResult 计算 ai_score（复用 domain.attribution.score_ai_attribution）。"""
    from lurker.domain.attribution import score_ai_attribution
    return score_ai_attribution(result)


class GeminiAttributor:
    """Gemini LLM 归因实现，使用 OpenAI-compatible 接口。

    也可通过 base_url 连接任何 OpenAI-compatible LLM（DeepSeek、ollama 等）。

    Args:
        api_key: API 密钥。默认从环境变量 GEMINI_API_KEY 读取。
        model: 模型名称，默认 gemini-2.5-flash。
        base_url: API 基础 URL，默认 Gemini OpenAI-compatible 端点。
        temperature: 生成温度，建议归因任务使用低温度（0.2）。
        timeout: 单次请求超时（秒），默认 30。
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = GEMINI_DEFAULT_MODEL,
        base_url: str = GEMINI_BASE_URL,
        temperature: float = 0.2,
        timeout: float = 30.0,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "GeminiAttributor 需要安装 openai 包：pip install openai"
            ) from exc

        resolved_key = api_key or os.environ.get(GEMINI_API_KEY_ENV, "")
        if not resolved_key:
            raise ValueError(
                f"未提供 API Key，请设置环境变量 {GEMINI_API_KEY_ENV} 或通过 api_key 参数传入。"
            )

        self._client = OpenAI(api_key=resolved_key, base_url=base_url)
        self._model = model
        self._temperature = temperature
        self._timeout = timeout

    def attribute(self, signal: StockSignal) -> tuple[AttributionResult, int]:
        """调用 Gemini 对个股做归因。

        失败时回退到 StubAttributor，保证整条 pipeline 不中断。
        """
        from lurker.ai.prompts import (
            ATTRIBUTION_SYSTEM_PROMPT,
            build_attribution_prompt_from_signal,
        )

        user_prompt = build_attribution_prompt_from_signal(
            symbol=signal.symbol,
            market=signal.market,
            returns=signal.returns,
            percentiles=signal.percentiles,
            double_bagger_class=signal.double_bagger_class,
            extra_sources=signal.extra_sources,
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": ATTRIBUTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._temperature,
                timeout=self._timeout,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or ""
            data = _parse_attribution_response(raw)
            result = _build_attribution_result(data)
            ai_score = _score_from_result(result)
            return result, ai_score

        except Exception as exc:
            logger.warning(
                "GeminiAttributor 归因失败，回退 StubAttributor。symbol=%s error=%s",
                signal.symbol,
                exc,
            )
            stub = StubAttributor()
            return stub.attribute(signal)
