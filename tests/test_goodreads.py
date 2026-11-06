"""Goodreads link import, tested against a fake HTTP layer (no network)."""
import pytest

from reading_dna.goodreads import (GoodreadsEmptyError, GoodreadsError, GoodreadsPrivateError,
                                   fetch_profile, parse_profile_id)

ITEM = """<item><title>{title}</title><book_id>1</book_id><author_name>{author}</author_name>
<isbn13>9780441013593</isbn13><user_rating>{rating}</user_rating>
<user_read_at>Mon, 30 May 2022 00:00:00 -0700</user_read_at><book_published>1965</book_published>
<book><num_pages>658</num_pages></book></item>"""


def feed(*items):
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>shelf</title>{"".join(items)}</channel></rss>'


def fake_get(pages: dict):
    """pages: {(shelf, page): (status, body)}; anything missing returns an empty feed."""
    calls = []
    def get(url, params):
        calls.append((url, params["shelf"], params["page"]))
        return pages.get((params["shelf"], params["page"]), (200, feed()))
    get.calls = calls
    return get


@pytest.mark.parametrize("text,uid", [
    ("https://www.goodreads.com/user/show/57146668-alex-biswas", "57146668"),
    ("goodreads.com/review/list/57146668?shelf=read", "57146668"),
    ("https://www.goodreads.com/review/list_rss/57146668?key=abc&shelf=read", "57146668"),
    ("57146668", "57146668"),
    ("https://example.com/user/show/1", None),
])
def test_parse_profile_id(text, uid):
    assert parse_profile_id(text) == uid


def test_reads_all_shelves_and_paginates():
    get = fake_get({
        ("read", 1): (200, feed(ITEM.format(title="Dune", author="Frank Herbert", rating=5))),
        ("read", 2): (200, feed(ITEM.format(title="The Road", author="Cormac McCarthy", rating=4))),
        ("to-read", 1): (200, feed(ITEM.format(title="Exhalation", author="Ted Chiang", rating=0))),
    })
    r = fetch_profile("https://www.goodreads.com/user/show/57146668-alex-biswas", http_get=get)
    assert [(b.title, b.shelf) for b in r.books] == [("Dune", "read"), ("The Road", "read"), ("Exhalation", "to-read")]
    dune = r.books[0]
    assert dune.my_rating == 5 and dune.date_read == "2022-05-30" and dune.pages == 658 and dune.year == 1965
    assert "57146668" in get.calls[0][0]


def test_private_profile_gets_a_clear_reason():
    for response in [(403, "Forbidden"), (200, "<html><body>This profile is private</body></html>")]:
        with pytest.raises(GoodreadsPrivateError, match="private"):
            fetch_profile("57146668", http_get=fake_get({("read", 1): response}))


def test_empty_shelves():
    with pytest.raises(GoodreadsEmptyError):
        fetch_profile("57146668", http_get=fake_get({}))


def test_bad_link():
    with pytest.raises(GoodreadsError, match="profile link"):
        fetch_profile("https://www.goodreads.com/book/show/234225", http_get=fake_get({}))


def test_repeated_page_stops_pagination():
    same = (200, feed(ITEM.format(title="Dune", author="Frank Herbert", rating=5)))
    get = fake_get({("read", p): same for p in range(1, 11)})
    r = fetch_profile("57146668", http_get=get)
    assert len([b for b in r.books if b.title == "Dune"]) == 1
