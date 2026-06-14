"""Central configuration for the agent.

Everything that a reviewer might want to tweak (politeness delays, retry budget,
which LLM backend to use) lives here in one typed place rather than being
sprinkled through the code as magic numbers.

Settings are resolved with this precedence:  CLI flag > environment variable >
the defaults below.  The CLI layer (``cli.py``) is responsible for the first.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Project paths -------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
UNIVERSITIES_DIR = ROOT / "universities"
DB_PATH = DATA_DIR / "universities.db"

# The ten intelligence fields the assignment asks for, in a stable order.
# Used by the planner, validator and exporters so the field set is defined once.
FIELDS: tuple[str, ...] = (
    "about",
    "tuition_fees",
    "living_costs",
    "scholarships",
    "acceptance_rate",
    "graduate_employment",
    "average_salaries",
    "visa_policies",
    "intake_deadlines",
    "course_listings",
)


@dataclass
class HttpSettings:
    """Politeness / resilience knobs for the network layer."""

    # A descriptive UA with contact info is the courteous thing to send and makes
    # us look less like an anonymous bot (which some sites block outright).
    user_agent: str = (
        "UniIntelAgent/1.0 (+https://github.com/example/university-agent; "
        "research assignment; contact: student@example.com)"
    )
    timeout: float = 30.0          # per-request hard timeout (seconds)
    max_retries: int = 4           # attempts before we give up on a URL
    backoff_base: float = 1.5      # exponential back-off: base ** attempt
    rate_limit_delay: float = 1.0  # minimum seconds between hits to one host
    respect_robots: bool = True    # honour robots.txt Disallow rules
    use_cache: bool = True         # serve pages from the on-disk cache when fresh
    cache_ttl_hours: float = 168.0 # treat cached pages older than this as stale


@dataclass
class LLMSettings:
    """Which language model turns HTML into structured data.

    We use **Groq** - a free, OpenAI-compatible hosted tier - on purpose: the
    model runs in the cloud, so the heavy lifting never bogs down a laptop the way
    a local model (e.g. Ollama) would. Spoken to over plain HTTP, no vendor SDK.

    Set ``GROQ_API_KEY`` in the environment to enable extraction. With no key (or
    ``provider="none"``) the extractor falls back to deterministic regex
    heuristics, so the agent degrades gracefully instead of crashing.

    The default model is a small/fast tier; override with ``UNIAGENT_LLM_MODEL``
    or ``--model``.
    """

    provider: str = os.getenv("UNIAGENT_LLM_PROVIDER", "groq")  # "groq" or "none"
    model: str = os.getenv("UNIAGENT_LLM_MODEL", "llama-3.1-8b-instant")
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    temperature: float = 0.0       # deterministic extraction, no creativity wanted
    max_tokens: int = 1500         # reply cap; small to stay under Groq free-tier TPM
    request_timeout: float = 180.0 # be patient; cloud calls + retries
    # Resilience for the API itself: retry 429 (rate limit) / 413 (too large) / 5xx
    # with exponential back-off, which also paces us under the free-tier TPM limit.
    max_retries: int = 6
    retry_base: float = 2.5        # wait = retry_base ** attempt seconds
    # Cap on characters of page text sent to the model. The Groq free tier limits
    # tokens-per-minute, and request_tokens + max_tokens must fit under it, so we
    # keep prompts small. Long pages are truncated to this many characters.
    max_input_chars: int = 6000


@dataclass
class Settings:
    """Top-level settings bundle passed around the pipeline."""

    http: HttpSettings = field(default_factory=HttpSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    # Confidence below this is flagged "needs human review" by the validator.
    low_confidence_threshold: float = 0.55

    def ensure_dirs(self) -> None:
        """Create the data/cache directories if they do not yet exist."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)


# A module-level default instance for convenience; callers may build their own.
DEFAULT_SETTINGS = Settings()
