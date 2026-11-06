from pathlib import Path

import pytest

from reading_dna.ingest import parse_goodreads_csv, parse_kindle_clippings, parse_manual_list
from reading_dna.models import normalize_title

DATA = Path(__file__).resolve().parent.parent / "data"


def test_goodreads_sample_parses():
    r = parse_goodreads_csv((DATA / "sample_goodreads.csv").read_text())
    assert len(r.books) == 14
    dune = next(b for b in r.books if b.title == "Dune")
    assert dune.author == "Frank Herbert" and dune.my_rating == 5 and dune.date_read == "2022-05-30"
    assert {b.shelf for b in r.books} == {"read", "currently-reading"}


def test_goodreads_rejects_wrong_file():
    with pytest.raises(ValueError):
        parse_goodreads_csv("name,email\nalex,a@b.c\n")


def test_isbn_cleaning():
    csv_text = 'Title,Author,ISBN13,My Rating,Exclusive Shelf\nDune,Frank Herbert,"=""9780441013593""",4,read\n'
    assert parse_goodreads_csv(csv_text).books[0].isbn == "9780441013593"


def test_kindle_dedupes_and_skips_bookmarks():
    r = parse_kindle_clippings((DATA / "sample_clippings.txt").read_text())
    zero = [h for h in r.highlights if h.book_title.startswith("Zero to One")]
    assert len(zero) == 1                       # duplicate removed, bookmark skipped
    assert zero[0].author == "Peter Thiel"
    assert len(r.highlights) == 7


def test_manual_list_formats():
    r = parse_manual_list("Dune by Frank Herbert\nThe Road - Cormac McCarthy\n\nWatchmen")
    assert [(b.title, b.author) for b in r.books] == [
        ("Dune", "Frank Herbert"), ("The Road", "Cormac McCarthy"), ("Watchmen", "")]
    assert all(b.my_rating == 5 for b in r.books)


@pytest.mark.parametrize("raw,expected", [
    ("The Road: A Novel", "road"),
    ("Zero to One: Notes on Startups", "zero to one"),
    ("Do Androids Dream of Electric Sheep?", "do androids dream of electric sheep"),
    ("Dune (Dune #1)", "dune"),
])
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected
