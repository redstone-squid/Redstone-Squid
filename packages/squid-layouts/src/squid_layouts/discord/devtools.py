"""Hidden prefix-command diagnostics for live Squid Layouts sessions."""

import io
from collections.abc import Awaitable, Callable, Sequence

import discord
from discord.ext import commands
from discord.ext.commands import Context

from squid_layouts.discord import delivery
from squid_layouts.discord.devtools_view import MountInspector, metrics_text, plan_text, scene_attachment
from squid_layouts.discord.live import mounts
from squid_layouts.discord.mount import MountSnapshot, owned_mount
from squid_layouts.discord.routing import routers
from squid_layouts.discord.sessions import MountRegistry
from squid_layouts.document import InlineAsset
from squid_layouts.factories import code, paragraph, section
from squid_layouts.semantic import LayoutNode

type DevToolsCheck[BotT: commands.Bot] = Callable[[Context[BotT]], Awaitable[bool]]

SESSION_SECONDS = 300


async def _owner_only[BotT: commands.Bot](ctx: Context[BotT]) -> bool:
    """Use discord.py's configured owner as the safe default authorization policy."""
    return await ctx.bot.is_owner(ctx.author)


class DevTools[BotT: commands.Bot](commands.Cog):
    """Inspect the mounts and routed controls attached to a Discord client."""

    def __init__(
        self,
        check: DevToolsCheck[BotT] = _owner_only,
        registry: MountRegistry | None = None,
    ) -> None:
        self._check = check
        self._registry = registry

    # pyrefly: ignore[bad-override]  # MaybeCoro[bool] covers a coroutine; pyrefly drops the parameter
    async def cog_check(self, ctx: Context[BotT]) -> bool:
        """Authorize every command through the single injected gate."""
        return await self._check(ctx)

    @commands.group(name="dev", hidden=True, invoke_without_command=True)
    async def dev_group(self, ctx: Context[BotT]) -> None:
        """Runtime diagnostics for Squid Layouts."""
        await ctx.send_help("dev")

    @dev_group.group(name="ui", invoke_without_command=True)
    async def ui_group(self, ctx: Context[BotT]) -> None:
        """Inspect live mounts."""
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
        """Attach the committed scene document for a mount."""
        snapshot = await self._snapshot_or_refuse(ctx, mount_id)
        if snapshot is None:
            return
        asset = scene_attachment(snapshot)
        if asset is None:
            await self._refuse(ctx, f"Mount `{mount_id}` has not committed a render yet, so it has no scene.")
            return
        assert isinstance(asset.source, InlineAsset)
        await self._send(
            ctx,
            [paragraph(f"Scene for mount `{mount_id}` — {len(asset.source.data)} bytes.")],
            files=[discord.File(io.BytesIO(asset.source.data), filename=asset.name)],
        )

    @ui_group.command(name="plan")
    async def dump_plan(self, ctx: Context[BotT], mount_id: str) -> None:
        """Show the retained plan report, grouped by severity."""
        snapshot = await self._snapshot_or_refuse(ctx, mount_id)
        if snapshot is not None:
            await self._send(ctx, [section(code(plan_text(snapshot)), heading=f"Plan for mount {mount_id}")])

    @ui_group.command(name="metrics")
    async def dump_metrics(self, ctx: Context[BotT], mount_id: str) -> None:
        """Show planner search and cache metrics for a mount."""
        snapshot = await self._snapshot_or_refuse(ctx, mount_id)
        if snapshot is not None:
            await self._send(ctx, [section(code(metrics_text(snapshot)), heading=f"Metrics for mount {mount_id}")])

    @dev_group.command(name="routes")
    async def list_routes(self, ctx: Context[BotT]) -> None:
        """List routed controls installed on this command's client."""
        lines: list[str] = []
        installed = routers(ctx.bot)
        for index, router in enumerate(installed, start=1):
            lines.append(f"router {index}: {type(router).__module__}.{type(router).__qualname__}")
            for route in router.describe():
                group = route.group_prefix or "ungrouped"
                lines.append(
                    f"  {route.component.value:6} {route.format} [{group}] -> "
                    f"{route.handler_module}.{route.handler_qualname}"
                )
                if route.aliases:
                    lines.append(f"         aliases: {', '.join(route.aliases)}")
                if route.middleware:
                    lines.append(f"         middleware: {' -> '.join(route.middleware)}")
        body = "No routers are installed on this client." if not lines else "\n".join(lines)
        await self._send(ctx, [section(code(body), heading="Routed controls")])

    async def _open(self, ctx: Context[BotT], *, focus: str | None) -> None:
        inspector = MountInspector(focus=focus, registry=self._registry)
        mount = owned_mount(inspector, ctx.author.id, timeout=SESSION_SECONDS)
        inspector.own_id = mount.id
        await mount.send(delivery.reply_to(ctx, ephemeral=ctx.interaction is not None))

    async def _snapshot_or_refuse(self, ctx: Context[BotT], mount_id: str) -> MountSnapshot | None:
        mount = next((candidate for candidate in mounts() if candidate.id == mount_id), None)
        if mount is None:
            await self._refuse(ctx, f"No live mount `{mount_id}`. Run `dev ui list` for the current ids.")
            return None
        return mount.snapshot()

    async def _refuse(self, ctx: Context[BotT], message: str) -> None:
        await self._send(ctx, [section(paragraph(message), heading="No such mount")])

    async def _send(
        self,
        ctx: Context[BotT],
        nodes: Sequence[LayoutNode],
        *,
        files: list[discord.File] | None = None,
    ) -> None:
        from squid_layouts.discord.compose import render_static

        await ctx.send(
            view=render_static(nodes),
            files=[] if files is None else files,
            ephemeral=ctx.interaction is not None,
            allowed_mentions=delivery.no_mentions(),
        )


__all__ = ["DevTools", "DevToolsCheck"]
