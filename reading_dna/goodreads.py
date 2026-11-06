"""Read a Goodreads profile from its link, via the public shelf RSS feeds.

    https://www.goodreads.com/user/show/57146668-alex-biswas  ->  user id 57146668
    -> https://www.goodreads.com/review/list_rss/57146668?shelf=read&page=1 ...

Goodreads publishes these feeds for feed readers. We only fetch them when a user pastes
their own link: a handful of requests per user, cached, never crawling. Private profiles
don't expose their shelves, and we tell the user that plainly. The CSV export stays as the
reliable fallback, because Goodreads can change or block these feeds at any time.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Callable

from .models import Book, ReaderData

FEED = "https://www.goodreads.com/review/list_rss/{uid}"
SHELVES = ("read", "currently-reading", "to-read")
HttpGet = Callable[[str, dict], tuple[int, str]]


class GoodreadsError(Exception):
    """Base class. str(e) is a user-facing message."""


class GoodreadsPrivateError(GoodreadsError):
    pass


class GoodreadsEmptyError(GoodreadsError):
    pass


def parse_profile_id(text: str) -> str | None:
    """Accept a profile link, a shelf/RSS link, or a bare numeric id."""
    text = (text or "").strip()
    if text.isdigit():
        return text
    m = re.search(r"goodreads\.com/(?:user/show|review/list(?:_rss)?)/(\d+)", text)
    return m.group(1) if m else None


def _default_get(url: str, params: dict) -> tuple[int, str]:
    import requests
    r = requests.get(url, params=params, timeout=15,
                     headers={"User-Agent": "ReadingDNA/0.2 (personal book recommender)"})
    return r.status_code, r.text


def _text(item: ET.Element, tag: str) -> str:
    node = item.find(f".//{tag}")
    return (node.text or "").strip() if node is not None and node.text else ""


def _int(raw: str) -> int | None:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _date(raw: str) -> str:
    try:
        return parsedate_to_datetime(raw).date().isoformat() if raw else ""
    except (TypeError, ValueError):
        return ""


def _parse_feed(body: str) -> list[ET.Element] | None:
    """Return the feed's items, or None if this isn't an RSS feed at all
    (what we get back when a profile is private or the link is wrong)."""
    try:
        root = ET.fromstring(body.strip().encode("utf-8"))
    except ET.ParseError:
        return None
    channel = root.find("channel")
    return None if channel is None else channel.findall("item")


def _book(item: ET.Element, shelf: str) -> Book:
    return Book(
        title=_text(item, "title"),
        author=_text(item, "author_name"),
        my_rating=_int(_text(item, "user_rating")) or 0,
        shelf=shelf,
        date_read=_date(_text(item, "user_read_at")),
        isbn=re.sub(r"[^0-9Xx]", "", _text(item, "isbn13") or _text(item, "isbn")),
        pages=_int(_text(item, "num_pages")),
        year=_int(_text(item, "book_published")),
    )


def fetch_profile(link: str, http_get: HttpGet | None = None, max_pages: int = 10) -> ReaderData:
    uid = parse_profile_id(link)
    if not uid:
        raise GoodreadsError("That doesn't look like a Goodreads profile link. It should look like "
                             "goodreads.com/user/show/12345678-your-name.")
    get = http_get or _default_get
    books: list[Book] = []
    for shelf in SHELVES:
        seen_first = None
        for page in range(1, max_pages + 1):
            status, body = get(FEED.format(uid=uid), {"shelf": shelf, "page": page})
            items = _parse_feed(body) if status == 200 else None
            if items is None:
                if shelf == SHELVES[0] and page == 1:
                    raise GoodreadsPrivateError(
                        "We couldn't read this profile's shelves. That usually means the profile "
                        "is private. Make it public in Goodreads (Account settings, then Privacy), "
                        "or upload your Goodreads export instead.")
                break
            if not items or _text(items[0], "title") == seen_first:
                break
            seen_first = _text(items[0], "title")
            books += [_book(i, shelf) for i in items if _text(i, "title")]
    if not books:
        raise GoodreadsEmptyError(
            "This profile has no books on its public shelves. If your shelves are hidden, make "
            "them public in Goodreads, or upload your Goodreads export instead.")
    return ReaderData(books=books)
