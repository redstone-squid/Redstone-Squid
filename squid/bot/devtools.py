"""Owner-only runtime devtools: `!dev ui` over the live mount registry.

Prefix commands rather than hybrids, and loaded only in development mode: these answer
questions about this process, they take a mount id nobody can guess, and putting them in the
application command tree would cost a sync and show them to everyone.

Everything they print — component state, planner events, a resolved scene — is internal
detail, so every reply goes out through `Private`: ephemeral where the transport allows it,
a direct message otherwise, never the channel.
"""

import io
from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from discord.ext.commands import Context

import squid_layouts as sl
from squid.bot.devtools_view import SESSION_SECONDS, MountInspector, scene_attachment
from squid.bot.routes import router
from squid.bot.ui import Private, create_mount, destination, error_layout, render_static
from squid.bot.utils.visibility import deliver_privately

if TYPE_CHECKING:
    import squid.bot.app

_REASON = "Runtime diagnostics name internal state, so they are never posted in a channel."


class DevTools[BotT: "squid.bot.app.RedstoneSquid"](commands.Cog):
    """Look at what this process is holding while it is holding it."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot

    # pyrefly: ignore[bad-override]  # MaybeCoro[bool] covers a coroutine; pyrefly drops the parameter
    async def cog_check(self, ctx: Context[BotT]) -> bool:
        """One gate for the whole cog, so a new subcommand cannot forget it.

        Not a check on the group: `invoke_without_command=True` skips the group's own
        `prepare`, and with it the group's checks, whenever a subcommand is what ran.
        """
        return await ctx.bot.is_owner(ctx.author)

    @commands.group(name="dev", hidden=True, invoke_without_command=True)
    async def dev_group(self, ctx: Context[BotT]) -> None:
        """Runtime diagnostics for the bot owner."""
        await ctx.send_help("dev")

    @dev_group.group(name="ui", invoke_without_command=True)
    async def ui_group(self, ctx: Context[BotT]) -> None:
        """Inspect live squid-layouts mounts."""
        await self._open(ctx, focus=None)

    @ui_group.command(name="list")
    async def list_mounts(self, ctx: Context[BotT]) -> None:
        """Open the inspector on every live mount."""
        await self._open(ctx, focus=None)

    @ui_group.command(name="inspect")
    async def inspect_mount(self, ctx: Context[BotT], mount_id: str) -> None:
        """Open the inspector on one mount by id."""
        await self._open(ctx, focus=mount_id)

    @ui_group.command(name="scene")
    async def dump_scene(self, ctx: Context[BotT], mount_id: str) -> None:
        """Attach the resolved SceneDocument behind a mount's current generation."""
        mount = sl.discord.live.find(mount_id)
        if mount is None:
            await self._refuse(ctx, f"No live mount `{mount_id}`. Run `dev ui list` for the current ids.")
            return
        asset = scene_attachment(mount.snapshot())
        if asset is None:
            await self._refuse(ctx, f"Mount `{mount_id}` has not committed a render yet, so it has no scene.")
            return
        assert isinstance(asset.source, sl.InlineAsset)
        await deliver_privately(
            ctx,
            render_static([sl.primitives.Text(f"Scene for mount `{mount_id}` — {len(asset.source.data)} bytes.")]),
            reason=_REASON,
            files=[discord.File(io.BytesIO(asset.source.data), filename=asset.name)],
        )

    @dev_group.command(name="routes")
    async def list_routes(self, ctx: Context[BotT]) -> None:
        """List stateless routed controls registered in this process."""
        descriptions = router.describe()
        lines: list[str] = []
        for route in descriptions:
            group = route.group_prefix or "ungrouped"
            lines.append(
                f"{route.component.value:6} {route.format} [{group}] -> {route.handler_module}.{route.handler_qualname}"
            )
            if route.aliases:
                lines.append(f"       aliases: {', '.join(route.aliases)}")
            if route.middleware:
                lines.append(f"       middleware: {' -> '.join(route.middleware)}")
        body = "No routed controls are registered." if not lines else "\n".join(lines)
        await deliver_privately(
            ctx,
            render_static([sl.primitives.Heading("Routed controls"), sl.primitives.Text(body)]),
            reason=_REASON,
        )

    async def _open(self, ctx: Context[BotT], *, focus: str | None) -> None:
        inspector = MountInspector(focus=focus)
        mount = create_mount(inspector, timeout=SESSION_SECONDS, lock_to=ctx.author.id)
        # The inspector appears in its own list from its second render on; without this it is
        # the one unexplained session in the table it just drew.
        inspector.own_id = mount.id
        await mount.send(destination(ctx, visibility=Private(_REASON)))

    async def _refuse(self, ctx: Context[BotT], message: str) -> None:
        await deliver_privately(ctx, error_layout("No such mount", message), reason=_REASON)


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(DevTools(bot))
