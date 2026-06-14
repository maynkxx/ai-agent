"""Persistence: SQLite as the source of truth, JSON + CSV as exports.

Why SQLite:  the brief asks for a *queryable* database and lists "SQL knowledge"
as a target skill. SQLite is zero-config, ships with Python, and the FastAPI/CLI
query layer reads straight from it.

Layout
------
* ``universities``  - one row per institution (the scalar header info).
* ``fields``        - the raw JSON for each of the ten fields, with confidence /
                      flags / provenance. This is the canonical, lossless store.
* ``courses`` /
  ``scholarships``  - normalised, flattened rows extracted from the JSON so the
                      list-valued fields are directly SQL-queryable.
* ``page_cache``    - url -> content hash, powering *incremental updates*: a
                      re-run can skip pages whose content hash is unchanged.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .config import DB_PATH, FIELDS
from .logging_setup import get_logger
from .schema import UniversityRecord

log = get_logger("storage")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS universities (
    slug TEXT PRIMARY KEY, name TEXT, country TEXT, city TEXT,
    homepage TEXT, scraped_at TEXT, coverage REAL
);
CREATE TABLE IF NOT EXISTS fields (
    slug TEXT, field TEXT, data_json TEXT, confidence REAL,
    flags_json TEXT, sources_json TEXT, needs_review INTEGER, extracted_at TEXT,
    PRIMARY KEY (slug, field)
);
CREATE TABLE IF NOT EXISTS courses (
    slug TEXT, code TEXT, title TEXT, credits TEXT,
    description TEXT, prerequisites TEXT, mode TEXT
);
CREATE TABLE IF NOT EXISTS scholarships (
    slug TEXT, name TEXT, value TEXT, eligibility TEXT, deadline TEXT, level TEXT
);
CREATE TABLE IF NOT EXISTS page_cache (
    url TEXT PRIMARY KEY, slug TEXT, content_hash TEXT, fetched_at TEXT
);
"""


class Storage:
    """Thin wrapper over a SQLite connection plus file exporters."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- incremental-update support ---------------------------------------- #
    def page_unchanged(self, url: str, content_hash: str) -> bool:
        """True if we have stored this exact page content before."""
        row = self.conn.execute(
            "SELECT content_hash FROM page_cache WHERE url = ?", (url,)
        ).fetchone()
        return bool(row) and row["content_hash"] == content_hash

    def record_page(self, url: str, slug: str, content_hash: str, fetched_at: str | None) -> None:
        self.conn.execute(
            "INSERT INTO page_cache(url, slug, content_hash, fetched_at) VALUES (?,?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET content_hash=excluded.content_hash, "
            "fetched_at=excluded.fetched_at",
            (url, slug, content_hash, fetched_at),
        )
        self.conn.commit()

    # -- writes ------------------------------------------------------------- #
    def save_record(self, rec: UniversityRecord) -> None:
        """Upsert a full university record and its normalised child rows."""
        self.conn.execute(
            "INSERT INTO universities(slug,name,country,city,homepage,scraped_at,coverage) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET "
            "name=excluded.name, country=excluded.country, city=excluded.city, "
            "homepage=excluded.homepage, scraped_at=excluded.scraped_at, coverage=excluded.coverage",
            (rec.slug, rec.name, rec.country, rec.city, rec.homepage,
             rec.scraped_at, rec.coverage()),
        )
        for name, fr in rec.fields.items():
            self.conn.execute(
                "INSERT INTO fields(slug,field,data_json,confidence,flags_json,"
                "sources_json,needs_review,extracted_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(slug,field) DO UPDATE SET data_json=excluded.data_json, "
                "confidence=excluded.confidence, flags_json=excluded.flags_json, "
                "sources_json=excluded.sources_json, needs_review=excluded.needs_review, "
                "extracted_at=excluded.extracted_at",
                (rec.slug, name, json.dumps(fr.data, ensure_ascii=False), fr.confidence,
                 json.dumps(fr.flags), json.dumps(fr.sources), int(fr.needs_review),
                 fr.extracted_at),
            )
        # Refresh normalised child tables for this university.
        self.conn.execute("DELETE FROM courses WHERE slug = ?", (rec.slug,))
        self.conn.execute("DELETE FROM scholarships WHERE slug = ?", (rec.slug,))
        self._flatten_courses(rec)
        self._flatten_scholarships(rec)
        self.conn.commit()
        log.info("saved %s (coverage %.0f%%)", rec.slug, rec.coverage() * 100)

    def _flatten_courses(self, rec: UniversityRecord) -> None:
        fr = rec.fields.get("course_listings")
        if not fr or not isinstance(fr.data, list):
            return
        for c in fr.data:
            if not isinstance(c, dict):
                continue
            self.conn.execute(
                "INSERT INTO courses(slug,code,title,credits,description,prerequisites,mode) "
                "VALUES (?,?,?,?,?,?,?)",
                (rec.slug, c.get("code"), c.get("title"), str(c.get("credits") or ""),
                 c.get("description"), c.get("prerequisites"), c.get("mode")),
            )

    def _flatten_scholarships(self, rec: UniversityRecord) -> None:
        fr = rec.fields.get("scholarships")
        if not fr or not isinstance(fr.data, list):
            return
        for s in fr.data:
            if not isinstance(s, dict):
                continue
            self.conn.execute(
                "INSERT INTO scholarships(slug,name,value,eligibility,deadline,level) "
                "VALUES (?,?,?,?,?,?)",
                (rec.slug, s.get("name"), s.get("value"), s.get("eligibility"),
                 s.get("deadline"), s.get("level")),
            )

    # -- reads (used by the CLI / API query layer) ------------------------- #
    def get_university(self, slug: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM universities WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            return None
        rec = dict(row)
        rec["fields"] = {}
        for fld in self.conn.execute(
            "SELECT * FROM fields WHERE slug = ?", (slug,)
        ).fetchall():
            rec["fields"][fld["field"]] = {
                "data": json.loads(fld["data_json"]) if fld["data_json"] else None,
                "confidence": fld["confidence"],
                "flags": json.loads(fld["flags_json"] or "[]"),
                "sources": json.loads(fld["sources_json"] or "[]"),
                "needs_review": bool(fld["needs_review"]),
            }
        return rec

    def list_universities(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT slug,name,country,city,coverage FROM universities ORDER BY slug"
        ).fetchall()]

    def get_field(self, slug: str, field: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM fields WHERE slug = ? AND field = ?", (slug, field)
        ).fetchone()
        if not row:
            return None
        return {
            "slug": slug, "field": field,
            "data": json.loads(row["data_json"]) if row["data_json"] else None,
            "confidence": row["confidence"],
            "flags": json.loads(row["flags_json"] or "[]"),
            "needs_review": bool(row["needs_review"]),
        }

    def query_courses(self, slug: str | None = None, q: str | None = None) -> list[dict]:
        sql = "SELECT * FROM courses WHERE 1=1"
        args: list = []
        if slug:
            sql += " AND slug = ?"; args.append(slug)
        if q:
            sql += " AND (code LIKE ? OR title LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]


# --------------------------------------------------------------------------- #
# File exporters (JSON + CSV) - operate on records, independent of the DB.
# --------------------------------------------------------------------------- #
def export_json(records: Iterable[UniversityRecord], path: Path) -> None:
    payload = [r.to_dict() for r in records]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), "utf-8")
    log.info("wrote %s", path)


def export_csv(records: list[UniversityRecord], out_dir: Path) -> None:
    """Write a flat summary CSV plus child CSVs for courses & scholarships.

    Nested fields can't fit one rectangular table, so we emit a summary row per
    university (scalar fields + per-field confidence) and separate long-format
    files for the list-valued fields.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = out_dir / "summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as fh:
        cols = ["slug", "name", "country", "city", "coverage"] + [f"{f}_confidence" for f in FIELDS]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in records:
            row = {"slug": r.slug, "name": r.name, "country": r.country,
                   "city": r.city, "coverage": round(r.coverage(), 3)}
            for f in FIELDS:
                fr = r.fields.get(f)
                row[f"{f}_confidence"] = round(fr.confidence, 3) if fr else 0.0
            w.writerow(row)

    courses_path = out_dir / "courses.csv"
    with courses_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["slug", "code", "title", "credits", "prerequisites", "mode", "description"])
        for r in records:
            fr = r.fields.get("course_listings")
            for c in (fr.data if fr and isinstance(fr.data, list) else []):
                if isinstance(c, dict):
                    w.writerow([r.slug, c.get("code"), c.get("title"), c.get("credits"),
                                c.get("prerequisites"), c.get("mode"), c.get("description")])

    sch_path = out_dir / "scholarships.csv"
    with sch_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["slug", "name", "value", "eligibility", "deadline", "level"])
        for r in records:
            fr = r.fields.get("scholarships")
            for s in (fr.data if fr and isinstance(fr.data, list) else []):
                if isinstance(s, dict):
                    w.writerow([r.slug, s.get("name"), s.get("value"),
                                s.get("eligibility"), s.get("deadline"), s.get("level")])
    log.info("wrote %s, %s, %s", summary_path, courses_path, sch_path)
