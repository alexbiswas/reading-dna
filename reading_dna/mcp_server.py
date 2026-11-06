"""The MCP server: our book catalog, exposed as tools any MCP client can use.

Why MCP instead of plain Python functions?
  * One definition, many clients: our agent, the Streamlit app, *and* Claude desktop
    (or any other MCP host) can all use the same tools.
  * The tool's name, description and input schema are generated from the function
    signature + docstring. The LLM reads exactly that, so docstrings ARE prompts. Write them well.

Note what is NOT here: the reader's data. The server is stateless and knows nothing about
any user, so it's safe to share publicly. "Has this user already read it?" lives in verifier.py.

Run standalone (e.g. for Claude desktop):   python -m reading_dna.mcp_server
Run against the offline catalog:            READING_DNA_OFFLINE=1 python -m reading_dna.mcp_server
"""
from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from . import catalog as cat


def build_server(catalog: cat.Catalog) -> MCPServer:
    server = MCPServer(
        "reading-dna-catalog",
        instructions="Book catalog tools. Use search_books to find candidates, "
                     "get_book_details to learn what a book is about, and verify_book "
                     "before recommending anything.",
    )

    @server.tool()
    def search_books(query: str, limit: int = 5) -> list[dict]:
        """Search the book catalog by title, author or topic (e.g. 'antitrust monopoly economics').
        Returns up to `limit` books with work_id, title, author, first publication year and subjects."""
        return catalog.search(query, limit=max(1, min(limit, 10)))

    @server.tool()
    def get_book_details(work_id: str) -> dict:
        """Get the description and subjects of one book, by the work_id returned from search_books.
        Use this to check what a book actually argues before recommending it."""
        return catalog.details(work_id) or {"error": f"No book with work_id {work_id}"}

    @server.tool()
    def verify_book(title: str, author: str = "") -> dict:
        """Check that a book with this exact title (and author) really exists in the catalog.
        Always call this before recommending a book. Returns exists=true/false plus catalog data."""
        return cat.verify(catalog, title, author)

    return server


def default_catalog() -> cat.Catalog:
    if os.environ.get("READING_DNA_OFFLINE"):
        return cat.FakeCatalog()
    return cat.OpenLibraryCatalog()


if __name__ == "__main__":
    build_server(default_catalog()).run("stdio")
