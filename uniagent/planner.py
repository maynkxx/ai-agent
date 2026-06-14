"""Planning loop: decide which pages to visit, and in what order.

Two complementary strategies (the "Planning Loop" column of the brief):

1. **Seed URLs (declarative).** Each university ships a small YAML config listing
   the best-known page(s) per field. This is what makes adding a 4th university a
   *zero-code* change - drop in a YAML file and the agent picks it up.

2. **Link discovery (adaptive).** Seeds are never exhaustive and sites get
   reorganised, so the planner also scans a page's links for field-relevant
   keywords and proposes extra URLs. This is how the agent "adapts when it hits
   unexpected layouts / navigation" instead of being brittle.

The planner emits an ordered list of :class:`CrawlTask` objects; the pipeline
executes them. Order matters: ``about`` first (cheap, anchors context), then the
high-value structured fields, with ``course_listings`` (the heaviest, paginated)
last.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from pathlib import Path

import yaml

from .config import FIELDS, UNIVERSITIES_DIR
from .logging_setup import get_logger

log = get_logger("planner")

# Anchor-text / URL keywords that hint a page is about a given field. Used by the
# adaptive discovery pass to find pages the YAML seeds may have missed.
FIELD_KEYWORDS: dict[str, list[str]] = {
    "about": ["about", "overview", "history", "facts", "at a glance"],
    "tuition_fees": ["tuition", "fees", "cost of attendance", "cost"],
    "living_costs": ["cost of living", "living cost", "living expenses", "budget", "estimated expenses"],
    "scholarships": ["scholarship", "financial aid", "funding", "bursary", "grants"],
    "acceptance_rate": ["acceptance rate", "admission statistics", "class profile", "admitted students", "selectivity"],
    "graduate_employment": ["employment", "career outcomes", "graduate outcomes", "first destination", "placement"],
    "average_salaries": ["salary", "salaries", "earnings", "starting salary", "compensation"],
    "visa_policies": ["visa", "study permit", "immigration", "international students", "i-20"],
    "intake_deadlines": ["deadline", "important dates", "application dates", "key dates", "apply by"],
    "course_listings": ["courses", "course catalog", "course catalogue", "subjects", "curriculum", "programs", "modules"],
}


@dataclass
class CrawlTask:
    """One unit of work for the pipeline: fetch these URLs to fill this field."""

    field: str
    urls: list[str]
    paginate: bool = False     # follow "next" links (for course catalogues)
    render_js: bool = False    # force the JS renderer for this field's pages
    max_pages: int = 8         # pagination safety cap


@dataclass
class UniversityConfig:
    """Parsed university plug-in config (one YAML file per institution)."""

    slug: str
    name: str
    country: str | None = None
    city: str | None = None
    currency: str | None = None
    homepage: str | None = None
    seeds: dict = dc_field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> "UniversityConfig":
        raw = yaml.safe_load(path.read_text("utf-8"))
        return cls(
            slug=raw["slug"],
            name=raw["name"],
            country=raw.get("country"),
            city=raw.get("city"),
            currency=raw.get("currency"),
            homepage=raw.get("homepage"),
            seeds=raw.get("seeds", {}),
        )


def load_configs(slugs: list[str] | None = None) -> list[UniversityConfig]:
    """Load every university YAML in ``universities/`` (optionally filtered).

    This is the plug-in loader: any ``*.yaml`` dropped in the directory becomes a
    scrapeable university with no code change.
    """
    configs: list[UniversityConfig] = []
    for path in sorted(UNIVERSITIES_DIR.glob("*.yaml")):
        cfg = UniversityConfig.from_yaml(path)
        if slugs is None or cfg.slug in slugs:
            configs.append(cfg)
    if slugs:  # warn about anything the caller asked for but we didn't find
        found = {c.slug for c in configs}
        for missing in set(slugs) - found:
            log.warning("no config found for requested university '%s'", missing)
    return configs


class Planner:
    """Builds the ordered crawl plan for a university."""

    def plan(self, cfg: UniversityConfig) -> list[CrawlTask]:
        """Turn a config's seeds into ordered :class:`CrawlTask`s."""
        tasks: list[CrawlTask] = []
        for field in FIELDS:  # FIELDS order == crawl order
            spec = cfg.seeds.get(field)
            if spec is None:
                continue
            # A seed can be a bare list of URLs, or a dict with options.
            if isinstance(spec, dict):
                urls = spec.get("urls", [])
                tasks.append(CrawlTask(
                    field=field,
                    urls=list(urls),
                    paginate=bool(spec.get("paginate", False)),
                    render_js=bool(spec.get("render_js", False)),
                    max_pages=int(spec.get("max_pages", 8)),
                ))
            else:
                tasks.append(CrawlTask(field=field, urls=list(spec)))
        log.info("planned %d field-tasks for %s", len(tasks), cfg.slug)
        return tasks

    @staticmethod
    def discover(links: list[tuple[str, str]], fields_needed: list[str]) -> dict[str, list[str]]:
        """Mine a page's links for URLs relevant to fields we still lack.

        ``links`` is ``[(anchor_text, absolute_url), ...]`` from the scraper.
        Returns ``{field: [candidate_urls]}`` for fields in ``fields_needed``.
        This is the adaptive half of planning - it lets the agent recover when a
        seed URL is dead or a site has been restructured.
        """
        found: dict[str, list[str]] = {f: [] for f in fields_needed}
        for text, url in links:
            haystack = f"{text} {url}".lower()
            for field in fields_needed:
                if any(kw in haystack for kw in FIELD_KEYWORDS[field]):
                    if url not in found[field]:
                        found[field].append(url)
        return {f: urls[:3] for f, urls in found.items() if urls}  # cap noise
