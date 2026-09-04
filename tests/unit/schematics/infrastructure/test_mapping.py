"""Tests for persisted schematic row decoding."""

from collections.abc import Callable

import pytest

from squid.core.errors import DataIntegrityError
from squid.schematics.infrastructure.mapping import to_stored_schematic
from squid.schematics.infrastructure.models import BuildSchematic


def row() -> BuildSchematic:
    return BuildSchematic(
        build_id=7,
        file_sha256="0" * 64,
        is_primary=True,
        original_filename="door.litematic",
        width=3,
        height=4,
        length=5,
        allocated_width=3,
        allocated_height=4,
        allocated_length=5,
        block_count=42,
        bounding_volume=60,
        entity_count=0,
        palette_size=3,
        region_names=["Main"],
        signs=[],
        analyzer_version="nucleation-test",
        analysis_schema_version=1,
    )


@pytest.mark.parametrize(
    ("source_format", "mutate"),
    [
        ("unknown", lambda _row: None),
        ("litematic", lambda stored: setattr(stored, "visibility", "world_readable")),
        ("litematic", lambda stored: setattr(stored, "signs", [{"x": 1}])),
        ("litematic", lambda stored: setattr(stored, "lattice", {"mode": "1d"})),
        ("litematic", lambda stored: setattr(stored, "simulation_evidence", {"ticks_run": "never"})),
    ],
)
def test_corrupt_persisted_values_raise_structured_error(
    source_format: str, mutate: Callable[[BuildSchematic], None]
) -> None:
    stored = row()
    mutate(stored)
    stored.id = 19

    with pytest.raises(DataIntegrityError) as raised:
        to_stored_schematic(stored, source_format=source_format, byte_size=256)

    assert raised.value.context == {"schematic_id": 19}
