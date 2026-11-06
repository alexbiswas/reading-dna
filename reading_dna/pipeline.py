"""The pipeline: glue that runs every stage in order.

    ingest -> DNA (one LLM call) -> similar shelf (agent) -> steelman shelf (agent) -> verify

Why a pipeline with agents inside, rather than one big autonomous agent?
Each stage has a clear input, output and test. Only the parts that genuinely need open-ended
search (finding books) get an agent loop. Cheaper, faster, easier to debug and to evaluate.

CLI:
    python -m reading_dna.pipeline --goodreads data/sample_goodreads.csv --kindle data/sample_clippings.txt --mock
    python -m reading_dna.pipeline --manual "Dune by Frank Herbert\nThe Road by Cormac McCarthy" --mock
Drop --mock to use the real model (needs ANTHROPIC_API_KEY) and Open Library (needs network).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from mcp import Client

from . import catalog as cat
from .agent import build_shelf, tool_json
from .dna import build_dna
from .ingest import parse_goodreads_csv, parse_kindle_clippings, parse_manual_list
from .llm import LLM, AnthropicLLM
from .mcp_server import build_server
from .mock_llm import MockLLM
from .models import ReaderData, ReadingDNA, normalize_title
from .verifier import verify_shelf


def load_reader(goodreads: str | None = None, kindle: str | None = None,
                manual: str | None = None) -> ReaderData:
    reader = ReaderData()
    if goodreads:
        reader = reader.merge(parse_goodreads_csv(goodreads))
    if kindle:
        reader = reader.merge(parse_kindle_clippings(kindle))
    if manual:
        reader = reader.merge(parse_manual_list(manual))
    return reader


def make_backends(mock: bool) -> tuple[LLM, cat.Catalog]:
    return (MockLLM(), cat.FakeCatalog()) if mock else (AnthropicLLM(), cat.OpenLibraryCatalog())


def _add_usage(total: dict, usage: dict) -> None:
    for k, v in usage.items():
        total[k] = total.get(k, 0) + v


async def build_shelves(reader: ReaderData, dna: ReadingDNA, llm: LLM, catalog: cat.Catalog,
                        n: int = 5) -> dict:
    usage: dict = {}
    result: dict = {"shelves": {}, "rejected": {}, "metrics": {}, "trace": {}}

    # In-process MCP connection. To use a separate server process instead, pass
    # StdioServerParameters(command="python", args=["-m", "reading_dna.mcp_server"]),
    # or a URL if you deploy the server over HTTP. The agent code doesn't change.
    async with Client(build_server(catalog)) as client:

        async def verify_fn(title: str, author: str) -> dict:
            res = await client.call_tool("verify_book", {"title": title, "author": author})
            return tool_json(res)

        picked: set[str] = set()
        for kind in ("similar", "steelman"):
            run = await build_shelf(kind, dna, reader, llm, client, n=n, exclude=picked)
            _add_usage(usage, run.usage)
            result["trace"][kind] = run.trace
            if run.shelf is None:
                result["shelves"][kind], result["rejected"][kind] = [], []
                result["metrics"][kind] = {"error": "agent did not submit a shelf"}
                continue
            verified = await verify_shelf(run.shelf, reader, verify_fn, kind, picked)
            picked |= {normalize_title(r["title"]) for r in verified.kept}
            result["shelves"][kind] = verified.kept
            result["rejected"][kind] = verified.rejected
            result["metrics"][kind] = verified.metrics() | {"agent_turns": run.turns}

    result["usage"] = usage
    return result


async def run_pipeline(reader: ReaderData, llm: LLM, catalog: cat.Catalog, n: int = 5) -> dict:
    t0 = time.perf_counter()
    dna, dna_usage = build_dna(reader, llm)
    out = await build_shelves(reader, dna, llm, catalog, n=n)
    _add_usage(out["usage"], dna_usage)
    out["dna"] = dna.model_dump()
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Reading DNA: two-shelf book recommender")
    p.add_argument("--goodreads", type=Path)
    p.add_argument("--kindle", type=Path)
    p.add_argument("--manual", help="Books you loved, one per line ('Title by Author')")
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--mock", action="store_true", help="No API key or network needed")
    p.add_argument("--out", type=Path, default=Path("results.json"))
    a = p.parse_args()

    reader = load_reader(
        a.goodreads.read_text() if a.goodreads else None,
        a.kindle.read_text() if a.kindle else None,
        a.manual.replace("\\n", "\n") if a.manual else None,
    )
    llm, catalog = make_backends(a.mock)
    out = asyncio.run(run_pipeline(reader, llm, catalog, n=a.n))
    a.out.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    print(f"\nREADING DNA\n  {out['dna']['summary']}")
    for t in out["dna"]["themes"]:
        print(f"  - {t['name']} ({t['strength']}): {', '.join(t['evidence'])}")
    for kind in ("similar", "steelman"):
        print(f"\n{kind.upper()} SHELF")
        for r in out["shelves"][kind]:
            print(f"  + {r['title']} - {r['author']}\n      {r['reason']}")
        for r in out["rejected"][kind]:
            print(f"  x {r['title']} (rejected: {', '.join(r['failed'])})")
        print(f"  metrics: {out['metrics'][kind]}")
    print(f"\nTokens: {out['usage']}  |  {out['seconds']}s  |  full output: {a.out}")


if __name__ == "__main__":
    main()
