"""A thin wrapper around the LLM API.

Everything else in the codebase talks to `LLM.create(...)`, never to the Anthropic SDK
directly. That one seam lets us:
  * swap in MockLLM for tests, evals and offline demos (see mock_llm.py)
  * count tokens in one place (cost per recommendation is a metric we report)
  * change model or provider without touching agent code
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_MODEL = os.environ.get("READING_DNA_MODEL", "claude-sonnet-5-5")


@dataclass
class LLMResponse:
    content: list[dict]                      # blocks: {"type": "text"|"tool_use", ...}
    stop_reason: str                         # "end_turn" | "tool_use" | "max_tokens"
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b["type"] == "text")

    def tool_uses(self) -> list[dict]:
        return [b for b in self.content if b["type"] == "tool_use"]


class LLM(Protocol):
    def create(self, *, system: str, messages: list[dict], tools: list[dict] | None = None,
               tool_choice: dict | None = None, max_tokens: int = 4000) -> LLMResponse: ...


class AnthropicLLM:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        import anthropic                     # lazy: mock mode works without the SDK installed
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model

    def create(self, *, system, messages, tools=None, tool_choice=None, max_tokens=4000):
        kwargs: dict[str, Any] = dict(model=self.model, max_tokens=max_tokens,
                                      system=system, messages=messages)
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        resp = self.client.messages.create(**kwargs)
        blocks = [b.model_dump(exclude_none=True) for b in resp.content]
        return LLMResponse(content=blocks, stop_reason=resp.stop_reason,
                           usage={"input_tokens": resp.usage.input_tokens,
                                  "output_tokens": resp.usage.output_tokens})


def inline_schema(model_cls) -> dict:
    """Pydantic -> a self-contained JSON schema (no $ref/$defs) to use as a tool input_schema."""
    schema = model_cls.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {k: resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(schema)
