"""Material inventories survive analysis storage and older analyses remain unknown."""

import base64

import pytest

pytest.importorskip("nucleation")

from squid.schematics.domain.models import SchematicLimits
from squid.schematics.infrastructure import nucleation_adapter, wire


def test_native_inventory_round_trips_without_counting_unused_palette_entries() -> None:
    schematic = nucleation_adapter.nucleation.Schematic.create("inventory")
    schematic.set_block(0, 0, 0, "minecraft:slime_block")
    schematic.set_block(0, 0, 0, "minecraft:stone")
    schematic.set_block(1, 0, 0, "minecraft:stone")
    data = base64.b64decode(schematic.to_schematic_b64())
    analysis = nucleation_adapter.analyze(data, limits=SchematicLimits())
    assert analysis.metrics.block_counts == {"minecraft:stone": 2}
    payload = dict(wire.encode_analysis(analysis))
    assert wire.decode_analysis(payload) == analysis
    payload["metrics"].pop("block_counts")
    assert wire.decode_analysis(payload).metrics.block_counts is None
