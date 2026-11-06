"""Reading DNA - the web app.     Run:  streamlit run app.py

Product decisions you can see in this file:
  * four ways in: Goodreads profile link, Goodreads export, Kindle highlights, or typed favourites
  * private Goodreads profiles get a plain explanation and a fallback, not a dead end
  * the DNA is shown and editable BEFORE recommendations (trust + correction)
  * live progress while the agents search, and "how these were picked" afterwards
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
from reading_dna import ui
from reading_dna.analytics import log_event
from reading_dna.dna import build_dna
from reading_dna.goodreads import GoodreadsError, fetch_profile
from reading_dna.ingest import parse_goodreads_csv, parse_kindle_clippings, parse_manual_list
from reading_dna.llm import AnthropicLLM
from reading_dna.mock_llm import MOCK_DNA, MockLLM
from reading_dna.models import ReaderData, ReadingDNA
from reading_dna.pipeline import build_shelves

SAMPLE_DIR = Path(__file__).resolve().parent / "data"
MAX_UPLOAD_BYTES = 2_000_000
MAX_RUNS_PER_SESSION = int(os.environ.get("READING_DNA_MAX_RUNS", "3"))

st.set_page_config(page_title="Reading DNA", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)


def api_key() -> str | None:
    try:
        return st.secrets.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    except Exception:                         # no secrets file locally
        return os.environ.get("ANTHROPIC_API_KEY")


@st.cache_data(show_spinner=False)
def sample_reader() -> ReaderData:
    return (parse_goodreads_csv((SAMPLE_DIR / "sample_goodreads.csv").read_text())
            .merge(parse_kindle_clippings((SAMPLE_DIR / "sample_clippings.txt").read_text())))


@st.cache_data(ttl=3600, show_spinner=False)
def goodreads_reader(link: str) -> ReaderData:
    return fetch_profile(link)                # cached an hour: one fetch per profile, not per click


ss = st.session_state
if "sid" not in ss:
    ss.sid, ss.runs = uuid.uuid4().hex[:12], 0
    log_event(ss.sid, "app_opened")

has_key = bool(api_key())
with st.sidebar:
    mock = st.toggle("Demo mode (no AI calls)", value=not has_key, disabled=not has_key,
                     help="Demo mode shows pre-written example picks so you can see the flow for free.")
    st.caption(f"Runs left this session: {MAX_RUNS_PER_SESSION - ss.runs}")
    st.caption("Your reading history is used for this session only and never stored. "
               "We log anonymous events, like 'shelves built', to improve the product.")


def backends():
    if mock:
        return MockLLM(), cat.FakeCatalog()
    return AnthropicLLM(api_key=api_key()), cat.OpenLibraryCatalog()


def read_upload(f) -> str | None:
    if f is None:
        return None
    if f.size > MAX_UPLOAD_BYTES:
        raise ValueError("That file is over 2 MB. Upload a smaller export.")
    return f.getvalue().decode("utf-8", errors="replace")


# ------------------------------------------------------------------ hero
hero_strip = None if ss.get("dna") else ui.spine_strip(sample_reader(), ReadingDNA.model_validate(MOCK_DNA))
st.markdown(ui.hero(hero_strip), unsafe_allow_html=True)
if mock:
    st.info("Demo mode: the shelves show pre-written example picks, not live AI. "
            "They match the sample library, so start with that.")

# ------------------------------------------------------------------ 1. bring your books
st.markdown(ui.step(1, "Bring your books",
                    "Link your Goodreads profile, upload an export, or just type a few favourites."),
            unsafe_allow_html=True)
t_link, t_csv, t_kindle, t_typed = st.tabs(
    ["Goodreads profile link", "Goodreads export", "Kindle highlights", "Type a few books"])
with t_link:
    link = st.text_input("Your Goodreads profile link", placeholder="https://www.goodreads.com/user/show/12345678-your-name",
                         help="Open Goodreads, click your picture, then Profile, and copy the address. "
                              "Your profile needs to be public.")
with t_csv:
    gr_file = st.file_uploader("Goodreads library export (.csv)", type=["csv"],
                               help="Goodreads: My Books, then Import and export, then Export Library.")
with t_kindle:
    kindle_file = st.file_uploader("My Clippings.txt from your Kindle", type=["txt"],
                                   help="Connect your Kindle by USB and open the documents folder.")
with t_typed:
    typed = st.text_area("Books you loved, one per line",
                         placeholder="Dune by Frank Herbert\nThe Road by Cormac McCarthy")
use_sample = st.checkbox("Use a sample library instead")

if st.button("Read my library", type="primary"):
    reader, sources = ReaderData(), []
    try:
        if use_sample:
            reader, sources = reader.merge(sample_reader()), sources + ["sample"]
        if link.strip():
            with st.spinner("Reading your Goodreads shelves..."):
                reader = reader.merge(goodreads_reader(link.strip()))
            sources.append("goodreads_link")
        if (text := read_upload(gr_file)):
            reader, sources = reader.merge(parse_goodreads_csv(text)), sources + ["goodreads_csv"]
        if (text := read_upload(kindle_file)):
            reader, sources = reader.merge(parse_kindle_clippings(text)), sources + ["kindle"]
        if typed.strip():
            reader, sources = reader.merge(parse_manual_list(typed)), sources + ["typed"]
    except GoodreadsError as e:
        log_event(ss.sid, "goodreads_link_failed", reason=type(e).__name__)
        st.error(str(e))
        st.stop()
    except ValueError as e:
        st.error(str(e))
        st.stop()
    except Exception:
        st.error("Goodreads didn't respond. Try again in a minute, or upload your Goodreads export instead.")
        st.stop()

    if not reader.books:
        st.warning("Add some books first: paste your Goodreads link, upload a file, or type a few titles.")
    elif ss.runs >= MAX_RUNS_PER_SESSION:
        st.warning("You've used all the runs for this session. Come back later to try again.")
    else:
        ss.runs += 1
        log_event(ss.sid, "data_uploaded", books=len(reader.books),
                  highlights=len(reader.highlights), sources=sources)
        with st.spinner("Reading your reading..."):
            llm, _ = backends()
            try:
                dna, _ = build_dna(reader, llm)
            except Exception:
                st.error("We couldn't analyse your library just now. Try again in a minute.")
                st.stop()
        ss.reader, ss.dna, ss.result = reader, dna, None
        log_event(ss.sid, "dna_built", themes=len(dna.themes), stances=len(dna.stances))
        st.rerun()

# ------------------------------------------------------------------ 2. reading DNA
if ss.get("dna"):
    dna: ReadingDNA = ss.dna
    reader: ReaderData = ss.reader
    rated = sum(1 for b in reader.books if b.my_rating)
    st.markdown(ui.step(2, "Your Reading DNA",
                        f"From {len(reader.books)} books ({rated} rated) and {len(reader.highlights)} highlights. "
                        "Untick anything that isn't you. Your shelves only use what you keep."),
                unsafe_allow_html=True)
    st.markdown(ui.spine_strip(reader, dna) + ui.legend(dna)
                + f'<div class="rd-summary">{dna.summary}</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("**Themes**")
        keep_themes = [t for i, t in enumerate(dna.themes)
                       if st.checkbox(f"{t.name} ({t.strength})", True, key=f"t{i}",
                                      help="From: " + ", ".join(t.evidence))]
        if dna.style_likes:
            st.markdown("**You like** " + ", ".join(dna.style_likes))
    with c2:
        st.markdown("**Ideas you've absorbed**")
        st.caption("The Steelman shelf finds the best books arguing against these.")
        keep_stances = [s for i, s in enumerate(dna.stances)
                        if st.checkbox(s.claim, True, key=f"s{i}", help="From: " + ", ".join(s.source_books))]

    if st.button("Build my shelves", type="primary"):
        edited = dna.model_copy(update={"themes": keep_themes, "stances": keep_stances})
        removed = len(dna.themes) + len(dna.stances) - len(keep_themes) - len(keep_stances)
        if removed:
            log_event(ss.sid, "dna_edited", removed=removed)
        with st.status("Building your shelves", expanded=True) as status:
            def show(step_info):
                status.write(ui.progress_line(step_info))
            llm, catalog = backends()
            try:
                ss.result = asyncio.run(build_shelves(reader, edited, llm, catalog, n=5, on_step=show))
            except Exception:
                status.update(label="Something went wrong", state="error")
                st.error("Building the shelves failed partway through. Try again in a minute.")
                st.stop()
            status.update(label="Your shelves are ready", state="complete", expanded=False)
        log_event(ss.sid, "shelves_built",
                  **{f"{k}_kept": v.get("kept", 0) for k, v in ss.result["metrics"].items()})

# ------------------------------------------------------------------ 3. shelves
if ss.get("result"):
    res = ss.result
    st.markdown(ui.step(3, "Your shelves"), unsafe_allow_html=True)
    cols = st.columns(2, gap="large")
    for col, kind in zip(cols, ("similar", "steelman")):
        with col:
            st.markdown(ui.shelf_head(kind), unsafe_allow_html=True)
            if not res["shelves"][kind]:
                st.markdown('<div class="rd-empty">No picks passed our checks this time. '
                            "Try again, or add more books so there's more to go on.</div>",
                            unsafe_allow_html=True)
            for i, r in enumerate(res["shelves"][kind]):
                st.markdown(ui.book_card(r, kind), unsafe_allow_html=True)
                b1, b2, _ = st.columns([1, 1, 2])
                if b1.button("I'd read this", key=f"{kind}{i}y"):
                    log_event(ss.sid, "rec_feedback", shelf=kind, title=r["title"], verdict="want")
                    st.toast("Noted")
                if b2.button("Not for me", key=f"{kind}{i}n"):
                    log_event(ss.sid, "rec_feedback", shelf=kind, title=r["title"], verdict="no")
                    st.toast("Noted")

    with st.expander("How these were picked"):
        proposed = sum(m.get("proposed", 0) for m in res["metrics"].values())
        kept = sum(m.get("kept", 0) for m in res["metrics"].values())
        st.write(f"The agents proposed {proposed} books and {kept} passed every check: "
                 "the book exists, you haven't read it, and its reason points to books you actually read.")
        rejected = ui.rejection_summary(res["rejected"])
        if rejected:
            st.write("Left out:")
            st.markdown("\n".join(f"- {line}" for line in rejected))
        st.caption("Every search and check the agents made:")
        st.json(res["trace"], expanded=False)
