from types import SimpleNamespace

import pytest

from ai.analyzer import AIAnalyzer
from models.ai_result import RecoveryAction


def test_analyzer_retries_once_when_provider_returns_invalid_json_shape():
    """验证 DeepSeek 返回无效结构时会请求一次格式纠正。"""
    responses = [
        {"choices": [{"message": {"content": "{}"}}]},
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"error_type":"ui_failure","reason":"visible target missing",'
                            '"suggested_action":"scroll","confidence":0.6,'
                            '"scroll_direction":"down"}'
                        )
                    }
                }
            ]
        },
    ]
    calls = []

    def chat(messages, *, response_format):
        calls.append(messages)
        return responses.pop(0)

    # Step 1：输入失败现场并确认格式不合格后会重问一次且解析结构化动作。
    analysis = AIAnalyzer(client=SimpleNamespace(chat=chat)).analyze(
        failure="UI target was missing",
        context={"goal": "Settings → Developer options"},
    )

    assert analysis.suggested_action is RecoveryAction.SCROLL
    assert analysis.scroll_direction == "down"
    assert len(calls) == 2
    assert "只返回符合系统提示词字段的完整 JSON" in calls[1][-1]["content"]


def test_analyzer_stops_after_second_invalid_response():
    """验证连续两次结构错误时清晰停止分析。"""
    calls = 0

    def chat(_messages, *, response_format):
        nonlocal calls
        calls += 1
        return {"choices": [{"message": {"content": "{}"}}]}

    # Step 1：模拟 Provider 连续返回无效 JSON 并确认超过一次重试后停止。
    analyzer = AIAnalyzer(client=SimpleNamespace(chat=chat))

    with pytest.raises(ValueError, match="did not match AIAnalysisResult"):
        analyzer.analyze(failure="UI target was missing")

    assert calls == 2
