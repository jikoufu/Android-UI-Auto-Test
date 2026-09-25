"""Minimal OpenAI-compatible chat client using Python's standard library."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from utils.config import load_yaml


class ChatClient(Protocol):
    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...


class AIClientError(RuntimeError):
    """Raised for configuration or provider request errors."""


class OpenAICompatibleClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30,
        temperature: float = 0.1,
    ) -> None:
        if not api_key:
            raise ValueError("An API key is required")
        if not model:
            raise ValueError("A model name is required")
        self._api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature

    @classmethod
    def from_environment(cls, config_path: str | Path = "config/ai.yaml") -> "OpenAICompatibleClient":
        config = load_yaml(config_path)
        key_env = str(config.get("api_key_env", "OPENAI_API_KEY"))
        api_key = os.getenv(key_env, "")
        model = os.getenv("OPENAI_MODEL") or str(config.get("model") or "")
        if not api_key:
            raise AIClientError(f"Set the {key_env} environment variable to enable AI calls")
        if not model:
            raise AIClientError("Set OPENAI_MODEL or configure model in config/ai.yaml")
        return cls(
            api_key=api_key,
            model=model,
            base_url=os.getenv("OPENAI_BASE_URL", str(config.get("base_url", "https://api.openai.com/v1"))),
            timeout=float(os.getenv("AI_TIMEOUT", config.get("timeout", 30))),
            temperature=float(config.get("temperature", 0.1)),
        )

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise AIClientError(f"AI provider returned HTTP {exc.code}") from exc
        except (TimeoutError, URLError, json.JSONDecodeError) as exc:
            raise AIClientError(f"AI provider request failed: {type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise AIClientError("AI provider returned an invalid response")
        return payload
