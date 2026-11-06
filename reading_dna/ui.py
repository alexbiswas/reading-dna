"""Visual building blocks for the app: CSS and small HTML components.

Kept separate from app.py so they can be previewed and tested without running Streamlit.
Design tokens:
    paper #F5F6F8   ink #1D2740   muted #5B647A   rule #D5D9E2
    similar (agrees with you) #2F6F62   steelman (argues back) #8A2E3E
    Literata (made for e-readers) for books and prose, Atkinson Hyperlegible for the interface.
The one bold element is the spine strip: a reader's library drawn as book spines,
coloured by the theme each book belongs to.
"""
from __future__ import annotations

from html import escape

from .models import ReaderData, ReadingDNA, normalize_title

# Deliberately excludes the two shelf colours, so teal/oxblood only ever mean "similar"/"steelman".
THEME_COLORS = ["#3E5C9A", "#B07D24", "#6B5B8C", "#2C7C8C", "#A0567A", "#5E7F4F"]
UNTHEMED = "#CDD2DC"
FAILED_REASONS = {
    "exists": "couldn't find this book in the catalog",
    "unseen": "you've already read it",
    "grounded": "its reason cited a book you haven't read",
    "unique": "already on the other shelf",
    "counters": "didn't say which idea it argues against",
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Literata:ital,opsz,wght@0,7..72,400;0,7..72,600;1,7..72,400&family=Atkinson+Hyperlegible:wght@400;700&display=swap');
:root { --paper:#F5F6F8; --ink:#1D2740; --muted:#5B647A; --rule:#D5D9E2;
        --similar:#2F6F62; --steelman:#8A2E3E; --serif:'Literata', Georgia, serif;
        --sans:'Atkinson Hyperlegible', system-ui, sans-serif; }
html, body, .stApp, .stMarkdown, p, label, li, input, textarea, button, [data-baseweb="tab"] {
  font-family: var(--sans); color: var(--ink); }
.block-container { max-width: 1120px; padding-top: 2.5rem; padding-bottom: 4rem; }
#MainMenu, footer { visibility: hidden; }
h1, h2, h3, .rd-serif { font-family: var(--serif) !important; color: var(--ink); letter-spacing: -0.01em; }
.stButton button[kind="primary"] { background: var(--ink); border: 1px solid var(--ink); border-radius: 6px;
  font-weight: 700; padding: 0.55rem 1.2rem; }
.stButton button[kind="primary"]:hover { background: #2B3859; border-color: #2B3859; }
.stButton button:focus-visible, a:focus-visible { outline: 3px solid #F2C94C; outline-offset: 2px; }

.rd-hero-wrap { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 3rem;
  align-items: end; }
.rd-hero h1 { font-size: 3.4rem; line-height: 1.05; margin: 0 0 0.6rem; font-weight: 600; }
.rd-hero p { font-family: var(--serif); font-size: 1.3rem; line-height: 1.55; max-width: 38ch;
  color: var(--ink); margin: 0 0 1.8rem; }
.rd-caption { color: var(--muted); font-size: 0.92rem; margin-top: 0.5rem; max-width: 70ch; }

.rd-step { display: flex; align-items: baseline; gap: 0.9rem; margin: 3rem 0 0.8rem;
  border-top: 1px solid var(--rule); padding-top: 1.6rem; }
.rd-step .n { font-family: var(--serif); font-size: 2.2rem; line-height: 1; color: var(--muted); }
.rd-step h2 { font-size: 1.7rem; margin: 0; padding: 0; font-weight: 600; }
.rd-step p { color: var(--muted); margin: 0.2rem 0 0; }

.rd-strip { display: inline-flex; max-width: 100%; align-items: flex-end; gap: 3px; padding: 0 14px;
  overflow-x: auto; border-bottom: 4px solid var(--ink); min-height: 140px; box-sizing: border-box; }
.rd-spine { flex: 0 0 auto; border-radius: 2px 2px 0 0; display: flex; align-items: center;
  justify-content: center; overflow: hidden; }
.rd-spine span { writing-mode: vertical-rl; transform: rotate(180deg); font-family: var(--serif);
  font-size: 11px; line-height: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  max-height: calc(100% - 10px); color: #fff; }
.rd-spine.plain span { color: var(--ink); }
.rd-spine.reading { opacity: 0.55; }
.rd-legend { display: flex; flex-wrap: wrap; gap: 0.4rem 1.4rem; margin-top: 0.8rem; font-size: 0.95rem; }
.rd-legend i { display: inline-block; width: 12px; height: 12px; border-radius: 2px;
  margin-right: 0.45rem; vertical-align: -1px; }

.rd-summary { font-family: var(--serif); font-size: 1.25rem; line-height: 1.6; max-width: 60ch;
  margin: 1.4rem 0 1rem; }

.rd-shelf-head { border-top: 5px solid; padding-top: 0.9rem; margin-bottom: 0.4rem; }
.rd-shelf-head h3 { font-size: 1.6rem; margin: 0; padding: 0; font-weight: 600; }
.rd-shelf-head p { color: var(--muted); margin: 0.2rem 0 0; }
.rd-shelf-head.similar { border-color: var(--similar); }
.rd-shelf-head.steelman { border-color: var(--steelman); }

.rd-book { display: grid; grid-template-columns: 84px 1fr; gap: 1rem; padding: 1.2rem 0 0.6rem;
  border-bottom: 1px solid var(--rule); }
.rd-cover img, .rd-cover .ph { width: 84px; height: 126px; border-radius: 2px 4px 4px 2px; display: block;
  object-fit: cover; box-shadow: inset 4px 0 0 rgba(0,0,0,0.18); }
.rd-cover .ph { display: flex; flex-direction: column; justify-content: space-between; padding: 8px 7px 7px 11px;
  box-sizing: border-box; color: #fff; font-family: var(--serif); font-size: 12px; line-height: 1.2; }
.rd-cover .ph small { font-family: var(--sans); font-size: 10px; opacity: 0.85; }
.similar .ph { background: var(--similar); }
.steelman .ph { background: var(--steelman); }
.rd-title { font-family: var(--serif); font-size: 1.2rem; font-weight: 600; line-height: 1.3; }
.rd-author { color: var(--muted); margin: 0.1rem 0 0.5rem; }
.rd-counters { font-family: var(--serif); font-style: italic; color: var(--steelman); margin-bottom: 0.4rem; }
.rd-reason { margin: 0 0 0.45rem; line-height: 1.55; }
.rd-because { color: var(--muted); font-size: 0.92rem; }
.rd-because em { font-family: var(--serif); color: var(--ink); }
.rd-tag { display: inline-block; margin-top: 0.4rem; font-size: 0.85rem; padding: 0.1rem 0.5rem;
  border-radius: 3px; background: #F6E7A6; color: #5A4A0E; }
.rd-empty { color: var(--muted); padding: 1.2rem 0; font-family: var(--serif); }
@media (max-width: 820px) { .rd-hero-wrap { grid-template-columns: minmax(0, 1fr); gap: 1.5rem; } }
@media (max-width: 640px) { .rd-hero h1 { font-size: 2.5rem; } .rd-hero p { font-size: 1.12rem; } }
</style>
"""


def theme_colors(dna: ReadingDNA) -> dict[str, str]:
    return {t.name: THEME_COLORS[i % len(THEME_COLORS)] for i, t in enumerate(dna.themes)}


def _book_colors(dna: ReadingDNA) -> dict[str, str]:
    """normalized title -> colour of the first (strongest) theme that cites it."""
    colours = theme_colors(dna)
    out: dict[str, str] = {}
    for t in dna.themes:
        for title in t.evidence:
            out.setdefault(normalize_title(title), colours[t.name])
    return out


def spine_strip(reader: ReaderData, dna: ReadingDNA | None = None, max_books: int = 90) -> str:
    colours = _book_colors(dna) if dna else {}
    books = [b for b in reader.books if b.shelf in ("read", "currently-reading")]
    books.sort(key=lambda b: (b.shelf != "read", b.date_read or "9999"))
    spines = []
    for b in books[:max_books]:
        pages = min(b.pages or 300, 800)
        height, width = 78 + pages / 800 * 58, 16 + pages / 800 * 14
        colour = colours.get(normalize_title(b.title))
        classes = "rd-spine" + ("" if colour else " plain") + (" reading" if b.shelf != "read" else "")
        tip = f"{b.title} by {b.author}" + (" (reading now)" if b.shelf != "read" else "")
        spines.append(
            f'<div class="{classes}" title="{escape(tip)}" style="height:{height:.0f}px;width:{width:.0f}px;'
            f'background:{colour or UNTHEMED}"><span>{escape(b.title.split(":")[0])}</span></div>')
    return f'<div class="rd-strip" role="img" aria-label="Your library as book spines">{"".join(spines)}</div>'


def legend(dna: ReadingDNA) -> str:
    items = "".join(f'<span><i style="background:{c}"></i>{escape(name)}</span>'
                    for name, c in theme_colors(dna).items())
    return f'<div class="rd-legend">{items}<span><i style="background:{UNTHEMED}"></i>Other books</span></div>'


def hero(strip_html: str | None = None) -> str:
    """The strip is the sample reader's library; pass None once the user has their own."""
    text = ('<div class="rd-hero"><h1>Reading DNA</h1>'
            "<p>Show us what you've read. Get two shelves back: more of what moves you, "
            "and the strongest case against what you believe.</p></div>")
    if not strip_html:
        return text
    return (f'<div class="rd-hero-wrap">{text}<div>{strip_html}'
            '<div class="rd-caption">A sample reader\'s library. Each spine is a book they\'ve read, '
            "coloured by the theme it belongs to. Yours will look different.</div></div></div>")


def step(n: int, title: str, sub: str = "") -> str:
    sub_html = f"<p>{escape(sub)}</p>" if sub else ""
    return f'<div class="rd-step"><span class="n">{n}</span><div><h2>{escape(title)}</h2>{sub_html}</div></div>'


def shelf_head(kind: str) -> str:
    if kind == "similar":
        return ('<div class="rd-shelf-head similar"><h3>Similar</h3>'
                "<p>More of what moves you.</p></div>")
    return ('<div class="rd-shelf-head steelman"><h3>Steelman</h3>'
            "<p>The strongest, fairest case against ideas you've absorbed.</p></div>")


def _join_titles(titles: list[str]) -> str:
    t = [f"<em>{escape(x)}</em>" for x in titles]
    return t[0] if len(t) == 1 else ", ".join(t[:-1]) + " and " + t[-1]


def book_card(rec: dict, kind: str) -> str:
    title, author = escape(rec["title"]), escape(rec["author"])
    if rec.get("cover_url"):
        cover = f'<img src="{escape(rec["cover_url"])}" alt="" loading="lazy">'
    else:
        cover = f'<div class="ph"><span>{title}</span><small>{author}</small></div>'
    year = f", {rec['year']}" if rec.get("year") else ""
    counters = (f'<div class="rd-counters">Argues against: {escape(rec["counters"])}</div>'
                if kind == "steelman" and rec.get("counters") else "")
    because = (f'<div class="rd-because">Because you read {_join_titles(rec["because_of"])}</div>'
               if rec.get("because_of") else "")
    tag = '<span class="rd-tag">Already on your to-read list</span>' if rec.get("on_to_read") else ""
    return (f'<div class="rd-book {kind}"><div class="rd-cover">{cover}</div><div>'
            f'<div class="rd-title">{title}</div><div class="rd-author">{author}{year}</div>'
            f'{counters}<p class="rd-reason">{escape(rec["reason"])}</p>{because}{tag}</div></div>')


def rejection_summary(rejected: dict) -> list[str]:
    lines = []
    for recs in rejected.values():
        for r in recs:
            why = "; ".join(FAILED_REASONS.get(f, f) for f in r["failed"])
            lines.append(f"{r['title']}: {why}")
    return lines


def progress_line(step_info: dict) -> str:
    shelf = "Similar" if step_info.get("shelf") == "similar" else "Steelman"
    args = step_info.get("input", {})
    if step_info["tool"] == "search_books":
        return f"{shelf} shelf: searching for “{args.get('query', '')}”"
    if step_info["tool"] == "verify_book":
        return f"{shelf} shelf: checking “{args.get('title', '')}” exists"
    return f"{shelf} shelf: reading about a candidate"
