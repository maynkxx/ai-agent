"""University Intelligence Database Agent.

An AI-powered scraping agent that builds a structured, queryable database of
university intelligence (costs, courses, scholarships, visa policies, ...) across
multiple institutions.

The package is split into single-responsibility modules so each stage of the
pipeline can be reasoned about, tested, and swapped independently:

    planner   -> decides which pages to visit, in what order
    scraper   -> fetches pages politely (retry, back-off, robots.txt, cache)
    extractor -> turns messy HTML into structured JSON via an LLM (or heuristics)
    validator -> detects missing / implausible values and scores confidence
    storage   -> persists to SQLite and exports JSON / CSV
    pipeline  -> wires the stages together with graceful degradation
"""

__version__ = "1.0.0"
