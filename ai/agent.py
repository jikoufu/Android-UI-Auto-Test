"""用于测试分析的有限次数 AI 工具调用流程。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Type

from pydantic import BaseModel, ValidationError

from ai.client import ChatClient
from models.agent_result import AgentRunResult, AgentStatus


def _json_value(value: Any) -> Any:
    """递归转换工具结果，确保字典和序列以 JSON 结构返回。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass
class AITool:
    name: str
    description: str
    arguments_model: Type[BaseModel]
    handler: Callable[[BaseModel], Any]

    def definition(self) -> dict[str, Any]:
        """生成供 LLM 识别的函数工具定义。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments_model.model_json_schema(),
            },
        }

    def execute(self, arguments: str) -> str:
        """校验参数并执行工具，将结果编码为 JSON。"""
        try:
            parsed = self.arguments_model.model_validate_json(arguments)
            result = _json_value(self.handler(parsed))
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        except (ValidationError, ValueError, RuntimeError) as exc:
            return json.dumps({"ok": False, "error": str(exc)[:500]}, ensure_ascii=False)
        except Exception as exc:  # 单个设备工具失败时保护 Agent 主循环。
            return json.dumps({"ok": False, "error": f"Tool failed: {type(exc).__name__}"}, ensure_ascii=False)


class ToolRegistry:
    def __init__(self, tools: list[AITool] | None = None) -> None:
        """创建注册表，并载入初始工具。"""
        self._tools: dict[str, AITool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: AITool) -> None:
        """注册工具；重名时拒绝覆盖已有定义。"""
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def definitions(self) -> list[dict[str, Any]]:
        """返回全部工具的 LLM 函数定义。"""
        return [tool.definition() for tool in self._tools.values()]

    def execute(self, name: str, arguments: str) -> str:
        """按名称执行工具，未知名称以 JSON 错误返回。"""
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"ok": False, "error": f"Unknown tool: {name}"})
        return tool.execute(arguments)


class AIAgent:
    def __init__(self, client: ChatClient, max_tool_calls: int = 5) -> None:
        """初始化模型客户端和单次运行的工具调用上限。"""
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        self.client = client
        self.max_tool_calls = max_tool_calls

    def run(self, prompt: str, tools: ToolRegistry, system_prompt: str | None = None) -> AgentRunResult:
        """运行受调用次数限制的对话与工具执行循环。"""
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        calls_used = 0

        while calls_used <= self.max_tool_calls:
            try:
                response = self.client.chat(messages, tools=tools.definitions())
            except Exception as exc:
                return AgentRunResult(
                    status=AgentStatus.FAILED,
                    message=f"AI request failed: {type(exc).__name__}",
                    tool_calls=calls_used,
                )
            choices = response.get("choices")
            if not isinstance(choices, list) or not choices:
                return AgentRunResult(status=AgentStatus.FAILED, message="AI response contained no choices", tool_calls=calls_used)
            message = choices[0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    return AgentRunResult(status=AgentStatus.FAILED, message="AI response was empty", tool_calls=calls_used)
                return AgentRunResult(status=AgentStatus.COMPLETED, message=content, tool_calls=calls_used)
            if calls_used + len(tool_calls) > self.max_tool_calls:
                return AgentRunResult(
                    status=AgentStatus.BLOCKED,
                    message="Tool-call limit reached; human review is required before continuing.",
                    tool_calls=calls_used,
                )

            messages.append(message)
            for call in tool_calls:
                function = call.get("function") or {}
                result = tools.execute(str(function.get("name", "")), str(function.get("arguments", "{}")))
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id", "")),
                    "content": result,
                })
                calls_used += 1

        return AgentRunResult(
            status=AgentStatus.BLOCKED,
            message="Agent iteration limit reached; human review is required.",
            tool_calls=calls_used,
        )
