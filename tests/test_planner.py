"""Planner: adaptive link discovery and seed -> task expansion."""
from __future__ import annotations

from uniagent.planner import Planner, UniversityConfig


def test_discover_matches_keywords():
    links = [
        ("Tuition & Fees", "https://x.edu/cost/tuition"),
        ("Apply for a Scholarship", "https://x.edu/aid/scholarships"),
        ("Athletics", "https://x.edu/sports"),
    ]
    found = Planner.discover(links, ["tuition_fees", "scholarships"])
    assert "https://x.edu/cost/tuition" in found["tuition_fees"]
    assert "https://x.edu/aid/scholarships" in found["scholarships"]
    # An irrelevant link must not be attached to any field.
    assert all("sports" not in u for urls in found.values() for u in urls)


def test_discover_caps_candidates_per_field():
    links = [(f"tuition page {i}", f"https://x.edu/tuition/{i}") for i in range(10)]
    found = Planner.discover(links, ["tuition_fees"])
    assert len(found["tuition_fees"]) <= 3  # noise cap


def test_plan_orders_about_first_courses_last():
    cfg = UniversityConfig(
        slug="x", name="X University",
        seeds={
            "course_listings": ["https://x.edu/courses"],
            "about": ["https://x.edu/about"],
            "tuition_fees": ["https://x.edu/tuition"],
        },
    )
    tasks = Planner().plan(cfg)
    fields = [t.field for t in tasks]
    assert fields[0] == "about"
    assert fields[-1] == "course_listings"


def test_plan_parses_dict_seed_options():
    cfg = UniversityConfig(
        slug="x", name="X",
        seeds={"course_listings": {"urls": ["https://x.edu/c"], "paginate": True, "max_pages": 4}},
    )
    task = Planner().plan(cfg)[0]
    assert task.paginate is True
    assert task.max_pages == 4
    assert task.urls == ["https://x.edu/c"]
