"""Turn pytest and Android evidence into a validated recovery suggestion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai.client import ChatClient
from models.ai_result import AIAnalysisResult


class AIAnalyzer:
    def __init__(self, client: ChatClient, prompt_path: str | Path = "ai/prompts/error_analysis.md") -> None:
        self.client = client
        self.prompt_path = Path(prompt_path)

    def analyze(self, failure: str, context: dict[str, Any] | None = None) -> AIAnalysisResult:
        system_prompt = self.prompt_path.read_text(encoding="utf-8")
        user_context = {"failure": failure, "context": context or {}}
        response = self.client.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_context, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        try:
            content = response["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("AI response content is not text")
            return AIAnalysisResult.model_validate_json(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("AI provider response did not match AIAnalysisResult") from exc
