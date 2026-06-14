"""Polite, resilient page fetcher.

Responsibilities (the "Resilience" column of the brief):

* **Retry with back-off** - transient errors (timeouts, 5xx, 429) are retried
  with exponential back-off so a flaky network never kills a run.
* **Respect robots.txt** - every host's rules are fetched once and cached; a
  disallowed URL is skipped, not scraped.
* **Rate limiting** - we wait at least ``rate_limit_delay`` seconds between hits
  to the same host so we never hammer a server.
* **On-disk cache** - responses are cached by URL hash. This makes re-runs fast,
  enables *incremental updates* (skip pages whose content hash is unchanged) and
  means a single flaky fetch doesn't force re-downloading everything.
* **Graceful degradation** - a permanently failing URL returns a ``FetchResult``
  with ``ok=False`` instead of raising; callers carry on with partial data.

JS-heavy pages are handled by an optional Playwright renderer (see
:meth:`Fetcher.fetch`).  Playwright is an *optional* dependency: if it isn't
installed we simply fall back to the static HTML and log a note.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Protego (the same robots.txt parser Scrapy uses) is spec-compliant about
# `Allow:` directives; Python's stdlib ``urllib.robotparser`` mishandles them and
# wrongly blocks pages that sites explicitly permit (e.g. Wikipedia articles).
# We prefer Protego and fall back to the stdlib parser if it isn't installed.
try:
    from protego import Protego
    _HAVE_PROTEGO = True
except ImportError:  # pragma: no cover
    import urllib.robotparser
    _HAVE_PROTEGO = False

from .config import CACHE_DIR, HttpSettings
from .logging_setup import get_logger

log = get_logger("scraper")


@dataclass
class FetchResult:
    """Everything a downstream stage needs about one fetched page."""

    url: str
    ok: bool
    status: int | None = None
    html: str = ""
    text: str = ""                  # cleaned, human-readable text for the LLM
    fetched_at: str | None = None
    from_cache: bool = False
    rendered: bool = False          # True if JS-rendered via Playwright
    error: str | None = None
    content_hash: str | None = None  # sha1 of html, for incremental-update checks
    links: list[tuple[str, str]] = field(default_factory=list)  # (text, abs_url)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


def html_to_text(html: str, keep_tables: bool = True) -> str:
    """Reduce raw HTML to clean text suitable for an LLM prompt.

    We drop scripts/styles/nav/footer boilerplate (which only wastes context
    window) but keep table contents because so much university data - fees,
    deadlines, course lists - lives in tables.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "form"]):
        tag.decompose()
    # Render tables as pipe-separated rows so structure survives the flattening.
    if keep_tables:
        for table in soup.find_all("table"):
            rows = []
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(" | ".join(cells))
            table.replace_with("\n".join(rows))
    text = soup.get_text("\n", strip=True)
    # Collapse runs of blank lines.
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Return ``(anchor_text, absolute_url)`` pairs for the planner to mine."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        out.append((a.get_text(" ", strip=True), urljoin(base_url, href)))
    return out


class Fetcher:
    """Stateful HTTP client: one per run so it can rate-limit and cache."""

    def __init__(self, settings: HttpSettings | None = None) -> None:
        self.s = settings or HttpSettings()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.s.user_agent})
        self._last_hit: dict[str, float] = {}       # host -> monotonic time
        self._robots: dict[str, object] = {}        # host -> parsed robots policy
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # -- robots.txt --------------------------------------------------------- #
    def _load_robots(self, scheme: str, host: str) -> None:
        """Fetch and parse a host's robots.txt once, caching the policy object."""
        robots_url = f"{scheme}://{host}/robots.txt"
        text = ""
        try:
            req = urllib.request.Request(robots_url, headers={"User-Agent": self.s.user_agent})
            text = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001 - missing/broken robots => treat as allow-all
            log.debug("could not read %s; assuming allowed", robots_url)
        if _HAVE_PROTEGO:
            self._robots[host] = Protego.parse(text)
        else:  # stdlib fallback
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(text.splitlines())
            self._robots[host] = rp

    def _allowed(self, url: str) -> bool:
        if not self.s.respect_robots:
            return True
        parts = urlparse(url)
        if parts.netloc not in self._robots:
            self._load_robots(parts.scheme, parts.netloc)
        policy = self._robots[parts.netloc]
        try:
            if _HAVE_PROTEGO:
                return policy.can_fetch(url, self.s.user_agent)
            return policy.can_fetch(self.s.user_agent, url)  # stdlib arg order differs
        except Exception:  # noqa: BLE001
            return True

    # -- rate limiting ------------------------------------------------------ #
    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.s.rate_limit_delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    # -- cache -------------------------------------------------------------- #
    def _cache_path(self, url: str) -> Path:
        return CACHE_DIR / f"{_hash(url)}.json"

    def _read_cache(self, url: str) -> FetchResult | None:
        if not self.s.use_cache:
            return None
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            return None
        ts = datetime.fromisoformat(payload["fetched_at"])
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        if age_h > self.s.cache_ttl_hours:
            return None  # stale -> refetch
        payload["from_cache"] = True
        payload["links"] = [tuple(p) for p in payload.get("links", [])]
        return FetchResult(**payload)

    def _write_cache(self, result: FetchResult) -> None:
        if not self.s.use_cache or not result.ok:
            return
        try:
            data = result.__dict__.copy()
            self._cache_path(result.url).write_text(
                json.dumps(data, ensure_ascii=False), "utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("cache write failed for %s: %s", result.url, exc)

    # -- the public API ----------------------------------------------------- #
    def fetch(self, url: str, render_js: bool = False, force: bool = False) -> FetchResult:
        """Fetch ``url`` and return a :class:`FetchResult` (never raises).

        Args:
            url: absolute URL to fetch.
            render_js: if True (or if the static page looks JS-shelled), try to
                render with Playwright before giving up.
            force: ignore the cache and refetch.
        """
        if not force:
            cached = self._read_cache(url)
            if cached is not None:
                log.info("cache hit  %s", url)
                return cached

        if not self._allowed(url):
            log.warning("robots.txt disallows %s - skipping", url)
            return FetchResult(url=url, ok=False, error="blocked by robots.txt")

        result = self._fetch_static(url)

        # Heuristic: a page with almost no visible text but lots of <script> is
        # probably client-rendered. Try the JS renderer in that case.
        looks_js_shell = result.ok and len(result.text) < 600 and "<script" in result.html.lower()
        if (render_js or looks_js_shell):
            rendered = self._fetch_rendered(url)
            if rendered is not None and len(rendered.text) > len(result.text):
                log.info("used JS renderer for %s", url)
                result = rendered

        self._write_cache(result)
        return result

    def _fetch_static(self, url: str) -> FetchResult:
        """Plain HTTP GET with retry + exponential back-off."""
        last_error = "unknown"
        for attempt in range(1, self.s.max_retries + 1):
            self._throttle(url)
            try:
                resp = self.session.get(url, timeout=self.s.timeout)
                # 429 / 5xx are transient: back off and retry.
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"status {resp.status_code}")
                resp.raise_for_status()
                html = resp.text
                return FetchResult(
                    url=url,
                    ok=True,
                    status=resp.status_code,
                    html=html,
                    text=html_to_text(html),
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    content_hash=_hash(html),
                    links=extract_links(html, url),
                )
            except Exception as exc:  # noqa: BLE001 - any error is retryable here
                last_error = str(exc)
                wait = self.s.backoff_base ** attempt
                log.warning(
                    "fetch failed (%d/%d) %s: %s - retrying in %.1fs",
                    attempt, self.s.max_retries, url, last_error, wait,
                )
                time.sleep(wait)
        log.error("giving up on %s after %d attempts", url, self.s.max_retries)
        return FetchResult(url=url, ok=False, error=last_error)

    def _fetch_rendered(self, url: str) -> FetchResult | None:
        """Render a JS-heavy page with Playwright, if it is installed.

        Returns ``None`` (not an error) when Playwright is unavailable so the
        caller transparently keeps the static result.
        """
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            log.debug("playwright not installed; skipping JS render of %s", url)
            return None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.s.user_agent)
                page.goto(url, timeout=int(self.s.timeout * 1000), wait_until="networkidle")
                html = page.content()
                browser.close()
            return FetchResult(
                url=url,
                ok=True,
                status=200,
                html=html,
                text=html_to_text(html),
                fetched_at=datetime.now(timezone.utc).isoformat(),
                rendered=True,
                content_hash=_hash(html),
                links=extract_links(html, url),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("playwright render failed for %s: %s", url, exc)
            return None

    def crawl_paginated(
        self, start_url: str, max_pages: int = 10, render_js: bool = False
    ) -> list[FetchResult]:
        """Follow "next page" links to gather a paginated listing.

        Many course catalogues split results across pages. We look for an anchor
        whose text/rel looks like a "next" control and follow it until it runs
        out or we hit ``max_pages`` (a safety cap against infinite loops).
        """
        results: list[FetchResult] = []
        seen: set[str] = set()
        url: str | None = start_url
        while url and url not in seen and len(results) < max_pages:
            seen.add(url)
            res = self.fetch(url, render_js=render_js)
            results.append(res)
            if not res.ok:
                break
            url = self._find_next_link(res)
        return results

    @staticmethod
    def _find_next_link(res: FetchResult) -> str | None:
        soup = BeautifulSoup(res.html, "html.parser")
        # rel="next" is the semantic, most reliable signal.
        link = soup.find("a", rel="next")
        if link and link.get("href"):
            return urljoin(res.url, link["href"])
        # Otherwise match common "next" affordances by visible text.
        for a in soup.find_all("a", href=True):
            label = a.get_text(" ", strip=True).lower()
            if label in {"next", "next page", "next ›", "›", "»", "older"}:
                return urljoin(res.url, a["href"])
        return None
