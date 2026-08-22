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
from squid_layouts.discord.reactor import Reactor
from squid_layouts.discord.routing import routers
from squid_layouts.discord.sessions import SessionRegistry
from squid_layouts.document import InlineAsset
from squid_layouts.factories import code, paragraph, section
from squid_layouts.profiling import (
    CounterAggregate,
    NoOpProfiler,
    OperationAggregate,
    OperationKind,
    Profiler,
    RuntimeTrace,
    SpanAggregate,
    snapshot_json,
)
from squid_layouts.semantic import LayoutNode
from squid_layouts.topics import TopicBus

type DevToolsCheck[BotT: commands.Bot] = Callable[[Context[BotT]], Awaitable[bool]]

SESSION_SECONDS = 300
_PERCENTILE_SAMPLE_FLOOR = 20


async def _owner_only[BotT: commands.Bot](ctx: Context[BotT]) -> bool:
    """Use discord.py's configured owner as the safe default authorization policy."""
    return await ctx.bot.is_owner(ctx.author)


class DevTools[BotT: commands.Bot](commands.Cog):
    """Inspect the mounts and routed controls attached to a Discord client."""

    def __init__(
        self,
        check: DevToolsCheck[BotT] = _owner_only,
        registry: SessionRegistry | None = None,
        *,
        profiler: Profiler | None = None,
        reactor: Reactor | None = None,
        bus: TopicBus | None = None,
    ) -> None:
        self._check = check
        self._registry = registry
        self._reactor = reactor
        self._bus = bus if bus is not None else reactor.bus if reactor is not None else None
        self._profiler = (
            profiler
            if profiler is not None
            else reactor.profiler
            if reactor is not None
            else self._bus.profiler
            if self._bus is not None
            else NoOpProfiler()
        )

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

    @ui_group.command(name="profile")
    async def profile_mount(self, ctx: Context[BotT], mount_id: str) -> None:
        """Show retained dispatch and delivery traces for one mount."""
        snapshot = self._profiler.snapshot()
        retained = (*snapshot.recent, *snapshot.slow, *snapshot.failed, *snapshot.deadline_misses)
        traces = {
            trace.trace_id: trace
            for trace in retained
            if any(
                attribute.key == "mount_id" and attribute.value == mount_id
                for span in trace.spans
                if span.parent_span_id is None
                for attribute in span.attributes
            )
        }
        ordered = sorted(traces.values(), key=lambda trace: trace.started, reverse=True)[:12]
        body = (
            f"No retained profiles for mount {mount_id}."
            if not ordered
            else "\n\n".join(_trace_text(trace) for trace in ordered)
        )
        await self._send(ctx, [section(code(body), heading=f"Profile for mount {mount_id}")])

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

    @dev_group.group(name="profile", invoke_without_command=True)
    async def profile_group(self, ctx: Context[BotT]) -> None:
        """Inspect bounded runtime latency and queue diagnostics."""
        await ctx.send_help("dev profile")

    @profile_group.command(name="actions")
    async def profile_actions(self, ctx: Context[BotT]) -> None:
        """Show mounted and routed action latency aggregates."""
        snapshot = self._profiler.snapshot()
        actions = tuple(
            aggregate
            for aggregate in snapshot.aggregates
            if aggregate.key.operation in {OperationKind.DISPATCH, OperationKind.ROUTE_DISPATCH}
        )
        body = "No action profiles have been observed." if not actions else "\n".join(map(_aggregate_text, actions))
        counters = tuple(
            aggregate
            for aggregate in snapshot.counter_aggregates
            if aggregate.key.operation in {OperationKind.DISPATCH, OperationKind.ROUTE_DISPATCH}
        )
        if counters:
            body += "\n" + "\n".join(map(_counter_text, counters))
        await self._send(ctx, [section(code(body), heading="Action profiles")])

    @profile_group.command(name="queues")
    async def profile_queues(self, ctx: Context[BotT]) -> None:
        """Show Reactor and TopicBus pressure with delivery latency aggregates."""
        lines: list[str] = []
        if self._reactor is not None:
            reactor = self._reactor.snapshot()
            lines.append(
                "reactor  "
                f"queued={reactor.queued} in_flight={reactor.in_flight} redeliver={reactor.redeliver} "
                f"scheduled={reactor.scheduled} coalesced={reactor.coalesced} "
                f"delivered={reactor.delivered} failed={reactor.failed}"
            )
        if self._bus is not None:
            bus = self._bus.snapshot()
            lines.append(
                f"topics   known={len(bus.topics)} queued={bus.queued} in_flight={bus.in_flight} "
                f"delivered={bus.delivered} failed={bus.failed}"
            )
        snapshot = self._profiler.snapshot()
        queue_operations = {
            OperationKind.REACTOR_DELIVERY,
            OperationKind.TOPIC_DELIVERY,
            OperationKind.REFRESH,
        }
        lines.extend(
            _aggregate_text(aggregate)
            for aggregate in snapshot.aggregates
            if aggregate.key.operation in queue_operations
        )
        lines.extend(
            _span_aggregate_text(aggregate)
            for aggregate in snapshot.span_aggregates
            if aggregate.key.operation in queue_operations
            and (aggregate.key.span_name == "queue_wait" or aggregate.key.span_name.startswith("subscriber:"))
        )
        lines.extend(
            _counter_text(aggregate)
            for aggregate in snapshot.counter_aggregates
            if aggregate.key.operation in queue_operations
        )
        body = "No queue diagnostics are configured or observed." if not lines else "\n".join(lines)
        await self._send(ctx, [section(code(body), heading="Queue profiles")])

    @profile_group.command(name="export")
    async def profile_export(self, ctx: Context[BotT]) -> None:
        """Attach the current immutable runtime snapshot as JSON."""
        snapshot = self._profiler.snapshot()
        encoded = snapshot_json(snapshot, indent=2).encode()
        await self._send(
            ctx,
            [paragraph(f"Runtime profile `{snapshot.process_id}` — {len(encoded)} bytes.")],
            files=[discord.File(io.BytesIO(encoded), filename=f"runtime-profile-{snapshot.process_id}.json")],
        )

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
            view=render_static(nodes).layout,
            files=[] if files is None else files,
            ephemeral=ctx.interaction is not None,
            allowed_mentions=delivery.no_mentions(),
        )


__all__ = ["DevTools", "DevToolsCheck"]


def _aggregate_text(aggregate: OperationAggregate) -> str:
    key = aggregate.key
    histogram = aggregate.window if aggregate.window.observations else aggregate.lifetime
    count = histogram.observations
    average = 0.0 if count == 0 else histogram.total / count
    identity = key.disposition or key.outcome or "all"
    line = f"{key.operation or 'overflow'} {key.name} {identity} n={count} avg={_milliseconds(average)}"
    if count >= _PERCENTILE_SAMPLE_FLOOR:
        line += f" p50={_milliseconds(histogram.percentile(0.5))} p95={_milliseconds(histogram.percentile(0.95))}"
    return line


def _milliseconds(seconds: float | None) -> str:
    return "—" if seconds is None else f"{seconds * 1000:.1f}ms"


def _counter_text(aggregate: CounterAggregate) -> str:
    key = aggregate.key
    return f"counter  {key.operation}:{key.counter_name} window={aggregate.window} lifetime={aggregate.lifetime}"


def _span_aggregate_text(aggregate: SpanAggregate) -> str:
    key = aggregate.key
    histogram = aggregate.window if aggregate.window.observations else aggregate.lifetime
    count = histogram.observations
    average = 0.0 if count == 0 else histogram.total / count
    line = f"span     {key.operation}:{key.span_name} {key.outcome or 'all'} n={count} avg={_milliseconds(average)}"
    if count >= _PERCENTILE_SAMPLE_FLOOR:
        line += f" p50={_milliseconds(histogram.percentile(0.5))} p95={_milliseconds(histogram.percentile(0.95))}"
    return line


def _trace_text(trace: RuntimeTrace) -> str:
    disposition = "" if trace.result.dispatch is None else f" {trace.result.dispatch.disposition}"
    flags = " deadline-missed" if trace.deadline_missed else ""
    lines = [
        f"{trace.operation} {trace.name} {trace.result.outcome}{disposition} {_milliseconds(trace.duration)}{flags}"
    ]
    lines.extend(
        f"  {span.name} {span.outcome} {_milliseconds(span.duration)}"
        for span in trace.spans
        if span.parent_span_id is not None
    )
    return "\n".join(lines)
