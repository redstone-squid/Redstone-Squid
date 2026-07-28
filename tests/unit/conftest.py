"""Fixtures used only by legacy unit tests."""

import pytest

from squid.db.schema import BuildCategory, Restriction, RestrictionRecord, VersionRecord


@pytest.fixture
def sample_version_json_data() -> list[VersionRecord]:
    """Return representative serialized versions."""
    return [
        {"id": 1, "edition": "Java", "major_version": 1, "minor_version": 14, "patch_number": 0},
        {"id": 2, "edition": "Java", "major_version": 1, "minor_version": 15, "patch_number": 0},
        {"id": 3, "edition": "Java", "major_version": 1, "minor_version": 16, "patch_number": 0},
        {"id": 4, "edition": "Java", "major_version": 1, "minor_version": 16, "patch_number": 1},
        {"id": 5, "edition": "Java", "major_version": 1, "minor_version": 17, "patch_number": 0},
        {"id": 6, "edition": "Java", "major_version": 1, "minor_version": 17, "patch_number": 1},
        {"id": 7, "edition": "Java", "major_version": 1, "minor_version": 18, "patch_number": 0},
        {"id": 8, "edition": "Java", "major_version": 1, "minor_version": 19, "patch_number": 0},
        {"id": 9, "edition": "Java", "major_version": 1, "minor_version": 19, "patch_number": 1},
        {"id": 10, "edition": "Java", "major_version": 1, "minor_version": 19, "patch_number": 2},
        {"id": 11, "edition": "Java", "major_version": 1, "minor_version": 20, "patch_number": 0},
    ]


@pytest.fixture
def sample_restriction_json_data() -> list[RestrictionRecord]:
    """Return representative serialized restrictions."""
    return [
        {"id": 1, "name": "No pistons", "type": "component", "build_category": BuildCategory.DOOR},
        {"id": 2, "name": "No observers", "type": "component", "build_category": BuildCategory.DOOR},
        {"id": 3, "name": "No redstone dust", "type": "component", "build_category": BuildCategory.DOOR},
        {"id": 4, "name": "1-wide", "type": "wiring-placement", "build_category": BuildCategory.DOOR},
        {"id": 5, "name": "2-wide", "type": "miscellaneous", "build_category": BuildCategory.DOOR},
    ]


@pytest.fixture
def sample_restriction_data() -> list[Restriction]:
    """Return representative persisted restrictions."""
    restrictions = [
        Restriction(name="No pistons", type="component", build_category="Door"),
        Restriction(name="No observers", type="component", build_category="Door"),
        Restriction(name="No redstone dust", type="component", build_category="Door"),
        Restriction(name="1-wide", type="wiring-placement", build_category="Door"),
        Restriction(name="2-wide", type="miscellaneous", build_category="Door"),
    ]
    for i, restriction in enumerate(restrictions):
        restriction.id = i + 1
    return restrictions
