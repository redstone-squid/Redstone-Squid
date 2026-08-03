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

from squid.bot.i18n import resolve_locale, t
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.utils.components import StaticLayout, no_mentions, text_layout
from squid.builds.application import BuildService
from squid.core.i18n import _
from squid.schematics.application import ConvertRequest, SchematicService, StoredSchematic, summarise_losses
from squid.schematics.domain.models import SchematicFormat

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

    @schematic_group.command(name="info")
    @app_commands.describe(build_id=app_commands.locale_str(_("The submission ID to inspect.")))
    async def schematic_info(self, ctx: Context[BotT], build_id: int) -> None:
        """Show what the engine read out of the build's schematic."""
        await ctx.defer()
        locale = await resolve_locale(ctx, self.bot.services.settings)
        stored = await self._primary_or_explain(ctx, build_id, locale=locale)
        if stored is None:
            return
        await ctx.send(
            view=StaticLayout(discord.ui.TextDisplay(_describe(stored, locale=locale))),
            allowed_mentions=no_mentions(),
        )

    @schematic_group.command(name="download")
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
        await ctx.send(
            file=discord.File(io.BytesIO(data), filename=f"build-{build_id}.{WRITABLE_EXTENSIONS[file_format]}"),
            allowed_mentions=no_mentions(),
        )

    @schematic_group.command(name="convert")
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
        await ctx.send(
            view=text_layout(f"{t(locale, _('Conversion report:'))}\n{summarise_losses(losses)}"),
            file=discord.File(io.BytesIO(data), filename=f"build-{build_id}-converted.litematic"),
            allowed_mentions=no_mentions(),
        )

    async def _primary_or_explain(
        self, ctx: Context[BotT], build_id: int, *, locale: str | None
    ) -> StoredSchematic | None:
        """Fetch the build's primary schematic, explaining plainly when there is none.

        Both "no engine here" and "this build has no schematic" are ordinary states — most
        builds predate the feature entirely — so they are answered with a sentence rather than
        raised as errors.
        """
        if not self.schematics.available:
            await _say(ctx, t(locale, _("Schematic support is not enabled on this instance.")))
            return None
        stored = await self.schematics.primary_for_build(build_id)
        if stored is None:
            await _say(ctx, t(locale, _("Build `{id}` has no schematic attached."), id=build_id))
            return None
        return stored


async def _say(ctx: Context["squid.bot.app.RedstoneSquid"], message: str) -> None:
    await ctx.send(view=text_layout(message), allowed_mentions=no_mentions())


def _describe(stored: StoredSchematic, *, locale: str | None) -> str:
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
    if metrics.signs:
        joined = " / ".join(sign.text.replace("\n", " ") for sign in metrics.signs[:5])
        lines.append(t(locale, _("**Signs**: {text}"), text=joined))
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
