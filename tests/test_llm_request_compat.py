import asyncio

import httpx

from server.controller import _chat_completion_request


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {"choices": [{"message": {"content": "ok"}}]}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self):
        self.posts = []

    async def post(self, url, headers=None, json=None):
        self.posts.append(json)
        if len(self.posts) == 1:
            return _FakeResponse(400, text='{"error":"unsupported tool_choice"}')
        return _FakeResponse(200, payload={"status": "ok"})


def test_chat_completion_retries_required_tool_choice_400_with_auto():
    client = _FakeClient()
    result = asyncio.run(
        _chat_completion_request(
            client=client,
            cfg={
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "test",
                "model": "deepseek-chat",
                "max_tokens": 100,
            },
            model="deepseek-chat",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "answer_text", "parameters": {"type": "object"}}}],
            tool_choice="required",
        )
    )

    assert result == {"status": "ok"}
    assert client.posts[0]["tool_choice"] == "required"
    assert client.posts[1]["tool_choice"] == "auto"
