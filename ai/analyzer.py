"""将 pytest 和 Android 证据整理为经过校验的恢复建议。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai.client import ChatClient
from models.ai_result import AIAnalysisResult


class AIAnalyzer:
    def __init__(self, client: ChatClient, prompt_path: str | Path = "ai/prompts/error_analysis.md") -> None:
        """保存模型客户端和失败分析提示词路径。"""
        self.client = client
        self.prompt_path = Path(prompt_path)

    def analyze(self, failure: str, context: dict[str, Any] | None = None) -> AIAnalysisResult:
        """请求模型分析失败信息，并解析为结构化结果。"""
        system_prompt = self.prompt_path.read_text(encoding="utf-8")
        user_context = {"failure": failure, "context": context or {}}
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_context, ensure_ascii=False)},
        ]
        for attempt in range(2):
            response = self.client.chat(messages, response_format={"type": "json_object"})
            content: Any = None
            try:
                content = response["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    raise ValueError("AI response content is not text")
                result = json.loads(content)
                # DeepSeek 偶尔会把响应格式标记复述进内容；只忽略这一已知元数据。
                if isinstance(result, dict) and result.get("type") == "json_object":
                    result.pop("type")
                return AIAnalysisResult.model_validate(result)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                if attempt == 1:
                    raise ValueError("AI provider response did not match AIAnalysisResult") from exc
                messages.extend(
                    [
                        {"role": "assistant", "content": content if isinstance(content, str) else "{}"},
                        {
                            "role": "user",
                            "content": "上一个响应不符合结构要求。请只返回符合系统提示词字段的完整 JSON 对象。",
                        },
                    ]
                )
        raise ValueError("AI provider response did not match AIAnalysisResult")
