# Reading DNA

A book-recommendation agent with two shelves:
- **Similar:** more of what moves you, matched to your themes and writing style.
- **Steelman:** the strongest, fairest book arguing against ideas you've absorbed.

Built with the Claude API (tool use), an MCP server over Open Library, a deterministic verifier, and a four-layer eval suite.

**Start with [GUIDE.md](GUIDE.md)**. It explains how every part works and why.

## Quickstart
```bash
pip install -r requirements.txt
python -m pytest -q                       # offline tests
python -m reading_dna.pipeline --goodreads data/sample_goodreads.csv --kindle data/sample_clippings.txt --mock
export ANTHROPIC_API_KEY=sk-ant-...       # then drop --mock for real recommendations
streamlit run app.py                      # web app
```

## Layout
```
reading_dna/
  ingest.py       Goodreads / Kindle / typed-list parsers
  catalog.py      Open Library client + offline fake
  mcp_server.py   MCP tools: search_books, get_book_details, verify_book
  dna.py          Reading DNA (structured output)
  agent.py        the agent loop + shelf builder
  verifier.py     exists / unseen / grounded / unique checks
  pipeline.py     end-to-end runner (CLI)
  prompts.py      every prompt in one place
  mock_llm.py     fake model for offline runs
  analytics.py    funnel events
app.py            Streamlit web app
evals/            verifier, hold-out, LLM judge, blind human test
tests/            17 offline tests
data/             sample data (see data/README.md)
```
