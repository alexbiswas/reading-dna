"""Offline evals. Four of them, each answering a different question:

  verifier  "Is the agent making things up?"   hallucination / already-read / ungrounded rates
  holdout   "Does the similar shelf know my taste?"   hide your latest loved books, see if it finds them
  judge     "Are the steelman picks genuinely opposing?"   LLM-as-judge, calibrated on a golden set
  blind     "Do humans agree?"   exports a shuffled sheet for friends to rate, judge scores hidden

Usage (add --mock to test the plumbing offline; drop it for real scores):
    python -m evals.run_evals verifier --goodreads data/sample_goodreads.csv --runs 3 --mock
    python -m evals.run_evals holdout  --goodreads data/sample_goodreads.csv --hide 3 --mock
    python -m evals.run_evals judge    --goodreads data/sample_goodreads.csv --mock
    python -m evals.run_evals blind    --goodreads data/sample_goodreads.csv --mock
Results are appended to evals/results/<eval>.jsonl so you can compare runs over time.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import statistics
import time
from pathlib import Path

from pydantic import BaseModel, Field

from reading_dna.catalog import Catalog
from reading_dna.llm import LLM, inline_schema
from reading_dna.models import ReaderData, normalize_title
from reading_dna.pipeline import load_reader, make_backends, run_pipeline
from reading_dna.prompts import JUDGE_SYSTEM

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def save(eval_name: str, record: dict) -> None:
    RESULTS.mkdir(exist_ok=True)
    record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **record}
    with (RESULTS / f"{eval_name}.jsonl").open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, indent=2, ensure_ascii=False))


# ------------------------------------------------------------------ 1. verifier metrics

def eval_verifier(reader: ReaderData, llm: LLM, catalog: Catalog, runs: int) -> dict:
    """LLMs vary run to run, so run several times and report averages."""
    per_run = []
    for _ in range(runs):
        out = asyncio.run(run_pipeline(reader, llm, catalog))
        for kind, m in out["metrics"].items():
            if "proposed" in m:
                per_run.append({"shelf": kind, **m, "tokens": sum(out["usage"].values()), "seconds": out["seconds"]})

    def avg(key, shelf=None):
        vals = [r[key] for r in per_run if shelf is None or r["shelf"] == shelf]
        return round(statistics.mean(vals), 3) if vals else None

    return {"runs": runs,
            "hallucination_rate": avg("hallucination_rate"),
            "already_read_per_shelf": avg("already_read"),
            "ungrounded_per_shelf": avg("ungrounded"),
            "kept_similar": avg("kept", "similar"),
            "kept_steelman": avg("kept", "steelman"),
            "avg_tokens_per_run": avg("tokens"),
            "avg_seconds_per_run": avg("seconds")}


# ------------------------------------------------------------------ 2. hold-out

def split_holdout(reader: ReaderData, hide: int) -> tuple[ReaderData, list]:
    """Hide the most recently read books you rated 4-5 stars. The agent sees the rest."""
    loved = sorted([b for b in reader.books if b.shelf == "read" and b.my_rating >= 4 and b.date_read],
                   key=lambda b: b.date_read)
    hidden = loved[-hide:]
    hidden_keys = {normalize_title(b.title) for b in hidden}
    visible = ReaderData(
        books=[b for b in reader.books if normalize_title(b.title) not in hidden_keys],
        highlights=[h for h in reader.highlights if normalize_title(h.book_title) not in hidden_keys])
    return visible, hidden


def eval_holdout(reader: ReaderData, llm: LLM, catalog: Catalog, hide: int) -> dict:
    visible, hidden = split_holdout(reader, hide)
    if not hidden:
        return {"error": "Need rated (4-5 star) books with a Date Read to hold out."}
    out = asyncio.run(run_pipeline(visible, llm, catalog, n=10))
    recs = out["shelves"]["similar"]
    rec_titles = {normalize_title(r["title"]) for r in recs}
    rec_authors = {r["author"].split()[-1].lower() for r in recs if r["author"]}

    # "Soft" hit: a recommendation shares 2+ catalog subjects with a hidden book.
    def subjects(title, author):
        hits = catalog.search(f"{title} {author}", limit=3)
        return {s.lower() for h in hits[:1] for s in h.get("subjects", [])}
    rec_subjects = [subjects(r["title"], r["author"]) for r in recs]

    rows = []
    for b in hidden:
        hs = subjects(b.title, b.author)
        rows.append({"hidden": b.title,
                     "exact_hit": normalize_title(b.title) in rec_titles,
                     "author_hit": b.author.split()[-1].lower() in rec_authors if b.author else False,
                     "soft_hit": any(len(hs & rs) >= 2 for rs in rec_subjects)})
    n = len(rows)
    return {"hidden": n, "recommended": len(recs),
            "exact_hit_rate": round(sum(r["exact_hit"] for r in rows) / n, 3),
            "author_hit_rate": round(sum(r["author_hit"] for r in rows) / n, 3),
            "soft_hit_rate": round(sum(r["soft_hit"] for r in rows) / n, 3),
            "detail": rows}


# ------------------------------------------------------------------ 3. LLM judge

class Judgement(BaseModel):
    opposing_thesis: int = Field(ge=1, le=5)
    credibility: int = Field(ge=1, le=5)
    good_faith: int = Field(ge=1, le=5)
    rigor_match: int = Field(ge=1, le=5)
    pass_: bool = Field(alias="pass")
    rationale: str


JUDGE_TOOL = {"name": "record_judgement", "description": "Record the steelman judgement.",
              "input_schema": inline_schema(Judgement)}


def judge_pair(llm: LLM, a: str, a_author: str, b: str, b_author: str) -> Judgement:
    resp = llm.create(system=JUDGE_SYSTEM, tools=[JUDGE_TOOL],
                      tool_choice={"type": "tool", "name": "record_judgement"},
                      messages=[{"role": "user", "content": f"Book A: {a} by {a_author}\nBook B: {b} by {b_author}"}])
    return Judgement.model_validate(resp.tool_uses()[0]["input"])


def eval_judge(reader: ReaderData, llm: LLM, catalog: Catalog) -> dict:
    # Step 1: calibrate. Does the judge agree with human labels on the golden set?
    golden = json.loads((HERE / "golden_steelman.json").read_text())["pairs"]
    agree = 0
    disagreements = []
    for p in golden:
        j = judge_pair(llm, p["a"], p["a_author"], p["b"], p["b_author"])
        if j.pass_ == p["label"]:
            agree += 1
        else:
            disagreements.append({"pair": f"{p['a']} vs {p['b']}", "human": p["label"], "judge": j.pass_})
    calibration = round(agree / len(golden), 3)

    # Step 2: only then score the agent's real steelman picks.
    out = asyncio.run(run_pipeline(reader, llm, catalog))
    scored = []
    for r in out["shelves"]["steelman"]:
        source = r["because_of"][0]
        author = next((b.author for b in reader.books if normalize_title(b.title) == normalize_title(source)), "")
        j = judge_pair(llm, source, author, r["title"], r["author"])
        scored.append({"a": source, "b": r["title"], "pass": j.pass_,
                       "scores": j.model_dump(exclude={"pass_", "rationale"}), "rationale": j.rationale})
    pass_rate = round(sum(s["pass"] for s in scored) / len(scored), 3) if scored else None
    return {"judge_agreement_with_humans": calibration, "disagreements": disagreements,
            "steelman_pass_rate": pass_rate, "picks": scored,
            "warning": None if calibration >= 0.85 else "Judge disagrees with humans too often. Fix the prompt before trusting pass rates."}


# ------------------------------------------------------------------ 4. blind human test

def eval_blind(reader: ReaderData, llm: LLM, catalog: Catalog) -> dict:
    """Mix the agent's steelman pairs with decoys from the golden set, shuffle, hide labels.
    Friends rate each pair 1-5 for 'is B a fair, strong counterargument to A?'"""
    out = asyncio.run(run_pipeline(reader, llm, catalog))
    rows = [{"a": r["because_of"][0], "b": f"{r['title']} ({r['author']})", "_source": "agent"}
            for r in out["shelves"]["steelman"]]
    golden = json.loads((HERE / "golden_steelman.json").read_text())["pairs"]
    rows += [{"a": p["a"], "b": f"{p['b']} ({p['b_author']})", "_source": f"golden_{'good' if p['label'] else 'bad'}"}
             for p in golden]
    random.shuffle(rows)
    RESULTS.mkdir(exist_ok=True)
    sheet, key = RESULTS / "blind_test_sheet.csv", RESULTS / "blind_test_key.csv"
    with sheet.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "book_a", "book_b", "rating_1_to_5", "comment"])
        for i, r in enumerate(rows, 1):
            w.writerow([i, r["a"], r["b"], "", ""])
    with key.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "source"])
        for i, r in enumerate(rows, 1):
            w.writerow([i, r["_source"]])
    return {"pairs": len(rows), "sheet_for_raters": str(sheet), "answer_key_keep_private": str(key),
            "how_to_score": "Average rating for 'agent' rows should beat 'golden_bad' and approach 'golden_good'."}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("eval", choices=["verifier", "holdout", "judge", "blind"])
    p.add_argument("--goodreads", type=Path)
    p.add_argument("--kindle", type=Path)
    p.add_argument("--mock", action="store_true")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--hide", type=int, default=3)
    a = p.parse_args()

    reader = load_reader(a.goodreads.read_text() if a.goodreads else None,
                         a.kindle.read_text() if a.kindle else None)
    llm, catalog = make_backends(a.mock)
    if a.eval == "verifier":
        result = eval_verifier(reader, llm, catalog, a.runs)
    elif a.eval == "holdout":
        result = eval_holdout(reader, llm, catalog, a.hide)
    elif a.eval == "judge":
        result = eval_judge(reader, llm, catalog)
    else:
        result = eval_blind(reader, llm, catalog)
    save(a.eval, {"mock": a.mock, **result})


if __name__ == "__main__":
    main()
