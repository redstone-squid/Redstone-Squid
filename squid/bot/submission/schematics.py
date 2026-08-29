"""Presentation helpers shared by the canonical build schematic workspace."""

from squid.core.i18n import tr
from squid.schematics.application import RenderSkipReason, StoredSchematic
from squid.schematics.domain.models import AutostackLattice, SchematicFormat, SimulationResult, Vector3
from squid.schematics.errors import AmbiguousSimulationInputError

WRITABLE_EXTENSIONS = {
    SchematicFormat.LITEMATIC: "litematic",
    SchematicFormat.SPONGE_SCHEM: "schem",
    SchematicFormat.MCSTRUCTURE: "mcstructure",
}
"""Formats the engine can write; legacy inputs remain read-only."""


def _describe(stored: StoredSchematic, *, locale: str | None, render_skip: RenderSkipReason | None = None) -> str:
    """Render the analysis as a readable card body."""
    metrics = stored.analysis.metrics
    dimensions = metrics.dimensions
    lines = [
        tr("### Schematic for build {id}", id=stored.build_id),
        tr(
            "**Measured size**: {width} x {height} x {length} (bounding volume {volume})",
            width=dimensions.width,
            height=dimensions.height,
            length=dimensions.length,
            volume=metrics.bounding_volume,
        ),
        tr("**Non-air blocks**: {count}", count=metrics.block_count),
        tr("**Distinct block states**: {count}", count=metrics.palette_size),
    ]
    if metrics.source_data_version is not None:
        lines.append(tr("**Data version**: {version}", version=metrics.source_data_version))
    if metrics.declared_author:
        lines.append(tr("**Declared author**: {author}", author=metrics.declared_author))
    if stored.analysis.lattice is not None and stored.analysis.lattice.label:
        lines.append(tr("**Repeating unit**: {label}", label=stored.analysis.lattice.label))
    if stored.simulation_evidence is not None and stored.simulation_evidence.last_piston_tick is not None:
        lines.append(
            tr(
                "**Simulated piston activity**: {ticks} gt (moderator evidence only)",
                ticks=stored.simulation_evidence.last_piston_tick + 1,
            )
        )
    if metrics.signs:
        joined = " / ".join(sign.text.replace("\n", " ") for sign in metrics.signs[:5])
        lines.append(tr("**Signs**: {text}", text=joined))
    if render_skip is not None:
        lines.append(tr("**No preview**: {reason}", reason=tr(render_skip.description)))
    lines.append(
        tr(
            "-# Read by {analyzer}. Block count is not the Door Rules cumulative volume.",
            analyzer=stored.analysis.analyzer_version,
        )
    )
    return "\n".join(lines)


def _parse_position(value: str | None) -> Vector3 | None:
    if value is None:
        return None
    try:
        parts = tuple(int(part) for part in value.replace(",", " ").split())
    except ValueError:
        return None
    if len(parts) != 3:
        return None
    return parts


_CANDIDATE_LIMIT = 20
"""Maximum candidate coordinates worth listing in a Discord response."""


def _describe_input_refusal(error: AmbiguousSimulationInputError, *, locale: str | None) -> str:
    """Say why the simulator refused, then list the inputs it would accept."""
    lines = [tr("### Simulation input not resolved"), error.public_detail()]
    if error.candidates:
        lines.append(tr("**Inputs found in this schematic**:"))
        lines.extend(f"- `{x} {y} {z}`" for x, y, z in error.candidates[:_CANDIDATE_LIMIT])
        if len(error.candidates) > _CANDIDATE_LIMIT:
            lines.append(
                tr(
                    "-# …and {count} more not listed.",
                    count=len(error.candidates) - _CANDIDATE_LIMIT,
                )
            )
    return "\n".join(lines)


def _describe_timing(result: SimulationResult, *, locale: str | None) -> str:
    position = result.input_position or (0, 0, 0)
    lines = [
        tr("### Simulated timing evidence"),
        tr(
            "**Input**: ({x}, {y}, {z}) ({source})",
            x=position[0],
            y=position[1],
            z=position[2],
            source=result.input_source or "unknown",
        ),
    ]
    if result.last_piston_tick is not None:
        lines.append(
            tr(
                "**Last piston movement**: tick {tick} ({duration} gt after input)",
                tick=result.last_piston_tick,
                duration=result.last_piston_tick + 1,
            )
        )
    else:
        lines.append(tr("**Last piston movement**: none observed"))
    lines.extend(
        (
            tr("**Settled**: {tick}", tick=result.settled_tick if result.settled_tick is not None else "no"),
            tr(
                "**Evidence**: {changes} block changes; {pistons} piston events; {redstone} redstone events",
                changes=result.block_changes,
                pistons=result.piston_events,
                redstone=result.redstone_events,
            ),
            tr(
                "**Integrity checks**: {status}",
                status="passed" if result.trustworthy else "inconclusive",
            ),
        )
    )
    lines.extend(f"- {note}" for note in result.notes)
    lines.append(tr("-# Moderator evidence only. This does not alter the human-declared or official record timing."))
    return "\n".join(lines)


def _describe_lattice(lattice: AutostackLattice, *, locale: str | None) -> str:
    cell = tuple(high - low + 1 for low, high in zip(lattice.cell_min, lattice.cell_max, strict=True))
    vectors = ", ".join(f"({x}, {y}, {z})" for x, y, z in lattice.vectors)
    return "\n".join(
        (
            tr("### Detected repeating lattice"),
            tr(
                "**Repeating unit**: {width} x {height} x {length}",
                width=cell[0],
                height=cell[1],
                length=cell[2],
            ),
            tr("**Stack vector(s)**: {vectors}", vectors=vectors),
            tr("**Coverage**: {coverage:.1%}", coverage=lattice.coverage),
            tr("-# Structured expansion evidence only; it does not establish the valid expandable domain."),
        )
    )
