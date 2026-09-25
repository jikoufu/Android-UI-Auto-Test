"""验证 AI 请求体使用配置的低延迟参数。"""

import json

from ai.client import OpenAICompatibleClient


def test_deepseek_recovery_request_disables_thinking(monkeypatch):
    """验证 DeepSeek 恢复请求关闭思考并限制输出长度。"""
    requests = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"choices":[]}'

    # Step 1：从项目配置创建客户端，并拦截请求以检查实际 JSON 请求体。
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setattr("ai.client.urlopen", lambda request, timeout: (requests.append(request) or FakeResponse()))
    client = OpenAICompatibleClient.from_environment()
    client.chat([{"role": "user", "content": "state"}], response_format={"type": "json_object"})

    # Step 2：确认恢复请求发送禁用 thinking 与输出 token 上限。
    body = json.loads(requests[0].data)
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 800
    assert body["response_format"] == {"type": "json_object"}
