"""Shared data shapes.

Two kinds of models live here:
  * dataclasses for the reader's raw data (books, highlights) - produced by ingest.py
  * pydantic models for anything an LLM produces (Reading DNA, recommendations).
    Pydantic gives us a JSON schema to hand to the model AND validation of what comes back.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- reader data

@dataclass
class Book:
    title: str
    author: str
    my_rating: int = 0              # 0 = not rated, 1-5 stars
    shelf: str = "read"             # read | currently-reading | to-read | did-not-finish
    date_read: str = ""             # ISO date if known
    isbn: str = ""
    pages: int | None = None
    year: int | None = None


@dataclass
class Highlight:
    book_title: str
    author: str
    text: str


@dataclass
class ReaderData:
    books: list[Book] = field(default_factory=list)
    highlights: list[Highlight] = field(default_factory=list)

    def finished(self) -> list[Book]:
        return [b for b in self.books if b.shelf == "read"]

    def seen_titles(self) -> set[str]:
        """Titles the reader has read or is reading - never recommend these."""
        return {normalize_title(b.title) for b in self.books
                if b.shelf in ("read", "currently-reading", "did-not-finish")}

    def to_read_titles(self) -> set[str]:
        return {normalize_title(b.title) for b in self.books if b.shelf == "to-read"}

    def library_titles(self) -> set[str]:
        return {normalize_title(b.title) for b in self.books}

    def merge(self, other: "ReaderData") -> "ReaderData":
        known = self.library_titles()
        extra = [b for b in other.books if normalize_title(b.title) not in known]
        return ReaderData(self.books + extra, self.highlights + other.highlights)


def normalize_title(title: str) -> str:
    """'The Road: A Novel' -> 'road'. Used everywhere we compare titles."""
    t = title.lower().split(":")[0].split("(")[0]
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"^(the|a|an) ", "", t.strip())
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- LLM outputs

class Theme(BaseModel):
    name: str = Field(description="Short theme name, e.g. 'power and the systems behind it'")
    evidence: list[str] = Field(description="Titles from the reader's library that show this theme")
    strength: Literal["strong", "medium", "weak"]


class Stance(BaseModel):
    claim: str = Field(description="A position the reader has absorbed, stated as a one-sentence thesis")
    source_books: list[str] = Field(description="Titles from the library that argue this position")


class ReadingDNA(BaseModel):
    summary: str = Field(description="Two sentences, second person, describing this reader")
    themes: list[Theme] = Field(description="3-6 recurring themes")
    style_likes: list[str] = Field(description="Writing qualities the reader gravitates to")
    style_dislikes: list[str] = Field(description="Writing qualities the reader tends to abandon or rate low")
    stances: list[Stance] = Field(description="2-5 arguable positions from non-fiction (and idea-driven fiction)")


class Recommendation(BaseModel):
    title: str
    author: str
    reason: str = Field(description="One or two sentences on why this reader specifically")
    because_of: list[str] = Field(description="Titles from the reader's library that motivated this pick")
    counters: str | None = Field(default=None, description="Steelman shelf only: the stance this book argues against")


class Shelf(BaseModel):
    recommendations: list[Recommendation]
