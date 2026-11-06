"""Test the MCP server exactly the way the agent uses it: through an MCP client.
The client connects in-process, so no subprocess and no network."""
import asyncio

from mcp import Client

from reading_dna.agent import mcp_tools_to_llm_tools, tool_json
from reading_dna.catalog import FakeCatalog
from reading_dna.mcp_server import build_server


def run(coro):
    return asyncio.run(coro)


async def _with_client(fn):
    async with Client(build_server(FakeCatalog())) as client:
        return await fn(client)


def test_server_lists_three_tools_with_schemas():
    tools = run(_with_client(lambda c: c.list_tools()))
    llm_tools = mcp_tools_to_llm_tools(tools)
    assert {t["name"] for t in llm_tools} == {"search_books", "get_book_details", "verify_book"}
    verify = next(t for t in llm_tools if t["name"] == "verify_book")
    assert verify["input_schema"]["required"] == ["title"]
    assert "before recommending" in verify["description"]       # docstring becomes the prompt


def test_verify_book_real_and_fake():
    async def go(c):
        real = tool_json(await c.call_tool("verify_book", {"title": "Klara and the Sun", "author": "Kazuo Ishiguro"}))
        fake = tool_json(await c.call_tool("verify_book", {"title": "The Quiet Algorithm", "author": "R. Mendes"}))
        wrong_author = tool_json(await c.call_tool("verify_book", {"title": "Dune", "author": "Ted Chiang"}))
        return real, fake, wrong_author
    real, fake, wrong_author = run(_with_client(go))
    assert real["exists"] and real["year"] == 2021
    assert not fake["exists"]
    assert not wrong_author["exists"]


def test_search_and_details():
    async def go(c):
        hits = tool_json(await c.call_tool("search_books", {"query": "monopoly antitrust", "limit": 3}))
        details = tool_json(await c.call_tool("get_book_details", {"work_id": hits[0]["work_id"]}))
        return hits, details
    hits, details = run(_with_client(go))
    assert hits[0]["title"] == "The Myth of Capitalism"
    assert "monopoly" in details["subjects"]
