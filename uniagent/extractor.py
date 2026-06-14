"""Turn cleaned page text into structured field data.

Primary path: send the page text + a strict JSON schema to an LLM and parse the
reply.  Fallback path: deterministic regex heuristics for when no LLM backend is
reachable.  The fallback is deliberately conservative (it returns less, with
lower confidence) - the point is that the agent *degrades* instead of crashing.
"""
from __future__ import annotations

import re

from .llm import LLMClient, LLMError, parse_json_block
from .logging_setup import get_logger
from .prompts import SYSTEM_PROMPT, build_prompt

log = get_logger("extractor")


class Extractor:
    """Field extractor with an LLM primary and a heuristic fallback."""

    def __init__(self, client: LLMClient, max_input_chars: int = 16000) -> None:
        self.client = client
        self.max_input_chars = max_input_chars
        # Resolve once: are we using the model or heuristics this run?
        self.llm_ok = client.available()
        if self.llm_ok:
            log.info("LLM backend '%s' (%s) is available", client.provider, client.model)
        else:
            log.warning(
                "No LLM backend reachable - falling back to regex heuristics "
                "(lower coverage, lower confidence)"
            )

    def extract(self, field: str, page_text: str, university: str, url: str):
        """Return ``(data, raw_reply)`` for ``field`` from one page.

        ``data`` is a dict / list / None matching the field schema. ``raw_reply``
        is the model's text (or ``"heuristic"``) and is kept for debugging.
        """
        if not page_text.strip():
            return None, "empty page"

        if self.llm_ok:
            try:
                return self._extract_llm(field, page_text, university, url)
            except LLMError as exc:
                # One bad call shouldn't sink the field; try heuristics instead.
                log.warning("LLM extraction failed for %s (%s); using heuristics", field, exc)

        return self._extract_heuristic(field, page_text), "heuristic"

    # -- LLM path ----------------------------------------------------------- #
    def _extract_llm(self, field: str, page_text: str, university: str, url: str):
        text = page_text[: self.max_input_chars]
        prompt = build_prompt(field, university, text, url)
        reply = self.client.complete(prompt, system=SYSTEM_PROMPT)
        data = parse_json_block(reply)
        return data, reply

    # -- heuristic fallback ------------------------------------------------- #
    def _extract_heuristic(self, field: str, text: str):
        """Cheap, dependency-free extraction for the no-LLM case.

        Only a subset of fields have reliable surface patterns; the rest return
        None so the validator marks them missing rather than wrong.
        """
        handler = {
            "about": self._h_about,
            "acceptance_rate": self._h_acceptance,
            "tuition_fees": self._h_tuition,
        }.get(field)
        return handler(text) if handler else None

    @staticmethod
    def _h_about(text: str):
        data: dict = {}
        m = re.search(r"\b(founded|established)\s+in\s+(\d{4})", text, re.I)
        if m:
            data["founding_year"] = int(m.group(2))
        if re.search(r"\bprivate\b", text, re.I):
            data["type"] = "private"
        elif re.search(r"\bpublic\b", text, re.I):
            data["type"] = "public"
        return data or None

    @staticmethod
    def _h_acceptance(text: str):
        m = re.search(r"acceptance rate[^0-9]{0,30}(\d{1,2}(?:\.\d)?)\s*%", text, re.I)
        if m:
            return {"overall_pct": float(m.group(1))}
        return None

    @staticmethod
    def _h_tuition(text: str):
        # Grab the largest few currency amounts as a rough tuition signal.
        amounts = re.findall(r"[$£€]\s?([0-9]{2,3}(?:,[0-9]{3})+)", text)
        nums = sorted({int(a.replace(",", "")) for a in amounts}, reverse=True)
        if not nums:
            return None
        cur = "USD" if "$" in text else ("GBP" if "£" in text else "EUR")
        return [{"program_level": "unspecified", "international_annual": float(nums[0]),
                 "currency": cur, "notes": "heuristic: largest currency amount on page"}]
