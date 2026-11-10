"""Presentation helpers shared by the canonical build schematic workspace."""

from squid.core.i18n import tr
from squid.schematics.application import RenderSkipReason, StoredSchematic
from squid.schematics.domain.models import AutostackLattice, SchematicFormat, SimulationResult, Vector3
from squid.schematics.errors import AmbiguousSimulationInputError
from squid_i18n import NUMBER
from squid_ui.text import Message, TextLike

WRITABLE_EXTENSIONS = {
    SchematicFormat.LITEMATIC: "litematic",
    SchematicFormat.SPONGE_SCHEM: "schem",
    SchematicFormat.MCSTRUCTURE: "mcstructure",
}
"""Formats the engine can write; legacy inputs remain read-only."""


def _describe(stored: StoredSchematic, *, render_skip: RenderSkipReason | None = None) -> Message:
    """Render the analysis as a readable card body."""
    metrics = stored.analysis.metrics
    id = stored.build_id
    width, height, length = metrics.dimensions.width, metrics.dimensions.height, metrics.dimensions.length
    volume = metrics.bounding_volume
    blocks = metrics.block_count
    states = metrics.palette_size
    lines: list[TextLike] = [
        tr(t"""
            schematic-build-measured-size =
                ### Schematic for build {(id_text := str(id))}
                {{ "*" }}*Measured size**: {width} x {height} x {length} (bounding volume {volume})
                {{ "*" }}*Non-air blocks**: {blocks}
                {{ "*" }}*Distinct block states**: {states}
            """)  # noqa: F841 - captured text identifier
    ]
    if metrics.source_data_version is not None:
        version = metrics.source_data_version
        lines.append(tr(t"bot-submission-schematics-data-version = **Data version**: {version}"))
    if metrics.declared_author:
        author = metrics.declared_author
        lines.append(tr(t"bot-submission-schematics-declared-author = **Declared author**: {author}"))
    if stored.analysis.lattice is not None and stored.analysis.lattice.label:
        label = stored.analysis.lattice.label
        lines.append(tr(t"bot-submission-schematics-repeating-unit = **Repeating unit**: {label}"))
    if stored.simulation_evidence is not None and stored.simulation_evidence.last_piston_tick is not None:
        ticks = stored.simulation_evidence.last_piston_tick + 1
        lines.append(
            tr(
                t"schematic-simulated-piston-evidence = **Simulated "
                t"piston activity**: {ticks} gt (moderator evidence only)"
            )
        )
    if metrics.signs:
        text = " / ".join(sign.text.replace("\n", " ") for sign in metrics.signs[:5])
        lines.append(tr(t"bot-submission-schematics-signs = **Signs**: {text}"))
    if render_skip is not None:
        reason = render_skip.description
        lines.append(tr(t"bot-submission-schematics-no-preview = **No preview**: {reason}"))
    analyzer = stored.analysis.analyzer_version
    lines.append(
        tr(
            t"schematic-block-count-disclaimer = -# Read by {analyzer}. Block count is not "
            t"the Door Rules cumulative volume."
        )
    )
    return Message.join("\n", lines)


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


def _describe_input_refusal(error: AmbiguousSimulationInputError) -> Message:
    """Say why the simulator refused, then list the inputs it would accept."""
    lines: list[TextLike] = [
        tr(t"schematic-simulation-input-unresolved = ### Simulation input not resolved"),
        error.public_text(),
    ]
    if error.candidates:
        lines.append(tr(t"schematic-inputs-heading = **Inputs found in this schematic**:"))
        lines.extend(f"- `{x} {y} {z}`" for x, y, z in error.candidates[:_CANDIDATE_LIMIT])
        if len(error.candidates) > _CANDIDATE_LIMIT:
            count = len(error.candidates) - _CANDIDATE_LIMIT
            lines.append(tr(t"bot-submission-schematics-and-more-not-listed = -# …and {count} more not listed."))
    return Message.join("\n", lines)


def _describe_timing(result: SimulationResult) -> Message:
    x, y, z = result.input_position or (0, 0, 0)
    source = result.input_source or "unknown"
    if result.last_piston_tick is not None:
        tick = result.last_piston_tick
        duration = result.last_piston_tick + 1
        movement = tr(t"bot-submission-schematics-tick-gt-after-input = tick {tick} ({duration} gt after input)")
    else:
        movement = tr(t"bot-submission-schematics-none-observed = none observed")
    settled = result.settled_tick if result.settled_tick is not None else tr(t"bot-submission-schematics-no = no")
    changes = result.block_changes
    pistons = result.piston_events
    redstone = result.redstone_events
    status = (
        tr(t"bot-submission-schematics-passed = passed")
        if result.trustworthy
        else tr(t"bot-submission-schematics-inconclusive = inconclusive")
    )
    summary = tr(t"""
        schematic-simulated-timing-evidence =
            ### Simulated timing evidence
            {{ "*" }}*Input**: ({x}, {y}, {z}) ({source})
            {{ "*" }}*Last piston movement**: {movement}
            {{ "*" }}*Settled**: {settled}
            {{ "*" }}*Evidence**: {changes} block changes; {pistons} piston events; {redstone} redstone events
            {{ "*" }}*Integrity checks**: {status}
        """)
    footer = tr(
        t"schematic-moderator-evidence-disclaimer = -# Moderator evidence "
        t"only. This does not alter the human-declared or official record timing."
    )
    return Message.join("\n", [summary, *(f"- {note}" for note in result.notes), footer])


def _describe_lattice(lattice: AutostackLattice) -> Message:
    width, height, length = (high - low + 1 for low, high in zip(lattice.cell_min, lattice.cell_max, strict=True))
    vectors = ", ".join(f"({x}, {y}, {z})" for x, y, z in lattice.vectors)
    coverage = lattice.coverage
    return tr(t"""
        schematic-repeating-lattice-summary =
            ### Detected repeating lattice
            {{ "*" }}*Repeating unit**: {width} x {height} x {length}
            {{ "*" }}*Stack vector(s)**: {vectors}
            {{ "*" }}*Coverage**: {NUMBER(coverage, minimum_fraction_digits=1, maximum_fraction_digits=1, style="percent")}
            -# Structured expansion evidence only; it does not establish the valid expandable domain.
        """)
