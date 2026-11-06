"""Stage 4 - the verifier. Deterministic code that checks the agent's work.

The prompt *asks* the agent to verify books and avoid ones you've read. The verifier
*enforces* it. Never trust a prompt alone for rules that must hold.

Checks per recommendation:
  exists    - the catalog has this exact title/author        (catches hallucinated books)
  unseen    - not on the reader's read/reading shelves         (catches lazy repeats)
  grounded  - every because_of title is really in the library  (catches made-up reasons)
  unique    - not already on this or the other shelf
  counters  - steelman picks must say what stance they oppose

The rejection counts double as eval metrics: hallucination rate etc. are logged on every run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .models import ReaderData, Shelf, normalize_title

VerifyFn = Callable[[str, str], Awaitable[dict]]


@dataclass
class VerifiedShelf:
    kept: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def metrics(self) -> dict:
        proposed = len(self.kept) + len(self.rejected)
        def count(reason):
            return sum(1 for r in self.rejected if reason in r["failed"])
        return {
            "proposed": proposed,
            "kept": len(self.kept),
            "hallucinated": count("exists"),
            "already_read": count("unseen"),
            "ungrounded": count("grounded"),
            "hallucination_rate": round(count("exists") / proposed, 3) if proposed else 0.0,
        }


async def verify_shelf(shelf: Shelf, reader: ReaderData, verify_fn: VerifyFn, kind: str,
                       already_picked: set[str] | None = None) -> VerifiedShelf:
    seen = reader.seen_titles()
    to_read = reader.to_read_titles()
    library = reader.library_titles()
    picked = set(already_picked or ())
    out = VerifiedShelf()

    for rec in shelf.recommendations:
        key = normalize_title(rec.title)
        checks = {
            "unseen": key not in seen,
            "unique": key not in picked,
            "grounded": bool(rec.because_of) and all(
                normalize_title(t) in library for t in rec.because_of),
            "counters": kind != "steelman" or bool(rec.counters),
        }
        found = await verify_fn(rec.title, rec.author)
        checks["exists"] = bool(found.get("exists"))

        item = rec.model_dump() | {"on_to_read": key in to_read, "year": found.get("year")}
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            out.rejected.append(item | {"failed": failed})
        else:
            out.kept.append(item)
            picked.add(key)
    return out
