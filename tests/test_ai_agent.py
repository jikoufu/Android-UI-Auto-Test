from pydantic import BaseModel, ConfigDict

from ai.agent import AIAgent, AITool, ToolRegistry
from models.agent_result import AgentStatus


class DoubleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


class FakeClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.messages: list[list[dict]] = []

    def chat(self, messages, *, tools=None, response_format=None):
        self.messages.append(list(messages))
        return self.responses.pop(0)


def _tool_call(value: int = 4) -> dict:
    return {"choices": [{"message": {"tool_calls": [{
        "id": "call-1",
        "type": "function",
        "function": {"name": "double", "arguments": f'{{"value": {value}}}'},
    }]}}]}


def test_agent_executes_registered_tool_and_returns_final_answer():
    client = FakeClient([
        _tool_call(),
        {"choices": [{"message": {"content": "The result is 8."}}]},
    ])
    registry = ToolRegistry([
        AITool("double", "Double a number.", DoubleArguments, lambda args: args.value * 2),
    ])

    result = AIAgent(client, max_tool_calls=2).run("Double 4", registry)

    assert result.status is AgentStatus.COMPLETED
    assert result.tool_calls == 1
    assert result.message == "The result is 8."
    assert '"result": 8' in client.messages[1][-1]["content"]


def test_agent_stops_before_exceeding_tool_call_limit():
    client = FakeClient([_tool_call(), _tool_call()])
    registry = ToolRegistry([
        AITool("double", "Double a number.", DoubleArguments, lambda args: args.value * 2),
    ])

    result = AIAgent(client, max_tool_calls=1).run("Double 4", registry)

    assert result.status is AgentStatus.BLOCKED
    assert result.tool_calls == 1
    assert "limit" in result.message
