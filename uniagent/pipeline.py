"""Orchestration: planner -> scraper -> extractor -> validator -> storage.

This module is where the four design pillars meet:

* **Planning** - asks the planner for an ordered task list and, when a field has
  no working seed, runs adaptive link discovery from the homepage.
* **Resilience** - *every* field is processed inside its own try/except, so a
  failure on one field (or one page) degrades to partial data instead of
  crashing the whole run. The scraper already handles per-request retries.
* **Cross-validation** - when a field is found on two pages, the values are
  merged and the corroboration raises confidence (see :meth:`_merge`).
* **Incremental updates** - in incremental mode a page whose content hash is
  unchanged since last run reuses the stored value and skips the (expensive) LLM
  call.

The output is a :class:`UniversityRecord` with all ten fields populated as far as
the sources allow.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import FIELDS, Settings
from .extractor import Extractor
from .llm import LLMClient
from .logging_setup import get_logger
from .planner import CrawlTask, Planner, UniversityConfig
from .scraper import Fetcher
from .schema import FieldResult, UniversityRecord
from .storage import Storage
from .validator import Validator

log = get_logger("pipeline")

# Fields whose value is a list of records (merge = concatenate + dedupe) vs a
# single object (merge = fill nulls from later sources).
LIST_FIELDS = {"tuition_fees", "scholarships", "average_salaries",
               "intake_deadlines", "course_listings"}

# How to deduplicate rows when merging list-valued fields from multiple pages.
_DEDUPE_KEYS = {
    "tuition_fees": lambda r: (r.get("program_level"), r.get("program")),
    "scholarships": lambda r: (r.get("name") or "").strip().lower(),
    "average_salaries": lambda r: (r.get("field_of_study") or "").strip().lower(),
    "intake_deadlines": lambda r: (r.get("intake"), r.get("program_level")),
    "course_listings": lambda r: (r.get("code") or r.get("title") or "").strip().lower(),
}


class Pipeline:
    """Runs the full scrape->store flow for one or more universities."""

    def __init__(self, settings: Settings, storage: Storage | None = None,
                 incremental: bool = False) -> None:
        self.settings = settings
        settings.ensure_dirs()
        self.fetcher = Fetcher(settings.http)
        self.extractor = Extractor(LLMClient(settings.llm), settings.llm.max_input_chars)
        self.validator = Validator(settings.low_confidence_threshold)
        self.planner = Planner()
        self.storage = storage
        self.incremental = incremental

    # ---------------------------------------------------------------------- #
    def run(self, cfg: UniversityConfig) -> UniversityRecord:
        """Scrape every field for one university and return its record."""
        log.info("=== scraping %s ===", cfg.name)
        rec = UniversityRecord(
            slug=cfg.slug, name=cfg.name, country=cfg.country, city=cfg.city,
            homepage=cfg.homepage, scraped_at=datetime.now(timezone.utc).isoformat(),
        )
        tasks = self.planner.plan(cfg)

        # Adaptive planning: if any field lacks a seed, mine the homepage links.
        missing = [f for f in FIELDS if f not in {t.field for t in tasks}]
        if missing and cfg.homepage:
            tasks += self._discover_tasks(cfg, missing)

        for task in tasks:
            try:
                rec.fields[task.field] = self._run_task(cfg, task)
            except Exception as exc:  # noqa: BLE001 - never let one field crash the run
                log.exception("field '%s' failed for %s: %s", task.field, cfg.slug, exc)
                rec.fields[task.field] = FieldResult(
                    field=task.field, flags=[f"error: {exc}"], needs_review=True,
                    extracted_at=datetime.now(timezone.utc).isoformat(),
                )

        # Ensure all ten fields exist in the record, even if empty.
        for f in FIELDS:
            rec.fields.setdefault(f, FieldResult(field=f, flags=["missing: no source"], needs_review=True))

        if self.storage is not None:
            self.storage.save_record(rec)
        log.info("=== done %s: coverage %.0f%% ===", cfg.slug, rec.coverage() * 100)
        return rec

    # ---------------------------------------------------------------------- #
    def _discover_tasks(self, cfg: UniversityConfig, missing: list[str]) -> list[CrawlTask]:
        """Fetch the homepage and propose URLs for fields without seeds."""
        log.info("discovering pages for fields without seeds: %s", missing)
        home = self.fetcher.fetch(cfg.homepage)
        if not home.ok:
            return []
        discovered = self.planner.discover(home.links, missing)
        return [CrawlTask(field=f, urls=urls) for f, urls in discovered.items()]

    def _run_task(self, cfg: UniversityConfig, task: CrawlTask) -> FieldResult:
        """Fetch + extract one field across all its URLs, then validate."""
        per_source: list[tuple[object, str]] = []  # (data, url) for non-empty results

        for url in task.urls:
            pages = (self.fetcher.crawl_paginated(url, task.max_pages, task.render_js)
                     if task.paginate else [self.fetcher.fetch(url, render_js=task.render_js)])
            for page in pages:
                if not page.ok or not page.text:
                    continue
                if self.storage is not None and page.content_hash:
                    self.storage.record_page(page.url, cfg.slug, page.content_hash, page.fetched_at)
                # Incremental: reuse stored value when the page hasn't changed.
                if self.incremental and self._reuse_unchanged(cfg, task.field, page):
                    log.info("incremental: %s unchanged, reusing stored %s", page.url, task.field)
                    continue
                data, _raw = self.extractor.extract(task.field, page.text, cfg.name, page.url)
                if not _is_empty(data):
                    per_source.append((data, page.url))

        merged, sources = self._merge(task.field, per_source)
        base_conf = 0.6 if self.extractor.llm_ok else 0.4
        confidence, flags, needs_review = self.validator.validate(
            task.field, merged, country=cfg.country,
            base_confidence=base_conf, source_count=len(sources),
        )
        return FieldResult(
            field=task.field, data=merged, confidence=confidence, sources=sources,
            flags=flags, needs_review=needs_review,
            extracted_at=datetime.now(timezone.utc).isoformat(),
        )

    def _reuse_unchanged(self, cfg: UniversityConfig, field: str, page) -> bool:
        """True if this page is unchanged AND we already have a stored value."""
        if self.storage is None:
            return False
        if not self.storage.page_unchanged(page.url, page.content_hash or ""):
            return False
        existing = self.storage.get_field(cfg.slug, field)
        return bool(existing and existing.get("data"))

    # ---------------------------------------------------------------------- #
    def _merge(self, field: str, per_source: list[tuple[object, str]]):
        """Combine per-page extractions into one value + the contributing URLs.

        List fields are concatenated and de-duplicated. Object fields are merged
        key-by-key, keeping the first non-null value (and thereby cross-checking:
        a field seen on two pages contributes two sources -> higher confidence).
        """
        if not per_source:
            return (None, [])

        if field in LIST_FIELDS:
            keyfn = _DEDUPE_KEYS[field]
            seen: set = set()
            merged: list = []
            sources: list[str] = []
            for data, url in per_source:
                rows = data if isinstance(data, list) else [data]
                contributed = False
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    k = keyfn(row)
                    if k in seen:
                        continue
                    seen.add(k)
                    merged.append(row)
                    contributed = True
                if contributed:
                    sources.append(url)
            return (merged, sources)

        # Object field: merge dicts, first non-null wins per key.
        merged_obj: dict = {}
        sources = []
        for data, url in per_source:
            if not isinstance(data, dict):
                continue
            used = False
            for k, v in data.items():
                if v in (None, "", [], {}):
                    continue
                if k not in merged_obj or merged_obj[k] in (None, "", [], {}):
                    merged_obj[k] = v
                    used = True
            if used:
                sources.append(url)
        return (merged_obj or None, sources)


def _is_empty(data) -> bool:
    if data is None:
        return True
    if isinstance(data, (list, dict, str)) and len(data) == 0:
        return True
    return False
