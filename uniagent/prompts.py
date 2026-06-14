"""Per-field extraction prompts.

Each of the ten fields gets (a) a one-line description of what to pull and
(b) a JSON schema example.  Giving the model an explicit, minimal schema is the
single biggest lever on extraction accuracy: it constrains the output shape, kills
chatty prose, and makes downstream parsing/validation deterministic.

Two hard rules are repeated in every prompt because they directly defend the
accuracy score:

  1. "Only use facts present in the text" -> suppresses hallucination.
  2. "Use null for anything not stated"  -> suppresses guessing / makes gaps honest.
"""
from __future__ import annotations

# JSON shape shown to the model for each field. Kept compact on purpose.
SCHEMA_HINTS: dict[str, str] = {
    "about": """{
  "name": str, "founding_year": int|null, "location": str|null,
  "country": str|null, "type": "public"|"private"|null,
  "ranking": {"<source_year>": int} | null, "overview": str|null
}""",
    "tuition_fees": """[
  {"program_level": "undergraduate"|"postgraduate"|str, "program": str|null,
   "domestic_annual": number|null, "international_annual": number|null,
   "currency": str|null, "notes": str|null}
]""",
    "living_costs": """{
  "city": str|null, "currency": str|null, "period": "monthly",
  "rent": number|null, "food": number|null, "transport": number|null,
  "utilities": number|null, "other": number|null, "total": number|null
}""",
    "scholarships": """[
  {"name": str, "value": str|null, "eligibility": str|null,
   "deadline": str|null, "level": str|null}
]""",
    "acceptance_rate": """{
  "overall_pct": number|null, "undergraduate_pct": number|null,
  "postgraduate_pct": number|null, "year": int|null
}""",
    "graduate_employment": """{
  "employed_within_6_months_pct": number|null, "source": str|null,
  "year": int|null, "notes": str|null
}""",
    "average_salaries": """[
  {"field_of_study": str, "median_salary": number|null,
   "currency": str|null, "year": int|null}
]""",
    "visa_policies": """{
  "country": str|null, "visa_type": str|null, "processing_time": str|null,
  "key_requirements": [str], "notes": str|null
}""",
    "intake_deadlines": """[
  {"intake": "Fall"|"Spring"|str, "program_level": str|null,
   "open_date": str|null, "close_date": str|null, "notes": str|null}
]""",
    "course_listings": """[
  {"code": str, "title": str, "credits": str|null, "description": str|null,
   "prerequisites": str|null, "mode": str|null}
]""",
}

# Field-specific guidance layered on top of the generic instructions.
FIELD_INSTRUCTIONS: dict[str, str] = {
    "about": "Extract the institution overview: founding year, location, "
             "public/private type, and any world/national rankings with their "
             "source and year.",
    "tuition_fees": "Extract annual tuition per programme level. Keep domestic "
                    "and international fees separate. Record the currency. If a "
                    "fee is per-term/semester, convert to annual only if the "
                    "page states the number of terms; otherwise note it.",
    "living_costs": "Extract estimated MONTHLY living costs for a student in "
                    "this city (rent, food, transport, utilities). If only an "
                    "annual figure is given, divide by 12 and note it.",
    "scholarships": "Extract each named scholarship with its monetary value, "
                    "eligibility criteria and application deadline.",
    "acceptance_rate": "Extract the admission/acceptance rate as a percentage. "
                       "Split undergraduate vs postgraduate if both are given.",
    "graduate_employment": "Extract the percentage of graduates employed (ideally "
                           "within 6 months), plus the survey source and year.",
    "average_salaries": "Extract median/average graduate starting salaries, "
                        "broken down by field of study where possible.",
    "visa_policies": "Extract the student visa type for international students, "
                     "typical processing time, and the key documents/requirements.",
    "intake_deadlines": "Extract application open and close dates for each intake "
                        "(Fall/Spring/etc.). Keep dates exactly as written.",
    "course_listings": "Extract each course: code, title, credit value, a short "
                       "description, prerequisites and delivery mode.",
}

SYSTEM_PROMPT = (
    "You are a meticulous data-extraction engine for a university research "
    "database. You convert webpage text into strict JSON. You never invent "
    "facts: if the text does not state something, you output null. You output "
    "ONLY JSON - no explanations, no markdown fences."
)


def build_prompt(field: str, university: str, page_text: str, page_url: str) -> str:
    """Compose the user prompt for extracting ``field`` from one page."""
    schema = SCHEMA_HINTS[field]
    instruction = FIELD_INSTRUCTIONS[field]
    return f"""University: {university}
Source page: {page_url}
Task: {instruction}

Return JSON matching EXACTLY this schema (use null / [] when unknown):
{schema}

Rules:
- Use ONLY information present in the page text below. Do not guess or use prior knowledge.
- Numbers must be plain (no currency symbols, no thousands separators).
- If the page contains nothing relevant to this field, return null (or [] for a list schema).

PAGE TEXT:
\"\"\"
{page_text}
\"\"\"
"""
