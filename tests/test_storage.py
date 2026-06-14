"""Storage + schema: DB round-trip, child-table flattening, coverage maths."""
from __future__ import annotations

from uniagent.schema import FieldResult, UniversityRecord
from uniagent.storage import Storage


def _sample_record() -> UniversityRecord:
    rec = UniversityRecord(slug="test", name="Test U", country="USA", city="Townsville")
    rec.fields["about"] = FieldResult(field="about", data={"founding_year": 1900}, confidence=0.8)
    rec.fields["course_listings"] = FieldResult(
        field="course_listings",
        data=[{"code": "CS101", "title": "Intro", "credits": "3"}],
        confidence=0.7,
    )
    rec.fields["scholarships"] = FieldResult(
        field="scholarships",
        data=[{"name": "Merit Award", "value": "$5000"}],
        confidence=0.6,
    )
    return rec


def test_save_and_get_roundtrip(tmp_path):
    db = Storage(tmp_path / "t.db")
    db.save_record(_sample_record())
    got = db.get_university("test")
    assert got["name"] == "Test U"
    assert got["fields"]["about"]["data"]["founding_year"] == 1900
    db.close()


def test_courses_flattened_into_child_table(tmp_path):
    db = Storage(tmp_path / "t.db")
    db.save_record(_sample_record())
    courses = db.query_courses("test")
    assert len(courses) == 1
    assert courses[0]["code"] == "CS101"
    # search by code substring
    assert db.query_courses(q="CS1")
    db.close()


def test_incremental_page_unchanged(tmp_path):
    db = Storage(tmp_path / "t.db")
    db.record_page("https://x.edu/p", "test", "hash123", "2026-01-01")
    assert db.page_unchanged("https://x.edu/p", "hash123") is True
    assert db.page_unchanged("https://x.edu/p", "different") is False
    db.close()


def test_coverage_counts_nonempty_fields():
    rec = UniversityRecord(slug="x", name="X")
    rec.fields["about"] = FieldResult(field="about", data={"x": 1})
    rec.fields["tuition_fees"] = FieldResult(field="tuition_fees", data=None)
    # 1 non-empty out of 10 canonical fields = 0.1
    assert abs(rec.coverage() - 0.1) < 1e-9


def test_fieldresult_is_empty():
    assert FieldResult(field="x", data=None).is_empty()
    assert FieldResult(field="x", data=[]).is_empty()
    assert not FieldResult(field="x", data={"a": 1}).is_empty()
