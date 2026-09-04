"""Tests for persisted schematic row decoding."""

from collections.abc import Callable

import pytest
from whenever import Instant

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


def lattice() -> dict[str, object]:
    return {
        "mode": "1d",
        "vectors": [[1, 0, 0]],
        "coverage": 0.75,
        "cell_min": [0, 0, 0],
        "cell_max": [1, 1, 1],
        "region_min": [0, 0, 0],
        "region_max": [4, 4, 4],
        "label": "repeat",
    }


def simulation() -> dict[str, object]:
    return {
        "ticks_run": 10,
        "settled_tick": 8,
        "input_position": [1, 2, 3],
        "input_source": "manual",
        "trustworthy": True,
        "samples": [{"tick": 1, "x": 1, "y": 2, "z": 3, "powered": False, "signal_strength": 0}],
        "notes": ["complete"],
    }


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("region_names", "Main"),
        ("region_names", ["Main", 2]),
        ("signs", "not-an-array"),
        ("signs", [{"x": True, "y": 2, "z": 3, "text": "label"}]),
        ("signs", [{"x": 1, "y": 2, "z": 3, "text": 4}]),
        ("sanitization_report", []),
        ("sanitization_report", {"removed": object()}),
        ("lattice", []),
        ("lattice", {**lattice(), "mode": "3d"}),
        ("lattice", {**lattice(), "vectors": [[1, 0, 0], [0, 1, 0]]}),
        ("lattice", {**lattice(), "vectors": [[1, 0]]}),
        ("lattice", {**lattice(), "coverage": 1.1}),
        ("lattice", {**lattice(), "coverage": "almost all"}),
        ("lattice", {**lattice(), "label": 4}),
        ("simulation_evidence", []),
        ("simulation_evidence", {**simulation(), "ticks_run": "ten"}),
        ("simulation_evidence", {**simulation(), "input_source": "automatic"}),
        ("simulation_evidence", {**simulation(), "trustworthy": 1}),
        ("simulation_evidence", {**simulation(), "samples": "none"}),
        (
            "simulation_evidence",
            {
                **simulation(),
                "samples": [{"tick": 1, "x": 1, "y": 2, "z": 3, "powered": 0, "signal_strength": 0}],
            },
        ),
        ("simulation_evidence", {**simulation(), "notes": "complete"}),
        ("simulation_evidence", {**simulation(), "notes": [2]}),
    ],
)
def test_every_persisted_json_shape_and_literal_is_validated(field: str, value: object) -> None:
    stored = row()
    setattr(stored, field, value)
    stored.id = 23

    with pytest.raises(DataIntegrityError) as raised:
        to_stored_schematic(stored, source_format="litematic", byte_size=256)

    assert raised.value.context == {"schematic_id": 23}


def test_valid_persisted_json_is_decoded_without_coercion() -> None:
    stored = row()
    stored.id = 29
    stored.region_names = ["Main", "Control"]
    stored.signs = [{"x": 1, "y": 2, "z": 3, "text": "open"}]
    stored.lattice = lattice()
    stored.simulation_evidence = simulation()
    stored.sanitized_at = Instant.parse_iso("2026-08-31T12:00:00Z")
    stored.sanitizer_version = "sanitizer-1"
    stored.sanitization_report = {"removed": 0, "paths": ["Metadata.Author"]}

    decoded = to_stored_schematic(stored, source_format="litematic", byte_size=256)

    assert decoded.analysis.metrics.region_names == ("Main", "Control")
    assert decoded.analysis.metrics.signs[0].text == "open"
    assert decoded.analysis.lattice is not None
    assert decoded.analysis.lattice.mode == "1d"
    assert decoded.simulation_evidence is not None
    assert decoded.simulation_evidence.trustworthy is True
    assert decoded.publication.sanitization_report == {"removed": 0, "paths": ["Metadata.Author"]}
