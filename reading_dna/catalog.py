"""Book catalogs. Two interchangeable implementations behind one interface:

  * OpenLibraryCatalog - the real thing (free, no API key): https://openlibrary.org/developers/api
  * FakeCatalog        - a small JSON file, used for tests, evals and offline demos

Design lesson: put external services behind a small interface so you can swap in a fake.
That's what makes the agent testable without network access or API spend.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .models import normalize_title

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class Catalog(Protocol):
    def search(self, query: str, limit: int = 5) -> list[dict]: ...
    def details(self, work_id: str) -> dict | None: ...


def _matches(wanted_title: str, wanted_author: str, book: dict) -> bool:
    if normalize_title(book.get("title", "")) != normalize_title(wanted_title):
        return False
    if not wanted_author:
        return True
    last = wanted_author.split()[-1].lower()
    return last in (book.get("author") or "").lower()


def verify(catalog: Catalog, title: str, author: str = "") -> dict:
    """Does this exact book exist? The key anti-hallucination check."""
    for book in catalog.search(f"{title} {author}".strip(), limit=10):
        if _matches(title, author, book):
            return {"exists": True, **book}
    return {"exists": False, "title": title, "author": author}


class OpenLibraryCatalog:
    BASE = "https://openlibrary.org"
    # A descriptive User-Agent is polite; Open Library asks for one. No personal data here.
    HEADERS = {"User-Agent": "ReadingDNA/0.1 (learning project)"}

    def __init__(self, timeout: float = 10.0):
        import requests                    # imported lazily so tests don't need it
        self._http = requests.Session()
        self._http.headers.update(self.HEADERS)
        self.timeout = timeout

    def search(self, query: str, limit: int = 5) -> list[dict]:
        r = self._http.get(f"{self.BASE}/search.json", timeout=self.timeout, params={
            "q": query, "limit": limit,
            "fields": "key,title,author_name,first_publish_year,subject,number_of_pages_median,cover_i",
        })
        r.raise_for_status()
        return [{
            "work_id": d["key"].split("/")[-1],
            "title": d.get("title", ""),
            "author": ", ".join(d.get("author_name", [])[:2]),
            "year": d.get("first_publish_year"),
            "subjects": d.get("subject", [])[:8],
            "pages": d.get("number_of_pages_median"),
            "cover_url": (f"https://covers.openlibrary.org/b/id/{d['cover_i']}-M.jpg"
                          if d.get("cover_i") else None),
        } for d in r.json().get("docs", [])]

    def details(self, work_id: str) -> dict | None:
        r = self._http.get(f"{self.BASE}/works/{work_id}.json", timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        d = r.json()
        desc = d.get("description", "")
        if isinstance(desc, dict):
            desc = desc.get("value", "")
        return {"work_id": work_id, "title": d.get("title", ""),
                "subjects": d.get("subjects", [])[:12], "description": desc[:1200]}


class FakeCatalog:
    """Keyword search over data/sample_catalog.json. Good enough to exercise every code path."""

    def __init__(self, path: Path | None = None):
        self.books = json.loads((path or DATA_DIR / "sample_catalog.json").read_text())

    def search(self, query: str, limit: int = 5) -> list[dict]:
        words = {w for w in normalize_title(query).split() if len(w) > 2}
        scored = []
        for b in self.books:
            hay = set(normalize_title(f"{b['title']} {b['author']} {' '.join(b['subjects'])}").split())
            score = len(words & hay)
            if score:
                scored.append((score, b))
        scored.sort(key=lambda x: -x[0])
        return [{k: v for k, v in b.items() if k != "description"} for _, b in scored[:limit]]

    def details(self, work_id: str) -> dict | None:
        return next((b for b in self.books if b["work_id"] == work_id), None)
