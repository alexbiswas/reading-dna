"""Stage 3 - the agent. This is the core of the project; read it slowly.

An "agent" here is just a loop:
    1. send the conversation + available tools to the LLM
    2. if the LLM asks to use tools, run them (via MCP) and append the results
    3. repeat until the LLM calls our special `submit_shelf` tool (or we hit a turn limit)

Everything an agent "decides" happens in step 1. Everything it can *do* is defined by the tools.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from mcp import Client
from pydantic import ValidationError

from .llm import LLM, inline_schema
from .models import ReaderData, ReadingDNA, Shelf
from .prompts import SIMILAR_SYSTEM, STEELMAN_SYSTEM

# A "finish tool": the agent ends the loop by calling it, and its schema forces structured output.
SUBMIT_SHELF_TOOL = {
    "name": "submit_shelf",
    "description": "Submit the final shelf of recommendations. Call exactly once, at the end.",
    "input_schema": inline_schema(Shelf),
}


@dataclass
class AgentResult:
    shelf: Shelf | None
    trace: list[dict] = field(default_factory=list)       # every step, for debugging + the UI
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    turns: int = 0


def mcp_tools_to_llm_tools(mcp_tools) -> list[dict]:
    """MCP tool definitions -> the LLM API's tool format. That's the whole 'integration'."""
    return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in mcp_tools.tools]


def _tool_result_text(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content)[:4000]


def tool_json(result):
    """Get a tool's return value as Python data. MCP may send it as structured content
    or as JSON text, depending on the return type, so handle both."""
    sc = result.structured_content
    if sc is not None:
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    try:
        return json.loads(_tool_result_text(result))
    except json.JSONDecodeError:
        return {"error": _tool_result_text(result)}


async def run_agent(llm: LLM, client: Client, system: str, user_message: str,
                    max_turns: int = 12) -> AgentResult:
    tools = mcp_tools_to_llm_tools(await client.list_tools()) + [SUBMIT_SHELF_TOOL]
    messages: list[dict] = [{"role": "user", "content": user_message}]
    out = AgentResult(shelf=None)
    nudged = False

    for turn in range(1, max_turns + 1):
        out.turns = turn
        t0 = time.perf_counter()
        resp = await asyncio.to_thread(llm.create, system=system, messages=messages, tools=tools)
        for k in out.usage:
            out.usage[k] += resp.usage.get(k, 0)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.text().strip():
            out.trace.append({"turn": turn, "type": "thinking", "text": resp.text().strip()[:500]})

        if not resp.tool_uses():
            # The model stopped without submitting. Nudge once, then give up.
            if nudged:
                break
            nudged = True
            messages.append({"role": "user", "content": "Please call submit_shelf with your final picks now."})
            continue

        results = []
        for call in resp.tool_uses():
            if call["name"] == "submit_shelf":
                try:
                    out.shelf = Shelf.model_validate(call["input"])
                    out.trace.append({"turn": turn, "type": "submit",
                                      "n": len(out.shelf.recommendations)})
                    return out
                except ValidationError as e:
                    # Self-correction: hand the validation error back and let the model fix it.
                    results.append({"type": "tool_result", "tool_use_id": call["id"],
                                    "content": f"Invalid shelf: {e}", "is_error": True})
                    continue

            result = await client.call_tool(call["name"], call["input"])
            text = _tool_result_text(result)
            out.trace.append({"turn": turn, "type": "tool", "tool": call["name"],
                              "input": call["input"], "output": text[:300],
                              "ms": round((time.perf_counter() - t0) * 1000)})
            results.append({"type": "tool_result", "tool_use_id": call["id"],
                            "content": text, "is_error": bool(result.is_error)})
        messages.append({"role": "user", "content": results})

    out.trace.append({"type": "gave_up", "turns": out.turns})
    return out


# ------------------------------------------------------------------ the two shelves

def shelf_brief(kind: str, dna: ReadingDNA, reader: ReaderData, n: int, exclude: set[str]) -> str:
    seen = sorted({b.title for b in reader.books if b.shelf != "to-read"})
    to_read = sorted({b.title for b in reader.books if b.shelf == "to-read"})
    parts = [
        f"Find {n} books for the {kind.upper()} shelf.",
        "READING DNA:\n" + dna.model_dump_json(indent=1),
        "ALREADY READ OR READING (never recommend): " + json.dumps(seen),
        "ON TO-READ LIST (allowed, mention it): " + json.dumps(to_read[:100]),
    ]
    if exclude:
        parts.append("ALREADY PICKED FOR THE OTHER SHELF (avoid): " + json.dumps(sorted(exclude)))
    return "\n\n".join(parts)


async def build_shelf(kind: str, dna: ReadingDNA, reader: ReaderData, llm: LLM, client: Client,
                      n: int = 5, exclude: set[str] | None = None) -> AgentResult:
    system = SIMILAR_SYSTEM if kind == "similar" else STEELMAN_SYSTEM
    return await run_agent(llm, client, system, shelf_brief(kind, dna, reader, n, exclude or set()))
