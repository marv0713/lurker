"""tests/test_gemini_attributor.py — GeminiAttributor 单元测试。

使用 unittest.mock 模拟 OpenAI 客户端，不需要真实 API Key。
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from lurker.ai.attributor import (
    GeminiAttributor,
    StubAttributor,
    _build_attribution_result,
    _parse_attribution_response,
)
from lurker.ai.prompts import build_attribution_prompt_from_signal
from lurker.application.signal_scan import StockSignal

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_signal(symbol: str = "NVDA", market: str = "us") -> StockSignal:
    return StockSignal(
        symbol=symbol,
        market=market,
        stock_score=75,
        double_bagger_class="near_double",
        returns={"return_20d": 0.12, "return_60d": 0.35, "return_180d": 0.85},
        percentiles={"return_20d_percentile": 0.92, "return_60d_percentile": 0.88},
    )


def _mock_openai_response(content: dict) -> MagicMock:
    """构造 openai 响应 mock，content 为 dict（会被序列化为 JSON 字符串）。"""
    choice = MagicMock()
    choice.message.content = json.dumps(content, ensure_ascii=False)
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# prompt 构建测试
# ---------------------------------------------------------------------------


def test_build_attribution_prompt_from_signal_contains_symbol():
    signal = _make_signal()
    prompt = build_attribution_prompt_from_signal(
        symbol=signal.symbol,
        market=signal.market,
        returns=signal.returns,
        percentiles=signal.percentiles,
        double_bagger_class=signal.double_bagger_class,
    )
    assert "NVDA" in prompt
    assert "美股" in prompt
    assert "180" in prompt  # return_180d 应该出现


def test_build_attribution_prompt_includes_extra_sources():
    prompt = build_attribution_prompt_from_signal(
        symbol="300308.SZ",
        market="cn",
        returns={"return_60d": 0.40},
        percentiles={},
        double_bagger_class="none",
        extra_sources=["公司发布订单公告，金额超预期。"],
    )
    assert "公司发布订单公告" in prompt


# ---------------------------------------------------------------------------
# JSON 解析测试
# ---------------------------------------------------------------------------


def test_parse_attribution_response_plain_json():
    data = {"classification": "产业趋势型", "reason_summary": "测试归因"}
    raw = json.dumps(data, ensure_ascii=False)
    result = _parse_attribution_response(raw)
    assert result["classification"] == "产业趋势型"


def test_parse_attribution_response_markdown_wrapped():
    data = {"classification": "证据不足型", "reason_summary": "无数据"}
    raw = f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"
    result = _parse_attribution_response(raw)
    assert result["classification"] == "证据不足型"


# ---------------------------------------------------------------------------
# AttributionResult 构建和字段校验测试
# ---------------------------------------------------------------------------


def test_build_attribution_result_invalid_classification_falls_back():
    """非法 classification 应回退到证据不足型。"""
    data = {
        "classification": "胡说八道型",
        "reason_summary": "测试",
        "evidence": [],
        "risk_flags": [],
        "upgrade_recommendation": "观察",
        "missing_evidence": [],
    }
    result = _build_attribution_result(data)
    assert result.classification == "证据不足型"


def test_build_attribution_result_invalid_evidence_filtered():
    """非法 evidence 类型应被过滤掉。"""
    data = {
        "classification": "事件驱动型",
        "reason_summary": "测试",
        "evidence": ["新闻", "不合法来源", "公告"],
        "risk_flags": [],
        "upgrade_recommendation": "观察",
        "missing_evidence": [],
    }
    result = _build_attribution_result(data)
    assert "不合法来源" not in result.evidence
    assert "新闻" in result.evidence
    assert "公告" in result.evidence


# ---------------------------------------------------------------------------
# GeminiAttributor mock 测试
# ---------------------------------------------------------------------------


@patch("lurker.ai.attributor.os.environ.get", return_value="fake-api-key")
@patch("openai.OpenAI")
def test_gemini_attributor_successful_attribution(mock_openai_cls, _mock_env):
    """正常 LLM 响应：应解析出正确的 AttributionResult 和 ai_score。"""
    llm_response_data = {
        "classification": "产业趋势型",
        "reason_summary": "AI 算力需求驱动光模块需求持续增长。",
        "evidence": ["新闻"],
        "risk_flags": ["估值偏高"],
        "upgrade_recommendation": "观察",
        "missing_evidence": ["订单数据", "财报验证"],
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_openai_response(llm_response_data)
    mock_openai_cls.return_value = mock_client

    attributor = GeminiAttributor(api_key="fake-api-key")
    signal = _make_signal()
    result, ai_score = attributor.attribute(signal)

    assert result.classification == "产业趋势型"
    assert result.upgrade_recommendation == "观察"
    assert "估值偏高" in result.risk_flags
    assert ai_score > 0


@patch("lurker.ai.attributor.os.environ.get", return_value="fake-api-key")
@patch("openai.OpenAI")
def test_gemini_attributor_fallback_on_error(mock_openai_cls, _mock_env):
    """LLM 调用失败时，应回退到 StubAttributor，不抛异常。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("network timeout")
    mock_openai_cls.return_value = mock_client

    attributor = GeminiAttributor(api_key="fake-api-key")
    signal = _make_signal()
    result, ai_score = attributor.attribute(signal)

    # 回退到 Stub：证据不足型
    assert result.classification == "证据不足型"
    assert ai_score == StubAttributor.AI_SCORE


def test_gemini_attributor_raises_without_api_key():
    """没有 API Key 时，构造 GeminiAttributor 应抛出 ValueError。"""
    with patch("lurker.ai.attributor.os.environ.get", return_value=""):
        with pytest.raises(ValueError, match="API Key"):
            GeminiAttributor()
