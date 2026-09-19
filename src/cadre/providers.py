"""LLM providers — the only code that knows how to talk to a model (ADR-001).

One adapter speaks the OpenAI-compatible ``/chat/completions`` shape, which every free provider
worth adding uses in 2026. A provider object is one *endpoint*; the model is chosen per call,
because the router moves work between models, not between endpoints.

Retrying is deliberately **not** done here. A free tier's normal failure is a rate limit, and
the right response — wait, or move to another model — needs knowledge only the router has. So
this layer classifies failures precisely and raises; the router decides.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .secrets import REDACTOR
from .types import ChatResponse, Message, RateInfo, ToolCall, ToolSpec, Usage

# --------------------------------------------------------------------------- failures


class ProviderError(RuntimeError):
    """Base class. `str(e)` is always safe to show and store (keys are redacted)."""

    def __init__(self, message: str):
        super().__init__(REDACTOR.redact(message))


class RateLimited(ProviderError):
    def __init__(self, message: str, retry_after: float | None = None, daily: bool = False,
                 limit: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        #: the provider says a *daily* quota is spent (Gemini: quotaId "...PerDay...")
        self.daily = daily
        #: the quota value the provider reported, when it did
        self.limit = limit


class AuthFailed(ProviderError):
    """401/403 — the key is wrong, revoked, or the account cannot use this model."""


class Unavailable(ProviderError):
    """5xx, timeouts, connection errors: worth another try somewhere."""


class RequestTooLarge(ProviderError):
    """This model will never accept this request (context or per-minute size)."""


class ToolsUnsupported(ProviderError):
    """The endpoint rejected function calling; the JSON tool protocol will work instead."""


class ModelGone(ProviderError):
    """404: this model does not exist for this key (retired, or closed to new users)."""


class MalformedOutput(ProviderError):
    """The model produced a tool call the provider could not parse (Groq: tool_use_failed)."""


class BadRequest(ProviderError):
    """Any other 4xx: a real answer, not worth retrying on this model."""


# --------------------------------------------------------------------------- rate headers

_DUR = re.compile(r"([\d.]+)\s*(ms|h|m|s)")


def parse_duration(text: str | None) -> float | None:
    """'2m59.56s' -> 179.56, '250ms' -> 0.25, '7' -> 7.0. None when unparseable."""
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    total, matched = 0.0, False
    for num, unit in _DUR.findall(text):
        matched = True
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if matched else None


def _int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v is not None else None
    except ValueError:
        return None


def parse_rate_headers(headers: httpx.Headers, now: float | None = None) -> RateInfo:
    """Groq/OpenAI style (`x-ratelimit-*-requests|tokens`) and OpenRouter style
    (`X-RateLimit-Remaining` + `X-RateLimit-Reset` as epoch milliseconds)."""
    now = time.time() if now is None else now
    info = RateInfo(
        remaining_requests=_int(headers.get("x-ratelimit-remaining-requests")),
        remaining_tokens=_int(headers.get("x-ratelimit-remaining-tokens")),
        reset_requests_s=parse_duration(headers.get("x-ratelimit-reset-requests")),
        reset_tokens_s=parse_duration(headers.get("x-ratelimit-reset-tokens")),
        limit_requests=_int(headers.get("x-ratelimit-limit-requests")),
        limit_tokens=_int(headers.get("x-ratelimit-limit-tokens")),
    )
    if info.remaining_requests is None and headers.get("x-ratelimit-remaining") is not None:
        info.remaining_requests = _int(headers.get("x-ratelimit-remaining"))
        info.limit_requests = _int(headers.get("x-ratelimit-limit"))
        reset = _int(headers.get("x-ratelimit-reset"))
        if reset is not None:
            # epoch ms (OpenRouter), epoch s, or already a delta
            if reset > 10**12:
                info.reset_requests_s = max(0.0, reset / 1000 - now)
            elif reset > 10**9:
                info.reset_requests_s = max(0.0, reset - now)
            else:
                info.reset_requests_s = float(reset)
    return info


_TRY_AGAIN = re.compile(r"(?:try again in|retry in|retryDelay\"?\s*:\s*\"?)\s*([\d.]+\s*(?:ms|s|m)?[\d.ms]*)", re.I)


def retry_after(r: httpx.Response) -> float | None:
    header = r.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(header).timestamp() - time.time())
            except (TypeError, ValueError):
                pass
    m = _TRY_AGAIN.search(r.text or "")
    if m:
        return parse_duration(m.group(1))
    return None


def _err_text(r: httpx.Response) -> str:
    try:
        data = r.json()
    except ValueError:
        return (r.text or "")[:300]
    err = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, list) and err:
        err = err[0]
    if isinstance(err, dict):
        err = err.get("message") or err.get("error") or json.dumps(err)[:600]
    return str(err)[:600]


_QUOTA_ID = re.compile(r'"quotaId"\s*:\s*"([^"]+)"')
_QUOTA_VALUE = re.compile(r'"quotaValue"\s*:\s*"?(\d+)')


def classify(r: httpx.Response, label: str) -> ProviderError:
    msg = f"{label} {r.status_code}: {_err_text(r)}"
    low = (r.text or "").lower()
    if r.status_code == 429:
        if "request too large" in low or "reduce your message size" in low:
            return RequestTooLarge(msg)
        # Google reports which quota was hit (live, 2026-09-17: "You exceeded your current quota")
        ids = _QUOTA_ID.findall(r.text or "")
        daily = any("perday" in q.lower() for q in ids) or "requests per day" in low
        value = _QUOTA_VALUE.search(r.text or "")
        if ids:
            msg += f" [quota {', '.join(dict.fromkeys(ids))}" + (f" = {value.group(1)}]" if value else "]")
        return RateLimited(msg, retry_after(r), daily=daily,
                           limit=int(value.group(1)) if value and daily else None)
    if r.status_code in (401, 403):
        return AuthFailed(msg)
    if r.status_code == 400 and ("api_key_invalid" in low or "valid api key" in low
                                 or "api key not valid" in low):
        # Google AI Studio answers a bad key with 400 INVALID_ARGUMENT (live in CI, 2026-09-19)
        return AuthFailed(msg)
    if r.status_code == 413 or "context length" in low or "context_length" in low \
            or "maximum context" in low or "too many tokens" in low:
        return RequestTooLarge(msg)
    if r.status_code == 400 and "tool_use_failed" in low:
        return MalformedOutput(msg)
    if r.status_code in (400, 404, 422) and "tool" in low and (
            "support" in low or "not enabled" in low or "unavailable" in low):
        return ToolsUnsupported(msg)
    if r.status_code == 404:
        return ModelGone(msg)
    if r.status_code >= 500 or r.status_code == 408:
        return Unavailable(msg)
    return BadRequest(msg)


# --------------------------------------------------------------------------- JSON tool protocol
# Endpoints and small models without function calling still need to act. The fallback is one
# strict JSON object per reply, described in the system prompt.

JSON_TOOL_INSTRUCTIONS = """\
## How to use tools
To call a tool, reply with ONLY this JSON object and nothing else:
{"tool": "<tool name>", "arguments": {...}}
When you are finished, reply with ONLY:
{"answer": "<your final answer>"}
Never write text outside the JSON object. Never invent tool names.
Tools:
"""


def tools_prompt(tools: list[ToolSpec]) -> str:
    lines = [JSON_TOOL_INSTRUCTIONS]
    for t in tools:
        props = t.parameters.get("properties", {})
        args = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in props.items())
        lines.append(f"- {t.name}({args}) — {t.description}")
    return "\n".join(lines)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_thinking(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def extract_json(text: str) -> Any:
    """The first JSON object or array in a reply, tolerating fences and prose. Raises ValueError."""
    text = strip_thinking(text)
    candidates = [m.group(1).strip() for m in _FENCE.finditer(text)] + [text.strip()]
    decoder = json.JSONDecoder()
    for cand in candidates:
        for i, ch in enumerate(cand):
            if ch in "{[":
                try:
                    obj, _ = decoder.raw_decode(cand[i:])
                    return obj
                except ValueError:
                    continue
    raise ValueError("no JSON object found in the reply")


def parse_json_reply(text: str) -> tuple[str, list[ToolCall]]:
    try:
        obj = extract_json(text)
    except ValueError:
        return strip_thinking(text), []
    if isinstance(obj, dict) and obj.get("tool"):
        args = obj.get("arguments") or obj.get("args") or {}
        if not isinstance(args, dict):
            args = {"value": args}
        return "", [ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=str(obj["tool"]),
                             arguments=args)]
    if isinstance(obj, dict) and "answer" in obj:
        ans = obj["answer"]
        return (ans if isinstance(ans, str) else json.dumps(ans)), []
    return strip_thinking(text), []


def to_json_protocol(messages: list[Message], tools: list[ToolSpec]) -> list[Message]:
    """Rewrite a native tool conversation into plain turns the JSON protocol understands."""
    out: list[Message] = []
    guide = tools_prompt(tools)
    placed = False
    for m in messages:
        if m.role == "system" and not placed:
            out.append(Message(role="system", content=f"{m.content}\n\n{guide}"))
            placed = True
        elif m.role == "assistant" and m.tool_calls:
            tc = m.tool_calls[0]
            out.append(Message(role="assistant",
                               content=json.dumps({"tool": tc.name, "arguments": tc.arguments})))
        elif m.role == "tool":
            out.append(Message(role="user", content=f"Result of {m.name or 'the tool'}:\n{m.content}"))
        else:
            out.append(m)
    if not placed:
        out.insert(0, Message(role="system", content=guide))
    return out


def estimate_tokens(messages: list[Message], tools: list[ToolSpec] | None = None) -> int:
    """~4 characters a token. Deliberately rough; the limiter settles against real usage."""
    chars = sum(len(m.content) + 16 + sum(len(json.dumps(t.arguments)) for t in m.tool_calls)
                for m in messages)
    chars += sum(len(json.dumps(t.model_dump())) for t in tools or [])
    return chars // 4 + 1


# --------------------------------------------------------------------------- base

class LLMProvider(ABC):
    def __init__(self, id: str, label: str = "", local: bool = False):
        self.id = id
        self.label = label or id
        self.local = local

    @abstractmethod
    async def chat(self, model: str, messages: list[Message], tools: list[ToolSpec] | None = None,
                   *, protocol: str = "native", max_tokens: int = 1024,
                   temperature: float = 0.3) -> ChatResponse: ...

    async def health(self) -> tuple[bool, str]:
        return True, "assumed reachable"

    async def list_models(self) -> list[str]:
        return []

    async def aclose(self) -> None:
        return None


# --------------------------------------------------------------------------- OpenAI-compatible

class OpenAICompatProvider(LLMProvider):
    def __init__(self, id: str, base_url: str, api_key: str | None, *, label: str = "",
                 local: bool = False, timeout: float = 120.0,
                 transport: httpx.AsyncBaseTransport | None = None,
                 extra_headers: dict[str, str] | None = None,
                 extra_body: dict[str, Any] | None = None,
                 unsigned_tool_call_extra: dict[str, Any] | None = None):
        super().__init__(id, label, local)
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        REDACTOR.register(api_key)
        self._timeout = timeout
        self._transport = transport
        self._extra = extra_headers or {}
        #: provider-specific request fields, e.g. Gemini's reasoning_effort (M5, 2026-09-17)
        self._extra_body = dict(extra_body or {})
        #: what to attach to a replayed tool call this provider did not issue (Gemini's documented
        #: last-resort placeholder signature); None: attach nothing
        self._unsigned_extra = unsigned_tool_call_extra
        self._client: httpx.AsyncClient | None = None

    @property
    def has_key(self) -> bool:
        return bool(self._api_key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"content-type": "application/json", **self._extra}
            if self._api_key:
                headers["authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=10.0), headers=headers,
                transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def chat(self, model, messages, tools=None, *, protocol="native", max_tokens=1024,
                   temperature=0.3) -> ChatResponse:
        tools = tools or []
        native = protocol == "native" and bool(tools)
        msgs = messages if native or not tools else to_json_protocol(messages, tools)
        body: dict[str, Any] = {
            "model": model,
            "messages": [_wire(m, self.id, self._unsigned_extra) for m in msgs],
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self._extra_body,
        }
        if native:
            body["tools"] = [{"type": "function", "function": t.model_dump()} for t in tools]
        try:
            r = await self._http().post(f"{self.base_url}/chat/completions", json=body)
        except httpx.TimeoutException as e:
            raise Unavailable(f"{self.label}: timed out ({type(e).__name__})") from None
        except httpx.HTTPError as e:
            raise Unavailable(f"{self.label}: unreachable ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise classify(r, self.label)
        try:
            data = r.json()
        except ValueError:
            raise Unavailable(f"{self.label}: non-JSON reply") from None
        if isinstance(data, dict) and data.get("error"):  # some hosts return 200 with an error
            raise Unavailable(f"{self.label}: {str(data['error'])[:300]}")
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        if isinstance(content, list):  # content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        calls: list[ToolCall] = []
        if native:
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                extra = tc.get("extra_content") if isinstance(tc.get("extra_content"), dict) else {}
                calls.append(ToolCall(id=tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                                      name=fn.get("name", ""), arguments=_as_dict(fn.get("arguments")),
                                      extra=extra, origin=self.id if extra else ""))
            content = strip_thinking(content)
        elif tools:
            content, calls = parse_json_reply(content)
        else:
            content = strip_thinking(content)
        u = data.get("usage") or {}
        usage = Usage(prompt_tokens=int(u.get("prompt_tokens") or 0),
                      completion_tokens=int(u.get("completion_tokens") or 0))
        if usage.total == 0:
            usage = Usage(prompt_tokens=estimate_tokens(msgs, tools if native else None),
                          completion_tokens=len(content) // 4 + 1)
        rate = parse_rate_headers(r.headers)
        return ChatResponse(content=content, tool_calls=calls, usage=usage,
                            model=str(data.get("model") or model),
                            finish_reason=str(choice.get("finish_reason") or ""),
                            rate=None if rate.empty() else rate)

    async def health(self) -> tuple[bool, str]:
        if not self._api_key and not self.local:
            return False, "no API key"
        try:
            r = await self._http().get(f"{self.base_url}/models", timeout=15.0)
        except httpx.HTTPError as e:
            return False, f"unreachable ({type(e).__name__})"
        if r.status_code in (401, 403) or isinstance(classify(r, "key check"), AuthFailed):
            return False, REDACTOR.redact(f"key rejected — {r.status_code}: {_err_text(r)}")
        if r.status_code >= 400:
            return True, f"/models answered {r.status_code}; chat may still work"
        try:
            n = len(r.json().get("data") or [])
        except (ValueError, AttributeError):
            n = 0
        return True, f"reachable, key accepted, {n} model(s) listed"

    async def list_models(self) -> list[str]:
        r = await self._http().get(f"{self.base_url}/models", timeout=20.0)
        if r.status_code >= 400:
            raise classify(r, self.label)
        data = r.json()
        items = data.get("data") if isinstance(data, dict) else data
        # Google's compatibility layer lists ids as "models/<id>" but accepts the bare id in chat
        return sorted({str(m.get("id")).removeprefix("models/") for m in items or []
                       if isinstance(m, dict) and m.get("id")})


def _as_dict(v: Any) -> dict[str, Any]:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            obj = json.loads(v)
            return obj if isinstance(obj, dict) else {"value": obj}
        except ValueError:
            return {"_raw": v}
    return {}


def _wire(m: Message, provider_id: str = "",
          unsigned_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id or "", "content": m.content}
    d: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        calls = []
        for t in m.tool_calls:
            c: dict[str, Any] = {"id": t.id, "type": "function",
                                 "function": {"name": t.name, "arguments": json.dumps(t.arguments)}}
            if t.extra and t.origin == provider_id:
                c["extra_content"] = t.extra          # replay the issuer's signature unchanged
            elif unsigned_extra:
                c["extra_content"] = unsigned_extra   # a call another model made
            calls.append(c)
        d["tool_calls"] = calls
        if not m.content:
            d["content"] = None
    return d


# --------------------------------------------------------------------------- scripted

Script = Callable[[str, list[Message], list[ToolSpec]], "ChatResponse | str | Awaitable[ChatResponse | str]"]


class ScriptedProvider(LLMProvider):
    """A provider whose replies come from Python — for tests and the offline demo.

    `script` is either a list (replies in order) or a function of (model, messages, tools).
    A reply may be a string, a ChatResponse, or an exception instance to raise.
    Every call is recorded in `calls`.
    """

    def __init__(self, id: str, script: Script | list[Any], *, label: str = "",
                 local: bool = True, models: list[str] | None = None):
        super().__init__(id, label or id, local)
        self._script = script
        self._models = models or []
        self.calls: list[dict[str, Any]] = []

    async def chat(self, model, messages, tools=None, *, protocol="native", max_tokens=1024,
                   temperature=0.3) -> ChatResponse:
        tools = tools or []
        self.calls.append({"model": model, "messages": messages, "tools": tools,
                           "protocol": protocol})
        if isinstance(self._script, list):
            if not self._script:
                raise AssertionError(f"scripted provider {self.id} ran out of replies")
            reply = self._script.pop(0)
        else:
            reply = self._script(model, messages, tools)
            if hasattr(reply, "__await__"):
                reply = await reply
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, str):
            # a JSON tool call in a scripted string is honoured under either protocol
            content, calls = (parse_json_reply(reply) if tools else (reply, []))
            reply = ChatResponse(content=content, tool_calls=calls)
        if reply.usage.total == 0:
            reply.usage = Usage(prompt_tokens=estimate_tokens(messages, tools),
                                completion_tokens=len(reply.content) // 4 + 1)
        reply.model = reply.model or model
        return reply

    async def list_models(self) -> list[str]:
        return list(self._models)
