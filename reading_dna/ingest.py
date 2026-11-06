"""Stage 1 - ingestion. Pure, deterministic Python: no LLM, no network.

Rule of thumb: never ask an LLM to do something plain code can do reliably.
Parsing files is plain code. Cheap, fast, testable.

Three onboarding paths, because most people don't have a Goodreads export:
  1. Goodreads CSV export
  2. Kindle 'My Clippings.txt'
  3. A typed list: "Dune by Frank Herbert", one per line
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime

from .models import Book, Highlight, ReaderData

MAX_BOOKS = 2000
MAX_HIGHLIGHTS = 5000


def _clean_isbn(raw: str) -> str:
    # Goodreads writes ISBNs as ="0441013597" so spreadsheets don't eat the leading zero.
    return re.sub(r"[^0-9Xx]", "", raw or "")


def _to_int(raw: str) -> int | None:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _iso_date(raw: str) -> str:
    raw = (raw or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def parse_goodreads_csv(text: str) -> ReaderData:
    """Parse the CSV from Goodreads -> My Books -> Import and export -> Export Library."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    required = {"Title", "Author"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("This doesn't look like a Goodreads export (missing Title/Author columns).")

    books = []
    for row in reader:
        title = (row.get("Title") or "").strip()
        if not title:
            continue
        books.append(Book(
            title=title,
            author=(row.get("Author") or "").strip(),
            my_rating=_to_int(row.get("My Rating")) or 0,
            shelf=(row.get("Exclusive Shelf") or "read").strip(),
            date_read=_iso_date(row.get("Date Read", "")),
            isbn=_clean_isbn(row.get("ISBN13") or row.get("ISBN") or ""),
            pages=_to_int(row.get("Number of Pages")),
            year=_to_int(row.get("Original Publication Year") or row.get("Year Published")),
        ))
        if len(books) >= MAX_BOOKS:
            break
    return ReaderData(books=books)


_CLIP_HEADER = re.compile(r"^(?P<title>.+?)\s*\((?P<author>[^()]*)\)\s*$")


def parse_kindle_clippings(text: str) -> ReaderData:
    """Parse Kindle's My Clippings.txt. Entries are separated by '=========='.

    Each entry: line 1 'Title (Author)', line 2 metadata ('- Your Highlight on page 12 | ...'),
    a blank line, then the highlighted text. Bookmarks have no text and are skipped.
    """
    highlights, seen = [], set()
    books: dict[str, Book] = {}
    for entry in text.lstrip("﻿").split("=========="):
        lines = [ln.strip() for ln in entry.strip().splitlines()]
        if len(lines) < 3 or "Highlight" not in lines[1]:
            continue
        body = " ".join(ln for ln in lines[2:] if ln).strip()
        if not body:
            continue
        m = _CLIP_HEADER.match(lines[0])
        title, author = (m["title"], m["author"]) if m else (lines[0], "")
        key = (title, body)
        if key in seen:            # Kindle duplicates a highlight every time you edit it
            continue
        seen.add(key)
        highlights.append(Highlight(book_title=title.strip(), author=author.strip(), text=body))
        books.setdefault(title, Book(title=title.strip(), author=author.strip(), shelf="read"))
        if len(highlights) >= MAX_HIGHLIGHTS:
            break
    return ReaderData(books=list(books.values()), highlights=highlights)


def parse_manual_list(text: str) -> ReaderData:
    """'Dune by Frank Herbert' or 'Dune - Frank Herbert', one per line. Treated as loved books."""
    books = []
    for line in text.splitlines():
        line = line.strip(" -*\t")
        if not line:
            continue
        parts = re.split(r"\s+by\s+|\s+[-–—]\s+", line, maxsplit=1)
        title = parts[0].strip()
        author = parts[1].strip() if len(parts) > 1 else ""
        books.append(Book(title=title, author=author, my_rating=5, shelf="read"))
    return ReaderData(books=books[:50])
