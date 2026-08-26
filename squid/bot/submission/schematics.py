"""Discord commands for inspecting and re-encoding a build's schematic.

The commands stay registered even where the optional engine is absent, and answer with one
plain sentence instead. A command that silently does not exist is indistinguishable from a bot
outage from the user's side; a command that says "schematic support is not enabled here" is
not.
"""

import io
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext.commands import Context

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.i18n import resolve_locale, t
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.ui import render_presentation, reply_presentation, send_component, text_layout
from squid.bot.utils.autocomplete import autocompletes
from squid.bot.utils.permissions import requires
from squid.bot.utils.visibility import personal
from squid.builds.application import BuildService
from squid.core.i18n import _
from squid.permissions.domain.catalogue import BUILD_SCHEMATIC_DETECT_LATTICE, BUILD_SCHEMATIC_MEASURE_TIMING
from squid.schematics.application import (
    ConvertRequest,
    RenderSkipReason,
    SchematicService,
    StoredSchematic,
    summarise_losses,
)
from squid.schematics.application.commands import MIN_RENDER_EXTENT
from squid.schematics.domain.models import AutostackLattice, SchematicFormat, SimulationResult, Vector3
from squid.schematics.errors import AmbiguousSimulationInputError, SchematicRenderRefusedError

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

WRITABLE_EXTENSIONS = {
    SchematicFormat.LITEMATIC: "litematic",
    SchematicFormat.SPONGE_SCHEM: "schem",
    SchematicFormat.MCSTRUCTURE: "mcstructure",
}
"""The formats the engine can write. Legacy `.schematic` and structure `.nbt` are read-only, so
they are accepted as uploads but never offered as a download target."""


class _DownloadDocument(sl.Component):
    def __init__(self, label: sl.TextLike, asset: sl.document.Asset, *, description: sl.TextLike | None = None) -> None:
        self.label = label
        self.asset = asset
        self.description = description

    def render(self) -> sl.LayoutNode:
        return sl.download(self.label, self.asset, key="download", description=self.description)


class BuildSchematicCommands[BotT: "squid.bot.app.RedstoneSquid"](BuildCommandGroup[BotT]):
    """Read machine-verified facts out of a build's attached schematic."""

    bot: BotT
    builds: BuildService

    @property
    def schematics(self) -> SchematicService:
        return self.bot.services.schematics

    @BuildCommandGroup.build_hybrid_group.group(name="schematic")  # type: ignore
    async def schematic_group(self, ctx: Context[BotT]) -> None:
        """Inspect, download, and convert a build's schematic."""
        await ctx.send_help("build schematic")

    @autocompletes(build_id="builds")
    @schematic_group.command(name="info")  # type: ignore
    @app_commands.describe(build_id=app_commands.locale_str(_("The submission ID to inspect.")))
    async def schematic_info(self, ctx: Context[BotT], build_id: int) -> None:
        """Show what the engine read out of the build's schematic."""
        await ctx.defer()
        locale = await resolve_locale(ctx, self.bot.services.settings)
        stored = await self._primary_or_explain(ctx, build_id, locale=locale)
        if stored is None:
            return
        await reply_presentation(
            ctx,
            render_presentation(
                [
                    sl.primitives.Text(
                        _describe(stored, locale=locale, render_skip=self.schematics.explain_render_skip(stored))
                    )
                ],
                locale=locale,
            ),
        )

    @autocompletes(build_id="builds")
    @schematic_group.command(name="download")  # type: ignore
    @app_commands.describe(
        build_id=app_commands.locale_str(_("The submission ID whose schematic to download.")),
        file_format=app_commands.locale_str(_("The file format to convert to.")),
    )
    async def schematic_download(
        self, ctx: Context[BotT], build_id: int, file_format: SchematicFormat = SchematicFormat.LITEMATIC
    ) -> None:
        """Download the build's schematic, converted to another format if asked."""
        await ctx.defer()
        locale = await resolve_locale(ctx, self.bot.services.settings)
        if file_format not in WRITABLE_EXTENSIONS:
            await _say(
                ctx,
                t(
                    locale,
                    _("The engine can only write these formats: {formats}."),
                    formats=", ".join(sorted(fmt.value for fmt in WRITABLE_EXTENSIONS)),
                ),
            )
            return

        stored = await self._primary_or_explain(ctx, build_id, locale=locale)
        if stored is None:
            return

        data, _losses = await self.schematics.convert(build_id, ConvertRequest(target_format=file_format))
        name = f"build-{build_id}.{WRITABLE_EXTENSIONS[file_format]}"
        await send_component(
            ctx,
            _DownloadDocument(
                t(locale, _("Download schematic")),
                sl.document.Asset("schematic", name, "application/octet-stream", sl.document.InlineAsset(data)),
            ),
            access=sd.Everyone(),
            locale=locale,
        )

    @autocompletes(build_id="builds")
    @schematic_group.command(name="render")  # type: ignore
    @app_commands.describe(
        build_id=app_commands.locale_str(_("The submission ID whose schematic to render.")),
        yaw=app_commands.locale_str(_("Camera yaw in degrees. Omit for the default view.")),
        pitch=app_commands.locale_str(_("Camera pitch in degrees. Omit for the default view.")),
        size=app_commands.locale_str(_("Image width and height in pixels.")),
    )
    async def schematic_render(
        self,
        ctx: Context[BotT],
        build_id: int,
        yaw: app_commands.Range[float, -360.0, 360.0] | None = None,
        pitch: app_commands.Range[float, -90.0, 90.0] | None = None,
        size: app_commands.Range[int, MIN_RENDER_EXTENT, 1536] | None = None,
    ) -> None:
        """Render the build's schematic and post the image."""
        await ctx.defer()
        locale = await resolve_locale(ctx, self.bot.services.settings)
        stored = await self._primary_or_explain(ctx, build_id, locale=locale)
        if stored is None:
            return
        try:
            rendered = await self.schematics.render_now(
                build_id,
                request=self.schematics.render_recipe(width=size, height=size, yaw=yaw, pitch=pitch),
            )
        except SchematicRenderRefusedError as error:
            # A refusal names a fact about the file — unsanitized, too big, already fatal to
            # the engine — that the user can neither retry away nor act on, so it is a
            # sentence rather than an error card telling them to report it.
            await _say(ctx, error.localized_public_detail(locale))
            return
        await reply_presentation(
            ctx,
            text_layout(t(locale, _("Rendered schematic for build {id}."), id=build_id)),
            files=[discord.File(io.BytesIO(rendered.png), filename=f"build-{build_id}-render.png")],
        )

    @autocompletes(build_id="builds", version="approved_source_versions")
    @schematic_group.command(name="convert")  # type: ignore
    @app_commands.describe(
        build_id=app_commands.locale_str(_("The submission ID whose schematic to convert.")),
        data_version=app_commands.locale_str(_("The Minecraft data version to target, e.g. 2586.")),
        version=app_commands.locale_str(_("A Minecraft version name to target instead, e.g. Java 1.16.5.")),
    )
    async def schematic_convert(
        self,
        ctx: Context[BotT],
        build_id: int,
        data_version: int | None = None,
        version: str | None = None,
    ) -> None:
        """Retarget the schematic at another Minecraft version and report what the change cost."""
        await ctx.defer()
        locale = await resolve_locale(ctx, self.bot.services.settings)
        if data_version is None and version is None:
            await _say(ctx, t(locale, _("Give either a data version or a Minecraft version to convert to.")))
            return

        stored = await self._primary_or_explain(ctx, build_id, locale=locale)
        if stored is None:
            return

        data, losses = await self.schematics.convert(
            build_id,
            ConvertRequest(target_format=SchematicFormat.LITEMATIC, target_data_version=data_version),
            version_label=version,
        )
        await send_component(
            ctx,
            _DownloadDocument(
                t(locale, _("Download converted schematic")),
                sl.document.Asset(
                    "schematic",
                    f"build-{build_id}-converted.litematic",
                    "application/octet-stream",
                    sl.document.InlineAsset(data),
                ),
                description=f"{t(locale, _('Conversion report:'))} {summarise_losses(losses)}",
            ),
            access=sd.Everyone(),
            locale=locale,
        )

    @autocompletes(build_id="builds")
    @schematic_group.command(name="measure-timing")  # type: ignore
    @requires(BUILD_SCHEMATIC_MEASURE_TIMING)
    @app_commands.describe(
        build_id=app_commands.locale_str(_("The submission ID whose schematic to simulate.")),
        input_position=app_commands.locale_str(_("An input block as x y z, required when several exist.")),
    )
    async def measure_timing(
        self,
        ctx: Context[BotT],
        build_id: int,
        input_position: str | None = None,
    ) -> None:
        """Measure moderator-facing piston timing without changing the submitted value."""
        await ctx.defer(ephemeral=personal(ctx))
        locale = await resolve_locale(ctx, self.bot.services.settings)
        stored = await self._primary_or_explain(ctx, build_id, locale=locale, ephemeral=personal(ctx))
        if stored is None:
            return
        position = _parse_position(input_position)
        if input_position is not None and position is None:
            await _say(
                ctx,
                t(locale, _("Input position must contain three integers, for example `12 5 -3`.")),
                ephemeral=personal(ctx),
            )
            return
        try:
            result = await self.schematics.measure_timing(build_id, input_position=position)
        except AmbiguousSimulationInputError as error:
            # The generic error handler would show the refusal without ever naming what to
            # choose between, leaving the moderator to guess coordinates out of a binary file.
            await _say(ctx, _describe_input_refusal(error, locale=locale), ephemeral=personal(ctx))
            return
        await _say(ctx, _describe_timing(result, locale=locale), ephemeral=personal(ctx))

    @autocompletes(build_id="builds")
    @schematic_group.command(name="detect-lattice")  # type: ignore
    @requires(BUILD_SCHEMATIC_DETECT_LATTICE)
    @app_commands.describe(build_id=app_commands.locale_str(_("The submission ID to inspect for repetition.")))
    async def detect_lattice(self, ctx: Context[BotT], build_id: int) -> None:
        """Show the repeating unit detected during schematic analysis."""
        await ctx.defer(ephemeral=personal(ctx))
        locale = await resolve_locale(ctx, self.bot.services.settings)
        stored = await self._primary_or_explain(ctx, build_id, locale=locale, ephemeral=personal(ctx))
        if stored is None:
            return
        lattice = await self.schematics.detect_lattice(build_id)
        if lattice is None:
            await _say(
                ctx, t(locale, _("No repeating lattice was detected in this schematic.")), ephemeral=personal(ctx)
            )
            return
        await _say(ctx, _describe_lattice(lattice, locale=locale), ephemeral=personal(ctx))

    async def _primary_or_explain(
        self,
        ctx: Context[BotT],
        build_id: int,
        *,
        locale: str | None,
        ephemeral: bool = False,
    ) -> StoredSchematic | None:
        """Fetch the build's primary schematic, explaining plainly when there is none.

        Both "no engine here" and "this build has no schematic" are ordinary states — most
        builds predate the feature entirely — so they are answered with a sentence rather than
        raised as errors.
        """
        if not self.schematics.available:
            await _say(ctx, t(locale, _("Schematic support is not enabled on this instance.")), ephemeral=ephemeral)
            return None
        stored = await self.schematics.primary_for_build(build_id)
        if stored is None:
            await _say(ctx, t(locale, _("Build `{id}` has no schematic attached."), id=build_id), ephemeral=ephemeral)
            return None
        return stored


async def _say(ctx: Context[squid.bot.app.RedstoneSquid], message: str, *, ephemeral: bool = False) -> None:
    await reply_presentation(ctx, text_layout(message), visibility="personal" if ephemeral else "public")


def _describe(stored: StoredSchematic, *, locale: str | None, render_skip: RenderSkipReason | None = None) -> str:
    """Render the analysis as a readable card body."""
    metrics = stored.analysis.metrics
    dimensions = metrics.dimensions
    lines = [
        t(locale, _("### Schematic for build {id}"), id=stored.build_id),
        t(
            locale,
            _("**Measured size**: {width} x {height} x {length} (bounding volume {volume})"),
            width=dimensions.width,
            height=dimensions.height,
            length=dimensions.length,
            volume=metrics.bounding_volume,
        ),
        t(locale, _("**Non-air blocks**: {count}"), count=metrics.block_count),
        t(locale, _("**Distinct block states**: {count}"), count=metrics.palette_size),
    ]
    if metrics.source_data_version is not None:
        lines.append(t(locale, _("**Data version**: {version}"), version=metrics.source_data_version))
    if metrics.declared_author:
        lines.append(t(locale, _("**Declared author**: {author}"), author=metrics.declared_author))
    if stored.analysis.lattice is not None and stored.analysis.lattice.label:
        lines.append(t(locale, _("**Repeating unit**: {label}"), label=stored.analysis.lattice.label))
    if stored.simulation_evidence is not None and stored.simulation_evidence.last_piston_tick is not None:
        lines.append(
            t(
                locale,
                _("**Simulated piston activity**: {ticks} gt (moderator evidence only)"),
                ticks=stored.simulation_evidence.last_piston_tick + 1,
            )
        )
    if metrics.signs:
        joined = " / ".join(sign.text.replace("\n", " ") for sign in metrics.signs[:5])
        lines.append(t(locale, _("**Signs**: {text}"), text=joined))
    if render_skip is not None:
        # "There is no preview" is otherwise indistinguishable from "the preview has not been
        # generated yet", and the two call for completely different responses from a moderator.
        lines.append(t(locale, _("**No preview**: {reason}"), reason=t(locale, render_skip.description)))
    # Stated on the card because the two numbers are easy to confuse and one of them decides
    # official records: cumulative volume has hallway, frame, and hitbox exceptions that no
    # static read of a file can apply.
    lines.append(
        t(
            locale,
            _("-# Read by {analyzer}. Block count is not the Door Rules cumulative volume."),
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
"""How many candidate coordinates are worth listing. A schematic with more controls than this
is one the moderator has to narrow down by looking at the build, not by reading a wall of
coordinates that would not fit in a Discord message anyway."""


def _describe_input_refusal(error: AmbiguousSimulationInputError, *, locale: str | None) -> str:
    """Say why the simulator refused, then list the inputs it would accept."""
    lines = [
        t(locale, _("### Simulation input not resolved")),
        error.localized_public_detail(locale),
    ]
    if error.candidates:
        lines.append(t(locale, _("**Inputs found in this schematic**:")))
        lines.extend(f"- `{x} {y} {z}`" for x, y, z in error.candidates[:_CANDIDATE_LIMIT])
        if len(error.candidates) > _CANDIDATE_LIMIT:
            lines.append(
                t(
                    locale,
                    _("-# …and {count} more not listed."),
                    count=len(error.candidates) - _CANDIDATE_LIMIT,
                )
            )
    return "\n".join(lines)


def _describe_timing(result: SimulationResult, *, locale: str | None) -> str:
    position = result.input_position or (0, 0, 0)
    lines = [
        t(locale, _("### Simulated timing evidence")),
        t(
            locale,
            _("**Input**: ({x}, {y}, {z}) ({source})"),
            x=position[0],
            y=position[1],
            z=position[2],
            source=result.input_source or "unknown",
        ),
    ]
    if result.last_piston_tick is not None:
        lines.append(
            t(
                locale,
                _("**Last piston movement**: tick {tick} ({duration} gt after input)"),
                tick=result.last_piston_tick,
                duration=result.last_piston_tick + 1,
            )
        )
    else:
        lines.append(t(locale, _("**Last piston movement**: none observed")))
    lines.extend(
        (
            t(locale, _("**Settled**: {tick}"), tick=result.settled_tick if result.settled_tick is not None else "no"),
            t(
                locale,
                _("**Evidence**: {changes} block changes; {pistons} piston events; {redstone} redstone events"),
                changes=result.block_changes,
                pistons=result.piston_events,
                redstone=result.redstone_events,
            ),
            t(
                locale,
                _("**Integrity checks**: {status}"),
                status="passed" if result.trustworthy else "inconclusive",
            ),
        )
    )
    lines.extend(f"- {note}" for note in result.notes)
    lines.append(
        t(
            locale,
            _("-# Moderator evidence only. This does not alter the human-declared or official record timing."),
        )
    )
    return "\n".join(lines)


def _describe_lattice(lattice: AutostackLattice, *, locale: str | None) -> str:
    cell = tuple(high - low + 1 for low, high in zip(lattice.cell_min, lattice.cell_max, strict=True))
    vectors = ", ".join(f"({x}, {y}, {z})" for x, y, z in lattice.vectors)
    return "\n".join(
        (
            t(locale, _("### Detected repeating lattice")),
            t(
                locale,
                _("**Repeating unit**: {width} x {height} x {length}"),
                width=cell[0],
                height=cell[1],
                length=cell[2],
            ),
            t(locale, _("**Stack vector(s)**: {vectors}"), vectors=vectors),
            t(locale, _("**Coverage**: {coverage:.1%}"), coverage=lattice.coverage),
            t(
                locale,
                _("-# Structured expansion evidence only; it does not establish the valid expandable domain."),
            ),
        )
    )
