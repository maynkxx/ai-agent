"""Self-validation layer.

This is the "Self-Validation" column of the brief: after extraction we
independently sanity-check each value, cross-check related fields, and turn that
into a single ``confidence`` score plus a list of human-readable ``flags``.

Philosophy: the validator never deletes data. A suspicious value is *flagged*
and its confidence lowered so a human reviewer can decide - silently dropping a
borderline-but-correct value would hurt the accuracy score.
"""
from __future__ import annotations

import re
from datetime import datetime

# Expected currency per country, used to catch e.g. a US school quoting "£".
COUNTRY_CURRENCY = {
    "usa": "USD", "united states": "USD", "us": "USD",
    "canada": "CAD",
    "uk": "GBP", "united kingdom": "GBP", "england": "GBP",
    "australia": "AUD", "germany": "EUR", "france": "EUR",
    "netherlands": "EUR", "singapore": "SGD", "switzerland": "CHF",
}

# Rough plausibility envelopes. Wide on purpose - we want to catch nonsense
# (negative fees, a 900% acceptance rate), not second-guess real outliers.
_CURRENT_YEAR = 2026
PLAUSIBLE = {
    "founding_year": (1000, _CURRENT_YEAR),
    "tuition_annual": (500, 150_000),
    "living_monthly": (100, 12_000),
    "salary": (5_000, 600_000),
    "pct": (0, 100),
}


def _in(value, key) -> bool:
    lo, hi = PLAUSIBLE[key]
    try:
        return lo <= float(value) <= hi
    except (TypeError, ValueError):
        return False


def _looks_like_date(s) -> bool:
    if not isinstance(s, str) or not s.strip():
        return False
    # Accept month names or numeric dates; we keep dates as written, so this is
    # only a smell test, not strict parsing.
    if re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", s, re.I):
        return True
    return bool(re.search(r"\d{1,4}[/\-.]\d{1,2}([/\-.]\d{1,4})?", s)) or bool(
        re.search(r"\d{4}", s)
    )


class Validator:
    """Computes confidence + flags for an extracted field value."""

    def __init__(self, low_confidence_threshold: float = 0.55) -> None:
        self.threshold = low_confidence_threshold

    def validate(
        self, field: str, data, *, country: str | None, base_confidence: float,
        source_count: int = 1,
    ) -> tuple[float, list[str], bool]:
        """Return ``(confidence, flags, needs_review)`` for one field value."""
        flags: list[str] = []

        if data is None or (isinstance(data, (list, dict, str)) and len(data) == 0):
            return 0.0, ["missing: no value extracted"], True

        checker = getattr(self, f"_v_{field}", None)
        if checker is not None:
            checker(data, country, flags)

        confidence = self._score(base_confidence, flags, source_count)
        needs_review = confidence < self.threshold or any(
            f.startswith(("implausible", "missing")) for f in flags
        )
        return confidence, flags, needs_review

    # -- confidence model --------------------------------------------------- #
    def _score(self, base: float, flags: list[str], source_count: int) -> float:
        """Combine a base confidence with penalties (flags) and bonuses (corroboration)."""
        score = base
        for f in flags:
            if f.startswith("implausible"):
                score -= 0.30
            elif f.startswith("cross-check"):
                score -= 0.15
            elif f.startswith("partial"):
                score -= 0.10
            else:
                score -= 0.05
        # Independent corroboration from a second source is a strong positive.
        if source_count >= 2:
            score += 0.15
        return max(0.0, min(1.0, round(score, 3)))

    # -- expected currency helper ------------------------------------------ #
    @staticmethod
    def _expected_currency(country: str | None) -> str | None:
        if not country:
            return None
        return COUNTRY_CURRENCY.get(country.strip().lower())

    # -- per-field checks --------------------------------------------------- #
    def _v_about(self, d, country, flags):
        if isinstance(d, dict):
            yr = d.get("founding_year")
            if yr is not None and not _in(yr, "founding_year"):
                flags.append(f"implausible: founding_year={yr}")
            t = (d.get("type") or "").lower()
            if t and t not in ("public", "private"):
                flags.append(f"cross-check: unexpected type '{t}'")

    def _v_tuition_fees(self, d, country, flags):
        exp_cur = self._expected_currency(country)
        if not isinstance(d, list) or not d:
            flags.append("partial: no fee rows")
            return
        for row in d:
            if not isinstance(row, dict):
                continue
            for key in ("domestic_annual", "international_annual"):
                v = row.get(key)
                if v is not None and not _in(v, "tuition_annual"):
                    flags.append(f"implausible: {key}={v}")
            cur = (row.get("currency") or "").upper()
            if exp_cur and cur and cur != exp_cur:
                flags.append(f"cross-check: currency {cur} != expected {exp_cur} for {country}")

    def _v_living_costs(self, d, country, flags):
        if not isinstance(d, dict):
            return
        for key in ("rent", "food", "transport", "utilities", "total"):
            v = d.get(key)
            if v is not None and not _in(v, "living_monthly"):
                flags.append(f"implausible: {key}={v}")
        # Cross-check: if components and a total are both present, they should
        # be in the same ballpark (the total shouldn't be less than the rent).
        rent, total = d.get("rent"), d.get("total")
        if isinstance(rent, (int, float)) and isinstance(total, (int, float)) and total < rent:
            flags.append("cross-check: total < rent")

    def _v_scholarships(self, d, country, flags):
        if not isinstance(d, list) or not d:
            return
        if not any(isinstance(s, dict) and s.get("name") for s in d):
            flags.append("partial: scholarships missing names")

    def _v_acceptance_rate(self, d, country, flags):
        if not isinstance(d, dict):
            return
        for key in ("overall_pct", "undergraduate_pct", "postgraduate_pct"):
            v = d.get(key)
            if v is not None and not _in(v, "pct"):
                flags.append(f"implausible: {key}={v}")

    def _v_graduate_employment(self, d, country, flags):
        if isinstance(d, dict):
            v = d.get("employed_within_6_months_pct")
            if v is not None and not _in(v, "pct"):
                flags.append(f"implausible: employment_pct={v}")

    def _v_average_salaries(self, d, country, flags):
        if not isinstance(d, list):
            return
        for row in d:
            if isinstance(row, dict):
                v = row.get("median_salary")
                if v is not None and not _in(v, "salary"):
                    flags.append(f"implausible: salary={v}")

    def _v_visa_policies(self, d, country, flags):
        if isinstance(d, dict) and not d.get("visa_type"):
            flags.append("partial: visa_type missing")

    def _v_intake_deadlines(self, d, country, flags):
        if not isinstance(d, list) or not d:
            return
        if not any(
            isinstance(x, dict) and (_looks_like_date(x.get("close_date"))
                                     or _looks_like_date(x.get("open_date")))
            for x in d
        ):
            flags.append("partial: no parseable dates in deadlines")

    def _v_course_listings(self, d, country, flags):
        if not isinstance(d, list) or not d:
            return
        good = sum(1 for c in d if isinstance(c, dict) and c.get("code") and c.get("title"))
        if good == 0:
            flags.append("partial: courses missing code/title")
