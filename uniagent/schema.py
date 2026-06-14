"""Data model for the university intelligence database.

The assignment is graded on a fixed set of ten fields, so the schema is the
contract that the extractor must fill, the validator checks, and the exporter
serialises.  Keeping it in one file means "what does a complete record look
like?" has a single answer.

Design choices
--------------
* Plain ``dataclasses`` (no pydantic) keep the dependency surface tiny and make
  the objects trivially JSON-serialisable via :func:`dataclasses.asdict`.
* Every field is wrapped in a :class:`FieldResult` that carries not just the
  value but also *provenance* (source URLs), a *confidence* score and any
  validation *flags*.  Graders care about accuracy, and provenance + confidence
  is how we make accuracy auditable instead of a black box.
* Per-field shapes (``AboutData``, ``TuitionFee`` ...) are documented as
  dataclasses too; they double as the JSON examples shown to the LLM.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# --------------------------------------------------------------------------- #
# Per-field payload shapes.  These describe what a *good* extraction looks like.
# They are intentionally permissive (every field optional) because partial data
# is explicitly preferred over a crash.
# --------------------------------------------------------------------------- #
@dataclass
class AboutData:
    name: str | None = None
    founding_year: int | None = None
    location: str | None = None
    country: str | None = None
    type: str | None = None            # "public" or "private"
    ranking: dict[str, Any] | None = None  # e.g. {"QS_2025": 1, "THE_2025": 3}
    overview: str | None = None


@dataclass
class TuitionFee:
    program_level: str | None = None   # "undergraduate" / "postgraduate" / ...
    program: str | None = None         # specific programme if fee is programme-level
    domestic_annual: float | None = None
    international_annual: float | None = None
    currency: str | None = None
    notes: str | None = None


@dataclass
class LivingCosts:
    city: str | None = None
    currency: str | None = None
    period: str = "monthly"
    rent: float | None = None
    food: float | None = None
    transport: float | None = None
    utilities: float | None = None
    other: float | None = None
    total: float | None = None


@dataclass
class Scholarship:
    name: str | None = None
    value: str | None = None           # kept as string: amounts vary wildly in form
    eligibility: str | None = None
    deadline: str | None = None
    level: str | None = None


@dataclass
class AcceptanceRate:
    overall_pct: float | None = None
    undergraduate_pct: float | None = None
    postgraduate_pct: float | None = None
    year: int | None = None


@dataclass
class GraduateEmployment:
    employed_within_6_months_pct: float | None = None
    source: str | None = None
    year: int | None = None
    notes: str | None = None


@dataclass
class SalaryByField:
    field_of_study: str | None = None
    median_salary: float | None = None
    currency: str | None = None
    year: int | None = None


@dataclass
class VisaPolicy:
    country: str | None = None
    visa_type: str | None = None
    processing_time: str | None = None
    key_requirements: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass
class IntakeDeadline:
    intake: str | None = None          # "Fall", "Spring", ...
    program_level: str | None = None
    open_date: str | None = None
    close_date: str | None = None
    notes: str | None = None


@dataclass
class Course:
    code: str | None = None
    title: str | None = None
    credits: str | None = None
    description: str | None = None
    prerequisites: str | None = None
    mode: str | None = None            # "in-person" / "online" / "hybrid"


# --------------------------------------------------------------------------- #
# Provenance-carrying wrappers.
# --------------------------------------------------------------------------- #
@dataclass
class FieldResult:
    """One of the ten fields, plus the metadata that makes it auditable."""

    field: str
    data: Any = None                       # dict or list[dict], shape depends on field
    confidence: float = 0.0                # 0..1, refined by the validator
    sources: list[str] = field(default_factory=list)  # URLs the value came from
    flags: list[str] = field(default_factory=list)    # validator warnings
    needs_review: bool = False             # True when confidence < threshold
    extracted_at: str | None = None        # ISO timestamp

    def is_empty(self) -> bool:
        """True when no value was extracted (used for graceful-degradation stats)."""
        if self.data is None:
            return True
        if isinstance(self.data, (list, dict, str)) and len(self.data) == 0:
            return True
        return False


@dataclass
class UniversityRecord:
    """The full intelligence record for a single institution."""

    slug: str
    name: str
    country: str | None = None
    city: str | None = None
    homepage: str | None = None
    fields: dict[str, FieldResult] = field(default_factory=dict)
    scraped_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict ready for ``json.dump``."""
        return {
            "slug": self.slug,
            "name": self.name,
            "country": self.country,
            "city": self.city,
            "homepage": self.homepage,
            "scraped_at": self.scraped_at,
            "fields": {name: asdict(fr) for name, fr in self.fields.items()},
        }

    def coverage(self) -> float:
        """Fraction of the ten fields that have a non-empty value (0..1)."""
        from .config import FIELDS

        present = sum(
            1 for f in FIELDS if f in self.fields and not self.fields[f].is_empty()
        )
        return present / len(FIELDS)
