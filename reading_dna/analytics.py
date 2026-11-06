"""Product analytics: append events to a JSONL file.

This is how you get a funnel to talk about in interviews:
    app_opened -> data_uploaded -> dna_built -> dna_edited -> shelves_built -> rec_feedback
Swap the file for PostHog/Amplitude/Mixpanel later; keep the same event names.
Privacy: log counts and recommendation titles, never the user's full reading history.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

EVENTS_FILE = Path(os.environ.get("READING_DNA_EVENTS", "events.jsonl"))


def log_event(session_id: str, name: str, **props) -> None:
    record = {"ts": round(time.time(), 3), "session": session_id, "event": name, **props}
    with EVENTS_FILE.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def funnel(path: Path = EVENTS_FILE) -> dict:
    """Unique sessions that reached each step."""
    steps = ["app_opened", "data_uploaded", "dna_built", "shelves_built", "rec_feedback"]
    reached: dict[str, set] = {s: set() for s in steps}
    if path.exists():
        for line in path.read_text().splitlines():
            e = json.loads(line)
            if e["event"] in reached:
                reached[e["event"]].add(e["session"])
    return {s: len(reached[s]) for s in steps}
