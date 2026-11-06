"""MockLLM: a fake model that follows the same protocol as AnthropicLLM.

It lets you run the whole pipeline, the app and the evals with no API key and no network.
It exercises every code path, including the failure ones: the similar shelf deliberately
proposes a made-up book and a book you've already read, so you can watch the verifier
reject them.

Mock mode tests the *plumbing*, never the *quality*. Quality evals need the real model.
"""
from __future__ import annotations

import copy
import itertools

from .llm import LLMResponse

_ids = itertools.count(1)


def _tool_use(name: str, payload: dict) -> LLMResponse:
    return LLMResponse(content=[{"type": "tool_use", "id": f"toolu_mock_{next(_ids)}",
                                 "name": name, "input": copy.deepcopy(payload)}], stop_reason="tool_use")


MOCK_DNA = {
    "summary": "You're drawn to stories about power and the systems behind it, and to quiet, "
               "unsettling questions about what makes someone human. Your non-fiction leans "
               "toward how people and markets really behave.",
    "themes": [
        {"name": "Power and the systems behind it", "strength": "strong",
         "evidence": ["The Godfather", "Dune", "Watchmen", "The Day of the Jackal"]},
        {"name": "What makes someone human", "strength": "strong",
         "evidence": ["Do Androids Dream of Electric Sheep?", "The Stranger", "All Systems Red"]},
        {"name": "How people and markets actually behave", "strength": "medium",
         "evidence": ["Predictably Irrational", "Zero to One", "Hooked"]},
        {"name": "Endings and collapse", "strength": "medium",
         "evidence": ["The Road", "The Sixth Extinction"]},
    ],
    "style_likes": ["tight, plot-driven tension", "spare, bleak literary prose", "big ideas made concrete"],
    "style_dislikes": ["sprawling, slow middles"],
    "stances": [
        {"claim": "Durable monopolies, not competition, drive technological progress",
         "source_books": ["Zero to One"]},
        {"claim": "Designing habit-forming products is good product strategy",
         "source_books": ["Hooked"]},
        {"claim": "Human decision-making is systematically irrational",
         "source_books": ["Predictably Irrational"]},
    ],
}

MOCK_SIMILAR = {"recommendations": [
    {"title": "Exhalation", "author": "Ted Chiang",
     "reason": "Quiet, brainy stories about machines and free will, for the reader who loved Androids.",
     "because_of": ["Do Androids Dream of Electric Sheep?", "All Systems Red"]},
    {"title": "Klara and the Sun", "author": "Kazuo Ishiguro",
     "reason": "An artificial narrator asking what love is, with Camus-like restraint.",
     "because_of": ["Do Androids Dream of Electric Sheep?", "The Stranger"]},
    {"title": "The Spy Who Came in from the Cold", "author": "John le Carré",
     "reason": "The cold procedural tension of Jackal, with more moral rot.",
     "because_of": ["The Day of the Jackal", "The Godfather"]},
    {"title": "Dune", "author": "Frank Herbert",               # already read -> verifier rejects
     "reason": "Power, ecology and politics.", "because_of": ["Watchmen"]},
    {"title": "The Quiet Algorithm", "author": "R. Mendes",    # doesn't exist -> verifier rejects
     "reason": "A literary novel about an AI that learns grief.", "because_of": ["The Road"]},
]}

MOCK_STEELMAN = {"recommendations": [
    {"title": "The Myth of Capitalism", "author": "Jonathan Tepper",
     "reason": "Argues that market concentration suppresses competition, wages and innovation.",
     "because_of": ["Zero to One"],
     "counters": "Durable monopolies, not competition, drive technological progress"},
    {"title": "Irresistible", "author": "Adam Alter",
     "reason": "Argues habit-forming tech shades into behavioural addiction with real costs.",
     "because_of": ["Hooked"],
     "counters": "Designing habit-forming products is good product strategy"},
    {"title": "Risk Savvy", "author": "Gerd Gigerenzer",
     "reason": "Argues our mental shortcuts are often smart adaptations, not flaws.",
     "because_of": ["Predictably Irrational"],
     "counters": "Human decision-making is systematically irrational"},
    {"title": "Silent Spring", "author": "Rachel Carson",      # cites a book not in library -> rejected
     "reason": "Environmental classic.", "because_of": ["The Lorax"],
     "counters": "Progress is good"},
]}

# Mock judge verdicts: plausible-looking but hard-coded. Only for testing the eval plumbing.
_BAD_STEELMEN = {"nudge", "indistractable", "the 4-hour workweek"}


class MockLLM:
    def create(self, *, system, messages, tools=None, tool_choice=None, max_tokens=4000):
        names = {t["name"] for t in (tools or [])}

        if "record_reading_dna" in names:
            return _tool_use("record_reading_dna", MOCK_DNA)

        if "record_judgement" in names:
            text = str(messages[-1]["content"]).lower()
            bad = any(f"book b: {t}" in text for t in _BAD_STEELMEN)
            s = 2 if bad else 5
            return _tool_use("record_judgement", {
                "opposing_thesis": s, "credibility": 4, "good_faith": 4, "rigor_match": 4,
                "pass": not bad, "rationale": "mock verdict"})

        if "submit_shelf" in names:
            steelman = "STEELMAN" in system
            n_results = sum(1 for m in messages if m["role"] == "user" and isinstance(m["content"], list))
            if n_results == 0:     # turn 1: search, like a real agent would
                q = "monopoly competition economics" if steelman else "artificial intelligence androids"
                return _tool_use("search_books", {"query": q, "limit": 5})
            if n_results == 1:     # turn 2: verify a candidate
                title = "The Myth of Capitalism" if steelman else "Exhalation"
                return _tool_use("verify_book", {"title": title})
            return _tool_use("submit_shelf", MOCK_STEELMAN if steelman else MOCK_SIMILAR)

        return LLMResponse(content=[{"type": "text", "text": "mock"}], stop_reason="end_turn")
