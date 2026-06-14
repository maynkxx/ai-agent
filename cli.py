#!/usr/bin/env python3
"""Command-line interface for the University Intelligence Agent.

Examples
--------
    # Scrape the two bundled universities and export JSON + CSV
    python cli.py run

    # Scrape one university with a specific Groq model
    python cli.py run --universities mit --model llama-3.1-8b-instant

    # Re-run but skip pages whose content is unchanged (incremental)
    python cli.py run --incremental

    # Query the database that was built
    python cli.py list
    python cli.py show mit
    python cli.py field mit tuition_fees
    python cli.py courses --slug mit --q algorithms
"""
from __future__ import annotations

import argparse
import json
import logging

from uniagent.config import DATA_DIR, FIELDS, Settings
from uniagent.logging_setup import configure
from uniagent.pipeline import Pipeline
from uniagent.planner import load_configs
from uniagent.storage import Storage, export_csv, export_json


def _build_settings(args: argparse.Namespace) -> Settings:
    """Apply CLI overrides on top of the default settings."""
    s = Settings()
    if getattr(args, "provider", None):
        s.llm.provider = args.provider
    if getattr(args, "model", None):
        s.llm.model = args.model
    if getattr(args, "no_llm", False):
        s.llm.provider = "none"  # forces heuristic extraction
    if getattr(args, "no_robots", False):
        s.http.respect_robots = False
    if getattr(args, "fresh", False):
        s.http.use_cache = False
    return s


def cmd_run(args: argparse.Namespace) -> None:
    settings = _build_settings(args)
    storage = Storage()
    pipeline = Pipeline(settings, storage=storage, incremental=args.incremental)

    configs = load_configs(args.universities)
    if not configs:
        print("No university configs found. Add YAML files under universities/.")
        return

    records = []
    for cfg in configs:
        records.append(pipeline.run(cfg))

    # Export the canonical JSON and the flattened CSVs.
    export_json(records, DATA_DIR / "output.json")
    export_csv(records, DATA_DIR)
    storage.close()

    print("\n=== Summary ===")
    for rec in records:
        review = sum(1 for fr in rec.fields.values() if fr.needs_review)
        print(f"  {rec.slug:8s}  coverage {rec.coverage()*100:5.0f}%   "
              f"{review} field(s) flagged for review")
    print(f"\nWrote {DATA_DIR/'output.json'} and CSVs to {DATA_DIR}/")


def cmd_list(args: argparse.Namespace) -> None:
    storage = Storage()
    for u in storage.list_universities():
        print(f"{u['slug']:8s}  {u['name']:45s}  coverage {u['coverage']*100:4.0f}%")
    storage.close()


def cmd_show(args: argparse.Namespace) -> None:
    storage = Storage()
    rec = storage.get_university(args.slug)
    print(json.dumps(rec, indent=2, ensure_ascii=False) if rec else f"no such university: {args.slug}")
    storage.close()


def cmd_field(args: argparse.Namespace) -> None:
    storage = Storage()
    res = storage.get_field(args.slug, args.field)
    print(json.dumps(res, indent=2, ensure_ascii=False) if res else "not found")
    storage.close()


def cmd_courses(args: argparse.Namespace) -> None:
    storage = Storage()
    rows = storage.query_courses(args.slug, args.q)
    print(f"{len(rows)} course(s)")
    for c in rows[: args.limit]:
        print(f"  [{c['slug']}] {c['code'] or '?':12s} {c['title'] or ''} "
              f"({c['credits'] or '?'} cr)")
    storage.close()


def main() -> None:
    p = argparse.ArgumentParser(description="University Intelligence Database Agent")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="scrape universities and build the database")
    r.add_argument("--universities", nargs="*", help="slugs to scrape (default: all configs)")
    r.add_argument("--provider", choices=["groq", "none"], help="LLM backend (default: groq)")
    r.add_argument("--model", help="Groq model name (default: llama-3.1-8b-instant)")
    r.add_argument("--no-llm", action="store_true", help="use regex heuristics only")
    r.add_argument("--incremental", action="store_true", help="skip unchanged pages")
    r.add_argument("--fresh", action="store_true", help="ignore the page cache")
    r.add_argument("--no-robots", action="store_true", help="ignore robots.txt (use responsibly)")
    r.set_defaults(func=cmd_run)

    sub.add_parser("list", help="list scraped universities").set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="print a university's full record")
    s.add_argument("slug")
    s.set_defaults(func=cmd_show)

    f = sub.add_parser("field", help="print one field for one university")
    f.add_argument("slug")
    f.add_argument("field", choices=FIELDS)
    f.set_defaults(func=cmd_field)

    c = sub.add_parser("courses", help="query the courses table")
    c.add_argument("--slug")
    c.add_argument("--q", help="match course code/title")
    c.add_argument("--limit", type=int, default=25)
    c.set_defaults(func=cmd_courses)

    args = p.parse_args()
    configure(logging.DEBUG if args.verbose else logging.INFO)
    args.func(args)


if __name__ == "__main__":
    main()
