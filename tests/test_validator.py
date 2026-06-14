"""Validator: plausibility envelopes, currency cross-check, confidence model."""
from __future__ import annotations

from uniagent.validator import Validator


def test_missing_value_is_flagged_and_zero_confidence():
    v = Validator()
    conf, flags, review = v.validate("about", None, country="USA", base_confidence=0.6)
    assert conf == 0.0
    assert review is True
    assert any(f.startswith("missing") for f in flags)


def test_implausible_founding_year_flagged():
    v = Validator()
    conf, flags, review = v.validate(
        "about", {"founding_year": 3025, "type": "private"},
        country="USA", base_confidence=0.6,
    )
    assert any("implausible" in f for f in flags)
    assert review is True


def test_currency_mismatch_cross_check():
    v = Validator()
    # A US school quoting GBP should trip the currency cross-check.
    fees = [{"program_level": "undergraduate", "international_annual": 50000, "currency": "GBP"}]
    conf, flags, _ = v.validate("tuition_fees", fees, country="USA", base_confidence=0.6)
    assert any("currency" in f and "GBP" in f for f in flags)


def test_currency_match_no_flag():
    v = Validator()
    fees = [{"program_level": "undergraduate", "international_annual": 50000, "currency": "USD"}]
    _, flags, _ = v.validate("tuition_fees", fees, country="USA", base_confidence=0.6)
    assert not any("currency" in f for f in flags)


def test_corroboration_raises_confidence():
    v = Validator()
    data = {"overall_pct": 5.0}
    one = v.validate("acceptance_rate", data, country="USA", base_confidence=0.6, source_count=1)[0]
    two = v.validate("acceptance_rate", data, country="USA", base_confidence=0.6, source_count=2)[0]
    assert two > one  # a second independent source is a positive signal


def test_acceptance_rate_over_100_implausible():
    v = Validator()
    _, flags, _ = v.validate("acceptance_rate", {"overall_pct": 900},
                             country="USA", base_confidence=0.6)
    assert any("implausible" in f for f in flags)


def test_living_costs_total_less_than_rent_flagged():
    v = Validator()
    _, flags, _ = v.validate("living_costs", {"rent": 2000, "total": 1500},
                             country="USA", base_confidence=0.6)
    assert any("total < rent" in f for f in flags)
