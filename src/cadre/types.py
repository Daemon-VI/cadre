"""The vocabulary every layer shares: messages, tool calls, usage, rate-limit readings."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    #: provider-specific data that must be sent back unchanged (Gemini 3's thought signature
    #: arrives as tool_calls[].extra_content), and the provider id that issued it
    extra: dict[str, Any] = Field(default_factory=dict)
    origin: str = ""


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class RateInfo(BaseModel):
    """What a provider said about its own limits on one response.

    Seconds are relative to when the response arrived; the limiter converts them to its clock.
    """

    remaining_requests: int | None = None
    remaining_tokens: int | None = None
    reset_requests_s: float | None = None
    reset_tokens_s: float | None = None
    limit_requests: int | None = None
    limit_tokens: int | None = None

    def empty(self) -> bool:
        return all(v is None for v in self.model_dump().values())


class ChatResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    finish_reason: str = ""
    rate: RateInfo | None = None


Tier = Literal["strong", "fast"]
