"""Expandable source facts shared by persistence and ingestion regression tests."""

from squid.builds.domain.expansion import (
    DoorExpansion,
    ExpansionBranch,
    ExpansionEvidence,
    ExpansionParameter,
    ExpansionTiming,
)


def expansion_document() -> DoorExpansion:
    return DoorExpansion(
        raw_description="XYZ expandable; x alternates 1 then 2 layers; y starts with 1 then repeats 2.",
        parameters=(
            ExpansionParameter(
                name="x",
                meaning="opening width",
                minimum=2,
                maximum_kind="unbounded",
                initial_steps=(),
                repeating_steps=(1, 2),
            ),
            ExpansionParameter(
                name="y",
                meaning="opening height",
                minimum=2,
                maximum_kind="finite",
                maximum=14,
                initial_steps=(1,),
                repeating_steps=(2,),
            ),
            ExpansionParameter(name="z", meaning="opening depth"),
        ),
        branches=(
            ExpansionBranch(
                opening_width="x",
                opening_height="y",
                opening_depth="z",
                volume="(x+3)(y+3)(z+1)",
                domain="x >= 2; 2 <= y <= 14",
                claimed_record_domain="5 < x <= 8 or x > 15",
                timings=(ExpansionTiming(method="opening", expression="2x+1", unit="game_tick"),),
            ),
            ExpansionBranch(label="alternate wiring", volume="ceil(3x/2) + 8", domain="odd x"),
        ),
        evidence=(ExpansionEvidence(source_message_id=10, text="A redstone door"),),
    )
