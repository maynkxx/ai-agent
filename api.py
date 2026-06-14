#!/usr/bin/env python3
"""Optional FastAPI query layer over the scraped database (bonus deliverable).

Run with:
    pip install fastapi uvicorn
    uvicorn api:app --reload
    # then open http://localhost:8000/docs

FastAPI is imported lazily/guarded so the core agent has zero dependency on it;
the rest of the project works without ever installing a web framework.
"""
from __future__ import annotations

from uniagent.config import FIELDS
from uniagent.storage import Storage

try:
    from fastapi import FastAPI, HTTPException
except ImportError as exc:  # pragma: no cover - only hit when extra not installed
    raise SystemExit(
        "FastAPI is not installed. Run:  pip install fastapi uvicorn"
    ) from exc

app = FastAPI(
    title="University Intelligence API",
    description="Query the scraped university database.",
    version="1.0.0",
)


def _db() -> Storage:
    # One connection per request keeps things simple and thread-safe for SQLite.
    return Storage()


@app.get("/universities")
def list_universities() -> list[dict]:
    """List every university in the database with its coverage score."""
    db = _db()
    try:
        return db.list_universities()
    finally:
        db.close()


@app.get("/universities/{slug}")
def get_university(slug: str) -> dict:
    """Full intelligence record for one university."""
    db = _db()
    try:
        rec = db.get_university(slug)
        if rec is None:
            raise HTTPException(404, f"unknown university '{slug}'")
        return rec
    finally:
        db.close()


@app.get("/universities/{slug}/fields/{field}")
def get_field(slug: str, field: str) -> dict:
    """One of the ten intelligence fields for a university."""
    if field not in FIELDS:
        raise HTTPException(400, f"unknown field; choose from {FIELDS}")
    db = _db()
    try:
        res = db.get_field(slug, field)
        if res is None:
            raise HTTPException(404, "not found")
        return res
    finally:
        db.close()


@app.get("/courses")
def search_courses(slug: str | None = None, q: str | None = None, limit: int = 50) -> list[dict]:
    """Search the normalised courses table by university and/or code/title."""
    db = _db()
    try:
        return db.query_courses(slug, q)[:limit]
    finally:
        db.close()
