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
    id = stored.build_id
    lines = [tr(tr(t"### Schematic for build {id}"))]
    width = dimensions.width
    height = dimensions.height
    length = dimensions.length
    volume = metrics.bounding_volume
    lines.append(tr(tr(t"**Measured size**: {width} x {height} x {length} (bounding volume {volume})")))
    count = metrics.block_count
    lines.append(tr(tr(t"**Non-air blocks**: {count}")))
    count = metrics.palette_size
    lines.append(tr(tr(t"**Distinct block states**: {count}")))
    if metrics.source_data_version is not None:
        version = metrics.source_data_version
        lines.append(tr(tr(t"**Data version**: {version}")))
    if metrics.declared_author:
        author = metrics.declared_author
        lines.append(tr(tr(t"**Declared author**: {author}")))
    if stored.analysis.lattice is not None and stored.analysis.lattice.label:
        label = stored.analysis.lattice.label
        lines.append(tr(tr(t"**Repeating unit**: {label}")))
    if stored.simulation_evidence is not None and stored.simulation_evidence.last_piston_tick is not None:
        ticks = stored.simulation_evidence.last_piston_tick + 1
        lines.append(tr(tr(t"**Simulated piston activity**: {ticks} gt (moderator evidence only)")))
    if metrics.signs:
        text = " / ".join(sign.text.replace("\n", " ") for sign in metrics.signs[:5])
        lines.append(tr(tr(t"**Signs**: {text}")))
    if render_skip is not None:
        reason = render_skip.description
        lines.append(tr(tr(t"**No preview**: {reason}")))
    analyzer = stored.analysis.analyzer_version
    lines.append(tr(tr(t"-# Read by {analyzer}. Block count is not the Door Rules cumulative volume.")))
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
    lines = [tr(tr(t"### Simulation input not resolved")), error.public_detail()]
    if error.candidates:
        lines.append(tr(tr(t"**Inputs found in this schematic**:")))
        lines.extend(f"- `{x} {y} {z}`" for x, y, z in error.candidates[:_CANDIDATE_LIMIT])
        if len(error.candidates) > _CANDIDATE_LIMIT:
            count = len(error.candidates) - _CANDIDATE_LIMIT
            lines.append(tr(tr(t"-# …and {count} more not listed.")))
    return "\n".join(lines)


def _describe_timing(result: SimulationResult, *, locale: str | None) -> str:
    position = result.input_position or (0, 0, 0)
    x, y, z = position
    source = result.input_source or "unknown"
    lines = [
        tr(tr(t"### Simulated timing evidence")),
        tr(tr(t"**Input**: ({x}, {y}, {z}) ({source})")),
    ]
    if result.last_piston_tick is not None:
        tick = result.last_piston_tick
        duration = result.last_piston_tick + 1
        lines.append(tr(tr(t"**Last piston movement**: tick {tick} ({duration} gt after input)")))
    else:
        lines.append(tr(tr(t"**Last piston movement**: none observed")))
    tick = result.settled_tick if result.settled_tick is not None else "no"
    lines.append(tr(tr(t"**Settled**: {tick}")))
    changes = result.block_changes
    pistons = result.piston_events
    redstone = result.redstone_events
    lines.append(tr(tr(t"**Evidence**: {changes} block changes; {pistons} piston events; {redstone} redstone events")))
    status = "passed" if result.trustworthy else "inconclusive"
    lines.append(tr(tr(t"**Integrity checks**: {status}")))
    lines.extend(f"- {note}" for note in result.notes)
    lines.append(
        tr(tr(t"-# Moderator evidence only. This does not alter the human-declared or official record timing."))
    )
    return "\n".join(lines)


def _describe_lattice(lattice: AutostackLattice, *, locale: str | None) -> str:
    cell = tuple(high - low + 1 for low, high in zip(lattice.cell_min, lattice.cell_max, strict=True))
    vectors = ", ".join(f"({x}, {y}, {z})" for x, y, z in lattice.vectors)
    width, height, length = cell
    coverage = lattice.coverage
    return "\n".join(
        (
            tr(tr(t"### Detected repeating lattice")),
            tr(tr(t"**Repeating unit**: {width} x {height} x {length}")),
            tr(tr(t"**Stack vector(s)**: {vectors}")),
            tr(tr(t"**Coverage**: {coverage:.1%}")),
            tr(tr(t"-# Structured expansion evidence only; it does not establish the valid expandable domain.")),
        )
    )
