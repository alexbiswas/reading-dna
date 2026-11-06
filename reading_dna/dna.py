"""Stage 2 - build the Reading DNA. One LLM call with *structured output*.

This is a workflow step, not an agent: the input is fixed and we want one well-shaped answer.
The trick for reliable JSON: define a tool whose input_schema is our pydantic model, then
force the model to call it (tool_choice). We never parse free text.
"""
from __future__ import annotations

from .llm import LLM, inline_schema
from .models import ReaderData, ReadingDNA
from .prompts import DNA_SYSTEM

RECORD_DNA_TOOL = {
    "name": "record_reading_dna",
    "description": "Record the reader's Reading DNA.",
    "input_schema": inline_schema(ReadingDNA),
}


def format_library(reader: ReaderData, max_books: int = 150, max_highlights: int = 120) -> str:
    """Turn reader data into compact text for the prompt. Highest-signal items go first,
    and we cap sizes so a 2,000-book library doesn't blow up cost."""
    books = sorted(reader.books, key=lambda b: (b.shelf != "read", -b.my_rating))[:max_books]
    lines = ["LIBRARY (title | author | my rating | shelf):"]
    for b in books:
        rating = f"{b.my_rating}/5" if b.my_rating else "unrated"
        lines.append(f"- {b.title} | {b.author} | {rating} | {b.shelf}")
    if reader.highlights:
        lines.append("\nHIGHLIGHTS (book: passage):")
        for h in reader.highlights[:max_highlights]:
            lines.append(f"- {h.book_title}: \"{h.text[:300]}\"")
    return "\n".join(lines)


def build_dna(reader: ReaderData, llm: LLM) -> tuple[ReadingDNA, dict]:
    if not reader.books:
        raise ValueError("No books to analyse.")
    resp = llm.create(
        system=DNA_SYSTEM,
        messages=[{"role": "user", "content": format_library(reader)}],
        tools=[RECORD_DNA_TOOL],
        tool_choice={"type": "tool", "name": "record_reading_dna"},
    )
    call = resp.tool_uses()[0]
    dna = ReadingDNA.model_validate(call["input"])     # raises if the shape is wrong

    # Grounding check: drop evidence that isn't actually in the library.
    library = {t.lower() for t in (b.title for b in reader.books)}
    def real(titles):
        return [t for t in titles if any(t.lower() in lib or lib in t.lower() for lib in library)]
    for theme in dna.themes:
        theme.evidence = real(theme.evidence)
    for stance in dna.stances:
        stance.source_books = real(stance.source_books)
    dna.themes = [t for t in dna.themes if t.evidence]
    dna.stances = [s for s in dna.stances if s.source_books]
    return dna, resp.usage
