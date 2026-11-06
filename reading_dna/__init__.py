"""Reading DNA: a book-recommendation agent with a 'similar' shelf and a 'steelman' shelf.

Run order (each module is one stage of the workflow):
    ingest.py      -> parse the reader's data (deterministic, no LLM)
    catalog.py     -> talk to a book catalog (Open Library, or an offline fake)
    mcp_server.py  -> expose catalog tools over MCP
    dna.py         -> LLM step: turn reading history into a structured Reading DNA
    agent.py       -> the agent loop: LLM + MCP tools -> a shelf of recommendations
    verifier.py    -> deterministic checks: real book? already read? grounded reason?
    pipeline.py    -> glue: runs all of the above end to end
"""
