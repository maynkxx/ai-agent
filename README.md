# University Intelligence Database Agent

An AI-powered scraping agent that builds a **structured, queryable database** of
university intelligence — about, tuition, living costs, scholarships, acceptance
rates, graduate employment, salaries, visa policies, intake deadlines and course
listings — across multiple institutions, optimised for **accuracy over breadth**.

Three universities (**MIT**, **University of Toronto**, **UC Berkeley**) are scraped end-to-end;
adding more is a zero-code YAML drop-in.

> Current eval score: **85.0% overall** (MIT 100%, Berkeley 85%, UofT 70%) — see
> [`eval/REPORT.md`](eval/REPORT.md).

---

## Setup

```bash
# 1. Install dependencies (pure-Python, no heavy ML libs)
pip install -r requirements.txt

# 2. Set a free Groq API key — the model runs in the CLOUD, light on your machine
export GROQ_API_KEY=...            # get one at https://console.groq.com/keys

# 3. Run the agent
python3 cli.py run

# 4. Read the results
python3 cli.py show mit
cat data/output.json
```

With **no key**, the agent still runs and produces partial data via a
deterministic regex fallback (graceful degradation): `python3 cli.py run --no-llm`.

A ready-made `data/output.json` + CSV sample ships in the repo, so the evaluator
runs out of the box:

```bash
python3 eval/evaluate.py           # score output against ground truth
python3 -m pytest -q               # 30 unit tests, no network required
```

### One-command Docker (bonus)

```bash
docker build -t uniagent .
docker run --rm -e GROQ_API_KEY=$GROQ_API_KEY -v "$PWD/data:/app/data" uniagent
```

The mounted `./data` volume persists `output.json`, the CSVs and the SQLite DB
back to the host. With no key the container still runs (regex fallback). Query or
evaluate by overriding the command:

```bash
docker run --rm -v "$PWD/data:/app/data" uniagent python3 cli.py list
docker run --rm -v "$PWD/data:/app/data" uniagent python3 eval/evaluate.py
```

### Why Groq (and not Ollama)

A local model (Ollama) is free but **heavy** — it can lock up a laptop. Groq's
free tier runs a small model (`llama-3.1-8b-instant`) in the cloud, so the
compute stays off your machine. If Groq is unreachable or no key is set, the
extractor falls back to regex heuristics instead of crashing.

Override the model: `python3 cli.py run --model llama-3.1-8b-instant`.

---

## Architecture

A linear pipeline of five single-responsibility modules. Each is small, typed,
commented, and unit-tested in isolation.

```
            ┌─────────┐   ┌──────────┐   ┌───────────┐   ┌───────────┐   ┌──────────┐
 YAML  ───► │ Planner │─► │ Scraper  │─► │ Extractor │─► │ Validator │─► │ Storage  │ ──► JSON / CSV
 config     └─────────┘   └──────────┘   └───────────┘   └───────────┘   └──────────┘      + SQLite
            which pages    fetch (retry,  HTML text ->     plausibility    SQLite +
            in what order  robots, cache, structured       + currency      exports
            + adaptive     JS render,     JSON via Groq     cross-checks
            link discovery pagination)    (or heuristics)   + confidence
```

The four design pillars from the brief, and where they live:

- **Planning loop** — `planner.py`: ordered seed tasks per field *plus* adaptive
  link discovery (`Planner.discover`) that mines a page's links when a seed is
  missing or dead. `pipeline.py` triggers discovery for any field without a seed.
- **Self-validation** — `validator.py`: plausibility envelopes (a founding year
  isn't 3025; a % is 0–100), currency-vs-country cross-checks (USD for MIT/Berkeley,
  CAD for UofT), cross-source corroboration → a single confidence score + flags. It
  **never deletes** data; it flags low-confidence values for review.
- **Resilience** — `scraper.py`: retry with exponential back-off, `robots.txt`
  (via Protego), per-host rate limiting, on-disk caching, optional Playwright JS
  rendering, pagination following. Every field runs in its own `try/except` so
  one failure degrades to partial data, never a crash.
- **Incremental updates** — content-hash page cache (`storage.page_cache`); a
  re-run with `--incremental` skips the LLM call for pages that haven't changed.

### Project layout

```
uniagent/
  config.py       central typed settings (paths, HTTP knobs, Groq LLM backend)
  planner.py      seed tasks + adaptive link discovery
  scraper.py      polite/resilient fetcher (retry, robots, cache, JS, pagination)
  extractor.py    Groq extraction (per-field JSON schemas) + regex fallback
  prompts.py      per-field prompts + JSON schema hints (the accuracy lever)
  validator.py    plausibility, cross-checks, confidence + flags
  schema.py       dataclasses for the 10 fields + provenance wrappers
  storage.py      SQLite (queryable) + JSON/CSV exporters
  pipeline.py     wires it all together with graceful degradation
  llm.py          Groq chat client (OpenAI-compatible)
cli.py            run + query commands
api.py            FastAPI query layer (bonus)
universities/     plug-in configs (mit.yaml, uoft.yaml, berkeley.yaml) — drop a file to add one
eval/             ground_truth.json + evaluate.py + REPORT.md
tests/            unit tests (validator, planner, scraper, storage, llm)
data/             cache/ (kept), output.json, *.csv, universities.db (generated)
```

---

## Universities

| University | Country | Coverage | Eval Score |
|---|---|---|---|
| MIT | USA | 80% | 100% |
| UC Berkeley | USA | 90% | 85% |
| University of Toronto | Canada | 60% | 70% |

UofT's lower coverage is due to bot-blocking (403 errors) and fee data hidden behind
a JavaScript widget — reported honestly rather than hallucinated.

---

## CLI

```bash
python3 cli.py run                              # scrape all configured universities
python3 cli.py run --universities mit           # just one
python3 cli.py run --universities mit berkeley  # two specific ones
python3 cli.py run --incremental                # skip unchanged pages
python3 cli.py run --no-llm                     # heuristics only (no key, offline)
python3 cli.py list                             # list scraped universities + coverage
python3 cli.py show mit                         # full record as JSON
python3 cli.py field mit tuition_fees           # one field
python3 cli.py courses --slug mit --q algorithms
```

## Query API (bonus)

```bash
pip install fastapi uvicorn
uvicorn api:app --reload         # http://localhost:8000/docs
```

Endpoints: `/universities`, `/universities/{slug}`,
`/universities/{slug}/fields/{field}`, `/courses?slug=&q=`.

## Add a university (zero code)

Drop a `universities/<slug>.yaml` with seed URLs per field — the loader picks it
up automatically. See `universities/mit.yaml` for the format (two seed sources
are given for cross-validated fields like fees and deadlines).

---

## Evaluation

`eval/evaluate.py` scores `data/output.json` against hand-verified
`eval/ground_truth.json`: numbers with tolerance, categories matched exactly,
list fields scored on size + key-completeness + an expected exemplar. Crucially,
fields a source **does not publish** use `null_ok` checks, so the agent is
rewarded for an honest null rather than punished for a gap it couldn't fill — and
penalised the same as a hallucination if it *invents* one.

See `eval/REPORT.md` for the full per-field breakdown and known limitations.

## Testing

```bash
python3 -m pytest -q      # 30 tests, ~0.2s, no network
```