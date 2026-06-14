"""Groq LLM client.

The extractor needs a single ``complete(prompt) -> str`` method. We provide it
over Groq's free, OpenAI-compatible hosted API, spoken to with plain ``requests``
so there is no vendor SDK to install. Groq runs the model in the cloud, keeping
the heavy compute off the user's machine.

``LLMClient.available()`` lets the extractor check up front whether a key is set
and fall back to regex heuristics if not - that is the "graceful degradation"
requirement applied to the AI layer.
"""
from __future__ import annotations

import json
import time

import requests

from .config import LLMSettings
from .logging_setup import get_logger

log = get_logger("llm")


class LLMError(RuntimeError):
    """Raised when a backend call fails after the client's own handling."""


class LLMClient:
    """Thin Groq chat client (OpenAI-compatible)."""

    def __init__(self, settings: LLMSettings | None = None) -> None:
        self.s = settings or LLMSettings()
        self.provider = self.s.provider
        self.model = self.s.model

    # -- capability check --------------------------------------------------- #
    def available(self) -> bool:
        """Return True if Groq is usable (a key is set and provider isn't 'none').

        Used once at startup so the pipeline can decide between LLM extraction
        and the deterministic heuristic fallback.
        """
        return self.provider == "groq" and bool(self.s.groq_api_key)

    # -- main entry point --------------------------------------------------- #
    def complete(self, prompt: str, system: str | None = None) -> str:
        """Send a single-turn prompt and return the model's text reply.

        Resilient to the Groq free tier's two failure modes:

        * **429 / 5xx** (rate limit, transient server errors) -> wait with
          exponential back-off and retry. The wait also paces us back under the
          tokens-per-minute limit. A ``Retry-After`` header is honoured if sent.
        * **413** (request bigger than the per-request token budget) -> shrink the
          page text in the prompt and retry, since a smaller prompt may fit.
        """
        if self.provider != "groq":
            raise LLMError(f"unsupported LLM provider: {self.provider!r}")
        url = f"{self.s.groq_base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.s.groq_api_key}"}

        last_error = "unknown"
        for attempt in range(1, self.s.max_retries + 1):
            body = {
                "model": self.model,
                "messages": ([{"role": "system", "content": system}] if system else [])
                + [{"role": "user", "content": prompt}],
                "temperature": self.s.temperature,
                "max_tokens": self.s.max_tokens,
            }
            try:
                r = requests.post(url, json=body, headers=headers, timeout=self.s.request_timeout)
                if r.status_code == 413:
                    # Too large: halve the prompt body and try again.
                    prompt = prompt[: max(800, len(prompt) // 2)]
                    last_error = "413 payload too large (shrinking prompt)"
                    log.warning("groq 413; shrinking prompt to %d chars and retrying", len(prompt))
                    continue
                if r.status_code in (429, 500, 502, 503):
                    retry_after = r.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else self.s.retry_base ** attempt
                    last_error = f"status {r.status_code}"
                    log.warning("groq %s (%d/%d); backing off %.1fs",
                                last_error, attempt, self.s.max_retries, wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except requests.RequestException as exc:
                last_error = str(exc)
                wait = self.s.retry_base ** attempt
                log.warning("groq request error (%d/%d): %s; retrying in %.1fs",
                            attempt, self.s.max_retries, last_error, wait)
                time.sleep(wait)
        raise LLMError(f"groq call failed after {self.s.max_retries} attempts: {last_error}")


def parse_json_block(text: str):
    """Best-effort extraction of a JSON value from an LLM reply.

    Models often wrap JSON in prose or ```json fences. We strip fences, then try
    a direct parse, then fall back to slicing the outermost ``{...}`` / ``[...]``.
    Returns the parsed object, or ``None`` if nothing parseable is found.
    """
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        # remove the opening fence (``` or ```json) and the trailing fence
        t = t.split("\n", 1)[-1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
        t = t.strip()
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        pass
    # Fall back to the first balanced-looking bracketed span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        end = t.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(t[start : end + 1])
            except Exception:  # noqa: BLE001
                continue
    return None
