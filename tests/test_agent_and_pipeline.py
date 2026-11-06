"""End-to-end tests with MockLLM + FakeCatalog: the agent loop, the verifier and the pipeline."""
import asyncio
from pathlib import Path

from reading_dna.catalog import FakeCatalog
from reading_dna.dna import RECORD_DNA_TOOL, build_dna
from reading_dna.llm import LLMResponse
from reading_dna.mock_llm import MockLLM
from reading_dna.models import Recommendation, Shelf
from reading_dna.pipeline import load_reader, run_pipeline
from reading_dna.verifier import verify_shelf

DATA = Path(__file__).resolve().parent.parent / "data"


def sample_reader():
    return load_reader((DATA / "sample_goodreads.csv").read_text(), (DATA / "sample_clippings.txt").read_text())


def test_tool_schemas_have_no_refs():
    assert "$ref" not in str(RECORD_DNA_TOOL["input_schema"])


def test_dna_drops_invented_evidence():
    class InventingLLM(MockLLM):
        def create(self, **kw):
            resp = super().create(**kw)
            resp.content[0]["input"]["themes"][0]["evidence"].append("A Book I Made Up")
            return resp
    dna, _ = build_dna(sample_reader(), InventingLLM())
    assert all("A Book I Made Up" not in t.evidence for t in dna.themes)


def test_pipeline_end_to_end_rejects_bad_picks():
    out = asyncio.run(run_pipeline(sample_reader(), MockLLM(), FakeCatalog()))
    similar = {r["title"] for r in out["shelves"]["similar"]}
    rejected = {r["title"]: r["failed"] for r in out["rejected"]["similar"]}
    assert "Exhalation" in similar
    assert "unseen" in rejected["Dune"]                   # already read
    assert "exists" in rejected["The Quiet Algorithm"]    # hallucinated
    assert "grounded" in out["rejected"]["steelman"][0]["failed"]
    assert out["metrics"]["similar"]["hallucination_rate"] == 0.2
    tools_used = [s["tool"] for s in out["trace"]["similar"] if s["type"] == "tool"]
    assert tools_used == ["search_books", "verify_book"]


def test_verifier_blocks_duplicates_across_shelves():
    reader = sample_reader()
    shelf = Shelf(recommendations=[Recommendation(title="Exhalation", author="Ted Chiang",
                                                  reason="x", because_of=["Dune"])])
    async def exists(title, author):
        return {"exists": True}
    v = asyncio.run(verify_shelf(shelf, reader, exists, "steelman", already_picked={"exhalation"}))
    assert v.rejected[0]["failed"] == ["unique", "counters"]


def test_agent_self_corrects_invalid_submission():
    """First submission is missing required fields; the loop returns the error and the model retries."""
    class SloppyLLM(MockLLM):
        calls = 0
        def create(self, **kw):
            SloppyLLM.calls += 1
            if SloppyLLM.calls == 1:
                return LLMResponse(content=[{"type": "tool_use", "id": "t1", "name": "submit_shelf",
                                             "input": {"recommendations": [{"title": "Exhalation"}]}}],
                                   stop_reason="tool_use")
            last = kw["messages"][-1]["content"][0]
            assert last["is_error"] and "Invalid shelf" in last["content"]
            return LLMResponse(content=[{"type": "tool_use", "id": "t2", "name": "submit_shelf",
                                         "input": {"recommendations": [{"title": "Exhalation", "author": "Ted Chiang",
                                                                        "reason": "r", "because_of": ["Dune"]}]}}],
                               stop_reason="tool_use")

    from mcp import Client
    from reading_dna.agent import run_agent
    from reading_dna.mcp_server import build_server

    async def go():
        async with Client(build_server(FakeCatalog())) as c:
            return await run_agent(SloppyLLM(), c, "system", "find books")
    result = asyncio.run(go())
    assert result.shelf and result.turns == 2
