import json

import httpx
import pytest

from cadre.providers import (
    AuthFailed,
    MalformedOutput,
    OpenAICompatProvider,
    RateLimited,
    RequestTooLarge,
    ToolsUnsupported,
    Unavailable,
    classify,
    extract_json,
    parse_duration,
    parse_json_reply,
    parse_rate_headers,
    to_json_protocol,
)
from cadre.types import Message, ToolCall, ToolSpec

TOOL = ToolSpec(name="read_file", description="read", parameters={
    "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})


@pytest.mark.parametrize("text,seconds", [
    ("2m59.56s", 179.56), ("7.66s", 7.66), ("250ms", 0.25), ("1h", 3600), ("12", 12.0), ("", None)])
def test_parse_duration(text, seconds):
    assert parse_duration(text) == (pytest.approx(seconds) if seconds is not None else None)


def test_rate_headers_groq_and_openrouter_styles():
    groq = parse_rate_headers(httpx.Headers({
        "x-ratelimit-remaining-tokens": "5120", "x-ratelimit-reset-tokens": "22.5s",
        "x-ratelimit-remaining-requests": "998", "x-ratelimit-reset-requests": "2m52s",
        "x-ratelimit-limit-tokens": "8000"}))
    assert (groq.remaining_tokens, groq.reset_tokens_s, groq.limit_tokens) == (5120, 22.5, 8000)
    assert groq.reset_requests_s == 172
    orr = parse_rate_headers(httpx.Headers({
        "X-RateLimit-Limit": "20", "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(int((1_758_000_000 + 30) * 1000))}), now=1_758_000_000)
    assert (orr.remaining_requests, orr.limit_requests) == (0, 20)
    assert orr.reset_requests_s == pytest.approx(30)


def _resp(status, body, headers=None):
    return httpx.Response(status, json=body, headers=headers or {},
                          request=httpx.Request("POST", "https://x/chat/completions"))


def test_classify_failures():
    groq_429 = _resp(429, {"error": {"message": "Rate limit reached. Please try again in 3.93s."}})
    e = classify(groq_429, "groq")
    assert isinstance(e, RateLimited) and e.retry_after == pytest.approx(3.93)
    assert isinstance(classify(_resp(429, {"error": "x"}, {"retry-after": "12"}), "p"), RateLimited)
    assert isinstance(classify(_resp(401, {"error": {"message": "bad key"}}), "p"), AuthFailed)
    assert isinstance(classify(_resp(413, {"error": {"message": "Request too large"}}), "p"), RequestTooLarge)
    assert isinstance(classify(_resp(400, {"error": {"code": "tool_use_failed"}}), "p"), MalformedOutput)
    assert isinstance(classify(_resp(400, {"error": {"message": "This model does not support tools"}}), "p"),
                      ToolsUnsupported)
    assert isinstance(classify(_resp(503, {"error": "overloaded"}), "p"), Unavailable)


async def test_native_tool_call_usage_and_rate_are_parsed():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, headers={"x-ratelimit-remaining-tokens": "100",
                                            "x-ratelimit-reset-tokens": "5s"}, json={
            "model": "m1", "choices": [{"finish_reason": "tool_calls", "message": {
                "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}]}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 7}})

    p = OpenAICompatProvider("groq", "https://api.example/v1", "sk-test-1234567890",
                             transport=httpx.MockTransport(handler))
    r = await p.chat("m1", [Message(role="system", content="s"), Message(role="user", content="u")], [TOOL])
    await p.aclose()
    assert r.tool_calls == [ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})]
    assert (r.usage.prompt_tokens, r.usage.completion_tokens) == (40, 7)
    assert r.rate.remaining_tokens == 100
    assert seen["body"]["tools"][0]["function"]["name"] == "read_file"
    assert seen["auth"] == "Bearer sk-test-1234567890"


async def test_json_protocol_for_models_without_function_calling():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "tools" not in body
        assert "How to use tools" in body["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "Sure.\n```json\n{\"tool\": \"read_file\", \"arguments\": {\"path\": \"x\"}}\n```"}}]})

    p = OpenAICompatProvider("or", "https://api.example/v1", "k" * 12, transport=httpx.MockTransport(handler))
    r = await p.chat("m", [Message(role="system", content="s"), Message(role="user", content="u")], [TOOL],
                     protocol="json")
    await p.aclose()
    assert r.tool_calls[0].name == "read_file" and r.tool_calls[0].arguments == {"path": "x"}
    assert r.usage.total > 0  # estimated when the host reports none


async def test_errors_never_contain_the_key():
    key = "sk-secret-abcdef123456"

    def handler(request):
        return httpx.Response(401, json={"error": {"message": f"Invalid API Key {key}"}})

    p = OpenAICompatProvider("x", "https://api.example/v1", key, transport=httpx.MockTransport(handler))
    with pytest.raises(AuthFailed) as info:
        await p.chat("m", [Message(role="user", content="hi")])
    await p.aclose()
    assert key not in str(info.value) and "***" in str(info.value)


def test_extract_json_and_replies():
    assert extract_json('noise {"a": 1} tail') == {"a": 1}
    assert extract_json("<think>{no}</think>```json\n{\"b\": [1]}\n```") == {"b": [1]}
    with pytest.raises(ValueError):
        extract_json("no json here")
    assert parse_json_reply('{"answer": "done"}') == ("done", [])
    assert parse_json_reply("plain prose")[0] == "plain prose"


def test_to_json_protocol_rewrites_tool_turns():
    msgs = [Message(role="system", content="sys"), Message(role="user", content="u"),
            Message(role="assistant", tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "a"})]),
            Message(role="tool", tool_call_id="1", name="read_file", content="data")]
    out = to_json_protocol(msgs, [TOOL])
    assert [m.role for m in out] == ["system", "user", "assistant", "user"]
    assert json.loads(out[2].content) == {"tool": "read_file", "arguments": {"path": "a"}}
    assert "Result of read_file" in out[3].content
