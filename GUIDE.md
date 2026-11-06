# How Reading DNA is built: a learning guide

This guide walks through the project in the order the code runs. For each piece it covers what it does, why it's built that way, and which file to open. Read it with the code side by side.

---

## 1. The big picture: a workflow with agents inside

```
  Goodreads CSV ─┐
  Kindle clips  ─┼─► [1 Ingest] ─► [2 Reading DNA] ─► [3a Similar agent] ──┐
  Typed list    ─┘    plain code     1 LLM call        [3b Steelman agent] ─┼─► [4 Verifier] ─► shelves
                                                            │      ▲          │    plain code
                                                            ▼      │          │
                                                       [MCP server: book catalog tools]
```

| Stage | File | Uses an LLM? | Why built this way |
|---|---|---|---|
| 1. Ingest | `ingest.py` | No | Parsing files is a job for plain code: cheap, exact, testable |
| 2. Reading DNA | `dna.py` | 1 call | Fixed input, one well-shaped answer. That's a workflow step, not an agent |
| 3. Shelves | `agent.py` | Loop | Finding books is open-ended: search, read, compare, verify. That needs an agent |
| Tools | `mcp_server.py` | No | One tool definition shared by our agent, the app, and Claude desktop |
| 4. Verifier | `verifier.py` | No | Rules that must always hold get enforced in code, not just requested in a prompt |
| Glue | `pipeline.py` | No | Runs the stages in order |

**The key design idea:** use an agent only where you need open-ended exploration. Everything else is deterministic code or a single structured LLM call. The result is cheaper, faster, easier to debug and, most importantly, easier to *evaluate*. "Why not one big autonomous agent?" is a question you should expect in an interview, and this table is your answer.

---

## 2. Stage by stage

### 2.1 Ingestion (`ingest.py`)
Three parsers, one for each onboarding path:
- **Goodreads CSV:** cleans Goodreads' odd `="0441013597"` ISBN format and normalises dates.
- **Kindle `My Clippings.txt`:** splits on `==========`, skips bookmarks, and removes duplicates (Kindle saves a highlight again every time you edit it).
- **Typed list:** "Dune by Frank Herbert". This is the cold-start path, because most users don't have an export.

All three produce the same `ReaderData` shape (`models.py`), so nothing downstream cares where the data came from.

**Lesson:** never pay an LLM to do what a regex can do reliably.

### 2.2 Tools over MCP (`catalog.py`, `mcp_server.py`)
**What MCP is:** a standard way for a program (the *server*) to offer tools, and for an AI app (the *client*) to discover and call them. Our server offers three tools:

| Tool | What it does |
|---|---|
| `search_books(query, limit)` | Keyword or topic search in Open Library |
| `get_book_details(work_id)` | Description and subjects, so the agent can check what a book actually argues |
| `verify_book(title, author)` | Does this exact book exist? The main defence against hallucinated books |

Things to notice in `mcp_server.py`:
- **Docstrings are prompts.** MCP generates each tool's description and input schema from the Python function. The LLM reads exactly that text, so "Always call this before recommending a book" in the docstring *is* an instruction to the agent.
- **The server is stateless.** It knows nothing about any user. "Has this person already read it?" belongs in the verifier. That makes the server safe to share publicly.
- **Catalogs are swappable.** `OpenLibraryCatalog` (real) and `FakeCatalog` (an offline JSON file) share an interface. That's what lets the tests run without network access.

**Three ways to connect to the same server**, with no agent code changes:
```python
Client(build_server(catalog))                                   # in-process (tests, app)
Client(StdioServerParameters(command="python",
       args=["-m", "reading_dna.mcp_server"]))                  # separate process
Client("https://your-host/mcp")                                 # deployed over HTTP
```
**Use it inside Claude desktop:** add this to Claude desktop's MCP config, and Claude can search and verify books in your normal chats:
```json
{"mcpServers": {"reading-dna-catalog": {
  "command": "python", "args": ["-m", "reading_dna.mcp_server"],
  "env": {"PYTHONPATH": "/absolute/path/to/reading-dna"}}}}
```

**Where's the RAG?** In v1, retrieval happens through tools: the agent searches a live catalog and reads results. That's sometimes called "agentic retrieval". There's no vector database yet, because a typical reader's highlights fit in the prompt. Add embeddings and vector search when a reader has thousands of highlights; that's Exercise 4. Being able to say *why* you didn't add a vector database yet is a good interview answer.

### 2.3 Reading DNA: structured output (`dna.py`)
The trick that makes LLM output reliable: define a tool whose `input_schema` is the `ReadingDNA` pydantic model, then **force** the model to call it with `tool_choice={"type": "tool", "name": "record_reading_dna"}`. You get schema-shaped JSON every time, with no "please reply in JSON" and no parsing free text.

Then a **grounding filter** drops any evidence title that isn't really in the library. LLMs sometimes add plausible-looking evidence, so we check it.

The DNA has three layers:
- **Themes:** what you care about.
- **Style likes and dislikes:** how you like books written.
- **Stances:** positions you've absorbed. The steelman shelf needs these.

### 2.4 The agent loop (`agent.py`, the core)
Strip away the details and an agent is this:
```python
tools = mcp_tools + [submit_shelf]
messages = [user_brief]
for turn in range(max_turns):
    response = llm(system_prompt, messages, tools)
    if response calls submit_shelf:      return validated shelf      # the "finish tool"
    for each tool call:                  result = mcp.call_tool(...)
    messages += [response, tool_results]
```
Patterns in the real code worth learning:
- **Finish tool.** The agent ends by calling `submit_shelf`, whose schema forces structured output. There's no guessing about when it's "done".
- **Self-correction.** If the submission fails validation, the error goes back to the model as a tool result and it tries again (see `test_agent_self_corrects_invalid_submission`).
- **Guardrails.** A turn limit, and one nudge if the model stops without submitting.
- **Trace.** Every tool call is recorded with its input, output and timing. That's your debugger, and it's what the app shows under "Show the agent's work".
- **MCP → LLM tools is 3 lines:** `mcp_tools_to_llm_tools()`. That's the whole integration.

### 2.5 Two shelves, one loop (`prompts.py`)
Both shelves run the *same* loop. Only the system prompt differs:
- **Similar:** match themes and style, vary the authors, cite which of your books motivated each pick.
- **Steelman:** for each stance, find a book that argues the opposite thesis, by a credible author, in good faith, with comparable rigor. The prompt says explicitly what does *not* count: same-author follow-ups, books that agree, polemics.

The steelman shelf runs second and is told what the similar shelf already picked, so the two never overlap.

### 2.6 The verifier (`verifier.py`)
The prompt *asks* the agent to behave. The verifier *enforces* it. Each pick must pass all of these checks:

| Check | Catches |
|---|---|
| `exists` | hallucinated books (via the `verify_book` MCP tool) |
| `unseen` | books you've already read or are reading |
| `grounded` | reasons that cite books you never read |
| `unique` | duplicates across the two shelves |
| `counters` | steelman picks that don't say what they oppose |

Rejections aren't thrown away. They become **eval metrics on every single run** (section 3).

### 2.7 The app (`app.py`) and analytics (`analytics.py`)
Product decisions you can point to:
- Three onboarding paths, with the typed list as the cold-start route.
- **The DNA is shown and editable before any recommendations.** Untick a wrong theme and the shelves ignore it. This builds trust and lets users correct mistakes.
- Every pick shows its reason and "because you read…".
- Cost controls: a per-session run limit, a 2 MB upload cap, and the API key kept on the server only. For a real public launch, also set a monthly spending limit in the Anthropic console, since session limits are easy to get around.
- Privacy: reading history is never stored. Only anonymous funnel events go to `events.jsonl`.

---

## 3. Evals: how you know it works

Think of evals as four layers, from cheap and constant to expensive and occasional:

```
         ┌──────────────────────────┐
         │  Online: real users       │  funnel + "want to read" rate      (analytics.py)
         ├──────────────────────────┤
         │  Human: blind test        │  friends rate pairs, labels hidden (evals: blind)
         ├──────────────────────────┤
         │  Offline quality evals    │  hold-out, LLM judge               (evals: holdout, judge)
         ├──────────────────────────┤
         │  Every run: verifier      │  hallucination / repeats / grounding (evals: verifier)
         ├──────────────────────────┤
         │  Unit tests (no LLM)      │  17 tests, under 1 second          (pytest)
         └──────────────────────────┘
```

### Layer 0: unit tests (`tests/`)
`python -m pytest -q`. These test the plumbing: parsers, MCP tools, the agent loop and the verifier, using `MockLLM` and `FakeCatalog`. No API key, no network, under a second. Run them after every change.

### Layer 1: verifier metrics, *"is it making things up?"*
`python -m evals.run_evals verifier --goodreads your.csv --runs 5`
- Runs the full pipeline several times, because LLM output varies, and averages the verifier's rejection counts.
- **What to watch:** `hallucination_rate` is measured *before* filtering, so it tells you how often the raw model invents books. Users never see those picks, but a high rate means wasted tokens and thin shelves. Also track `kept_similar` and `kept_steelman`: if picks keep getting rejected, the shelves come out short.
- It also reports tokens and seconds per run, which gives you cost per recommendation.

### Layer 2a: hold-out, *"does it actually know my taste?"*
`python -m evals.run_evals holdout --goodreads your.csv --hide 3`
- Hides your most recently read 4–5 star books, builds everything from the rest, and checks whether the similar shelf rediscovers them.
- It reports three levels of hit:
  - **exact:** recommended the hidden book itself
  - **author:** recommended another book by the same author
  - **soft:** recommended a book sharing 2+ catalog subjects with the hidden one
- Exact hits are rare and that's fine. Soft hits are the realistic signal. To know whether a number is good, compare against a baseline, e.g. 10 random popular books.
- It needs real dates and ratings, so it works properly with your real Goodreads export.

### Layer 2b: LLM-as-judge, *"are steelman picks genuinely opposing?"*
`python -m evals.run_evals judge --goodreads your.csv`
- A second LLM call scores each pair (book you read → steelman pick) on four criteria from 1 to 5: opposing thesis, credibility, good faith, rigor match.
- **Evaluate the evaluator first.** Before trusting the judge, it scores `golden_steelman.json`: hand-labelled good and bad pairs, including traps like *Hooked* vs *Indistractable* (same author) and *Predictably Irrational* vs *Nudge* (agrees rather than opposes). If the judge agrees with the human labels less than 85% of the time, the script warns you to fix the judge prompt before trusting its pass rate.
- Add your own labelled pairs over time. The golden set is how your eval gets sharper.

### Layer 3: blind human test, *"do people agree?"*
`python -m evals.run_evals blind --goodreads your.csv`
- Writes `evals/results/blind_test_sheet.csv`: the agent's steelman pairs mixed with known-good and known-bad pairs, shuffled, with no labels. Send it to 3–5 friends to rate 1–5.
- Score it with the private `blind_test_key.csv`. Agent pairs should clearly beat the known-bad decoys and come close to the known-good ones. The decoys tell you whether your raters are paying attention.

### Layer 4: online, *"do real users get value?"*
`analytics.py` logs a funnel: `app_opened → data_uploaded → dna_built → shelves_built → rec_feedback`. Call `funnel()` to count sessions at each step. The north-star metric is "Want to read" clicks per session, and later, books actually finished. That's the metric that reframes this from "a recommender" to "books you finish and love".

### The eval loop you'll actually use
1. Change one thing (a prompt line, a tool docstring, the model).
2. Run `verifier` and `judge`, which takes a few minutes.
3. Compare against the previous lines in `evals/results/*.jsonl`.
4. Keep the change or revert it. Write down why.

**Important:** `--mock` runs test the *plumbing* only. Mock scores are hard-coded and say nothing about quality. Real numbers need the real model and your real data.

---

## 4. Running it

```bash
pip install -r requirements.txt
python -m pytest -q                                              # 17 tests, offline

# Offline demo: no key, no network
python -m reading_dna.pipeline --goodreads data/sample_goodreads.csv --kindle data/sample_clippings.txt --mock

# Real run
export ANTHROPIC_API_KEY=sk-ant-...
python -m reading_dna.pipeline --goodreads path/to/goodreads_library_export.csv

# Web app (falls back to demo mode if no key is set)
streamlit run app.py
```

**Deploy publicly (Streamlit Community Cloud):** push the folder to a GitHub repo, then create a new app pointing at `app.py`. Add `ANTHROPIC_API_KEY` under the app's Secrets, and set a monthly spending limit in the Anthropic console before you share the link. Hugging Face Spaces works the same way.

Pick the model with `READING_DNA_MODEL` (the default is in `llm.py`).

---

## 5. Interview talking points
1. **"Pipeline with agents inside":** autonomy only where search is open-ended, and why.
2. **"Prompts ask, code enforces":** the verifier, and the hallucination rate you measured.
3. **"I evaluated the evaluator":** judge calibration against a human-labelled golden set.
4. **"One MCP server, three clients":** my agent, my app, and Claude desktop.
5. **"Editable DNA before recommendations":** trust, correction, and how many users edited it.
6. **Real numbers:** hold-out soft-hit rate compared with a random baseline, cost per recommendation, funnel conversion.

## 6. Exercises (learn by changing things)
1. Add a `get_books_by_author` MCP tool, then watch the agent start using it without any other code changes.
2. Switch the pipeline from an in-process connection to stdio (`StdioServerParameters`) and confirm the tests still pass.
3. Turn on prompt caching for the DNA block, which is repeated in both shelf runs, and measure the token savings with the verifier eval.
4. Add RAG over highlights: embed each highlight, and for each theme retrieve the top 5 to put in the shelf brief.
5. Decide what the steelman shelf should do with fiction, implement it, and add golden pairs that test it.
6. Run the hold-out eval with a random-books baseline and write up the comparison.
