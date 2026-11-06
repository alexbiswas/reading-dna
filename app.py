"""Reading DNA - the public web app.     Run:  streamlit run app.py

Product decisions you can see in this file:
  * three onboarding paths (most people have no Goodreads export)
  * DNA is shown and editable BEFORE recommendations (trust + correction)
  * every pick shows its reason and the books that motivated it
  * "show the agent's work" for transparency
  * per-session run limit + upload size cap (cost and abuse control)
  * nothing is stored except anonymous funnel events
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import streamlit as st

from reading_dna import catalog as cat
from reading_dna.analytics import log_event
from reading_dna.dna import build_dna
from reading_dna.ingest import parse_goodreads_csv, parse_kindle_clippings, parse_manual_list
from reading_dna.llm import AnthropicLLM
from reading_dna.mock_llm import MockLLM
from reading_dna.models import ReaderData, ReadingDNA
from reading_dna.pipeline import build_shelves

SAMPLE_DIR = Path(__file__).resolve().parent / "data"
MAX_UPLOAD_BYTES = 2_000_000
MAX_RUNS_PER_SESSION = int(os.environ.get("READING_DNA_MAX_RUNS", "3"))

st.set_page_config(page_title="Reading DNA", page_icon=None, layout="wide")


def api_key() -> str | None:
    try:
        return st.secrets.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    except Exception:                         # no secrets file locally
        return os.environ.get("ANTHROPIC_API_KEY")


ss = st.session_state
if "sid" not in ss:
    ss.sid, ss.runs = uuid.uuid4().hex[:12], 0
    log_event(ss.sid, "app_opened")

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Reading DNA")
    has_key = bool(api_key())
    mock = st.toggle("Demo mode (no AI calls)", value=not has_key, disabled=not has_key)
    st.caption(f"Runs left this session: {MAX_RUNS_PER_SESSION - ss.runs}")
    st.divider()
    st.caption("Privacy: your reading history is processed for this session only and never "
               "stored. We log anonymous usage events (e.g. 'shelf built') to improve the product.")


def backends():
    if mock:
        return MockLLM(), cat.FakeCatalog()
    return AnthropicLLM(api_key=api_key()), cat.OpenLibraryCatalog()


def read_upload(f) -> str | None:
    if f is None:
        return None
    if f.size > MAX_UPLOAD_BYTES:
        st.error("That file is over 2 MB. Please upload a smaller export.")
        return None
    return f.getvalue().decode("utf-8", errors="replace")


# ------------------------------------------------------------------ step 1: data in
st.title("Find your next book: more of what moves you, and the best case against it")
st.write("Two shelves: **Similar** finds books that match your themes and taste. "
         "**Steelman** finds the strongest, fairest book arguing against ideas you've absorbed.")

if mock:
    st.info("Demo mode: no AI is running, and the shelves show pre-written example picks. "
            "Tick 'Use the sample library' to see the full flow as intended.")
use_sample = st.checkbox("Use the sample library (a real reader's Goodreads favourites + Kindle highlights)",
                         value=False)

tab_gr, tab_kindle, tab_manual = st.tabs(["Goodreads export", "Kindle highlights", "Just type a few books"])
with tab_gr:
    st.caption("Goodreads -> My Books -> Import and export -> Export Library")
    gr_file = st.file_uploader("Goodreads CSV", type=["csv"])
with tab_kindle:
    st.caption("Connect your Kindle by USB and find documents/My Clippings.txt")
    kindle_file = st.file_uploader("My Clippings.txt", type=["txt"])
with tab_manual:
    manual = st.text_area("Books you loved, one per line", placeholder="Dune by Frank Herbert\nThe Road by Cormac McCarthy")

if st.button("Sequence my Reading DNA", type="primary"):
    try:
        reader = ReaderData()
        if use_sample:
            reader = reader.merge(parse_goodreads_csv((SAMPLE_DIR / "sample_goodreads.csv").read_text()))
            reader = reader.merge(parse_kindle_clippings((SAMPLE_DIR / "sample_clippings.txt").read_text()))
        if (text := read_upload(gr_file)):
            reader = reader.merge(parse_goodreads_csv(text))
        if (text := read_upload(kindle_file)):
            reader = reader.merge(parse_kindle_clippings(text))
        if manual.strip():
            reader = reader.merge(parse_manual_list(manual))
    except ValueError as e:
        st.error(str(e))
        st.stop()

    if not reader.books:
        st.warning("Add at least a few books first.")
    elif ss.runs >= MAX_RUNS_PER_SESSION:
        st.warning("You've hit this session's limit. Thanks for trying it!")
    else:
        ss.runs += 1
        log_event(ss.sid, "data_uploaded", books=len(reader.books), highlights=len(reader.highlights),
                  sources=[n for n, v in [("sample", use_sample), ("goodreads", gr_file),
                                          ("kindle", kindle_file), ("manual", manual.strip())] if v])
        with st.spinner("Reading your reading..."):
            llm, _ = backends()
            dna, _ = build_dna(reader, llm)
        ss.reader, ss.dna, ss.result = reader, dna, None
        log_event(ss.sid, "dna_built", themes=len(dna.themes), stances=len(dna.stances))

# ------------------------------------------------------------------ step 2: show + edit DNA
if ss.get("dna"):
    dna: ReadingDNA = ss.dna
    st.subheader("Your Reading DNA")
    st.write(dna.summary)
    st.caption("Untick anything that's wrong. The shelves will only use what you keep.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Themes**")
        keep_themes = [t for i, t in enumerate(dna.themes)
                       if st.checkbox(f"{t.name} ({t.strength})", True, key=f"t{i}",
                                      help="Evidence: " + ", ".join(t.evidence))]
        st.markdown("**You like:** " + ", ".join(dna.style_likes))
    with c2:
        st.markdown("**Stances you've absorbed** (the steelman shelf argues against these)")
        keep_stances = [s for i, s in enumerate(dna.stances)
                        if st.checkbox(s.claim, True, key=f"s{i}", help="From: " + ", ".join(s.source_books))]

    if st.button("Build my two shelves", type="primary"):
        edited = dna.model_copy(update={"themes": keep_themes, "stances": keep_stances})
        if len(keep_themes) < len(dna.themes) or len(keep_stances) < len(dna.stances):
            log_event(ss.sid, "dna_edited", removed=len(dna.themes) + len(dna.stances) - len(keep_themes) - len(keep_stances))
        with st.spinner("The agents are searching the catalog..."):
            llm, catalog = backends()
            ss.result = asyncio.run(build_shelves(ss.reader, edited, llm, catalog, n=5))
        m = ss.result["metrics"]
        log_event(ss.sid, "shelves_built", **{f"{k}_kept": v.get("kept", 0) for k, v in m.items()})

# ------------------------------------------------------------------ step 3: shelves
if ss.get("result"):
    res = ss.result
    cols = st.columns(2)
    for col, kind, label in [(cols[0], "similar", "Similar shelf"), (cols[1], "steelman", "Steelman shelf")]:
        with col:
            st.subheader(label)
            if not res["shelves"][kind]:
                st.info("No picks survived verification this time. Try again or add more books.")
            for i, r in enumerate(res["shelves"][kind]):
                with st.container(border=True):
                    st.markdown(f"**{r['title']}** by {r['author']}" + (" · on your to-read list" if r["on_to_read"] else ""))
                    if r.get("counters"):
                        st.caption(f"Argues against: {r['counters']}")
                    st.write(r["reason"])
                    st.caption("Because you read: " + ", ".join(r["because_of"]))
                    b1, b2 = st.columns(2)
                    if b1.button("Want to read", key=f"{kind}{i}y"):
                        log_event(ss.sid, "rec_feedback", shelf=kind, title=r["title"], verdict="want")
                        st.toast("Noted!")
                    if b2.button("Not for me", key=f"{kind}{i}n"):
                        log_event(ss.sid, "rec_feedback", shelf=kind, title=r["title"], verdict="no")
                        st.toast("Got it, thanks.")

    with st.expander("Show the agent's work"):
        st.write("Verifier metrics", res["metrics"])
        st.write("Rejected picks (and why)", res["rejected"])
        st.write("Step-by-step trace", res["trace"])
        st.caption(f"Tokens used: {res['usage']}")
