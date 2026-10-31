"""Check the serialized-output contract required before enabling schematic submissions.

Run against a clean Python environment containing only the release under investigation:
``python scripts/check_sanitizer_gate.py``. Failure keeps the release gate closed; passing
this reproducer still requires the cross-format and policy tests in the sanitizer plan.
Upstream report: https://github.com/Schem-at/Nucleation/issues/39.
"""

import base64
import json
from importlib.metadata import version

import nucleation
from nucleation import TransformPlan, apply_transform


def source(kind: str) -> bytes:
    """Build small, reviewable fixtures without reading a user schematic."""
    schematic = nucleation.Schematic.create("submission-sanitizer-gate")
    schematic.set_block(0, 0, 0, "minecraft:stone")
    if kind == "entity":
        schematic.add_entity_from_snbt(
            '{id:"minecraft:armor_stand",Pos:[0.0d,1.0d,0.0d],Invisible:1b,NoGravity:1b,Small:1b,Marker:1b}'
        )
    elif kind == "block_entity":
        schematic.set_block(1, 0, 0, "minecraft:chest")
        schematic.set_block_entity(
            1, 0, 0, "minecraft:chest", '{id:"minecraft:chest",CustomName:"x",Lock:"y",LootTable:"z",LootTableSeed:1L}'
        )
    return base64.b64decode(schematic.to_schematic_b64())


def sanitize(raw: bytes) -> bytes:
    """Apply the upstream plan and serialize canonical Sponge output."""
    schematic = nucleation.Schematic.from_data(raw)
    report = apply_transform(schematic, TransformPlan.registry_safe())
    if report.rejected or report.quarantined:
        message = "The release gate fixture was rejected or quarantined"
        raise RuntimeError(message)
    return base64.b64decode(schematic.to_schematic_b64())


def main() -> int:
    """Print bounded facts and fail if serialized output cannot be content-addressed."""
    print(json.dumps({"nucleation": version("nucleation")}))
    passed = True
    for kind in ("blocks", "entity", "block_entity"):
        raw = source(kind)
        outputs = {sanitize(raw) for _ in range(8)}
        idempotent = all(sanitize(output) == output for output in outputs)
        print(json.dumps({"fixture": kind, "distinct_outputs": len(outputs), "idempotent": idempotent}))
        passed &= len(outputs) == 1 and idempotent
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
