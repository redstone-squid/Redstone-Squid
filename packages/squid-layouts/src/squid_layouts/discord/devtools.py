"""Owner-only operational diagnostics for live Squid Layouts runtimes."""

import dataclasses
import io
import json
from collections.abc import Awaitable, Callable, Sequence

import discord
from discord.ext import commands
from discord.ext.commands import Context

import squid_layouts as sl
from squid_layouts.discord import delivery
from squid_layouts.discord.devtools_view import OperationalInspector, metrics_text, plan_text, scene_attachment
from squid_layouts.discord.live import find
from squid_layouts.discord.mount import MountSnapshot, owned_mount
from squid_layouts.discord.operations import (
    ActionDisabled,
    ConfirmationRequired,
    DevToolsRuntime,
    RuntimeUnavailable,
    TargetNotFound,
)
from squid_layouts.discord.reactor import Reactor
from squid_layouts.discord.routing import routers
from squid_layouts.discord.sessions import SessionRegistry
from squid_layouts.document import InlineAsset
from squid_layouts.factories import code, paragraph, section
from squid_layouts.profiling import OperationKind, Profiler, RuntimeTrace
from squid_layouts.semantic import LayoutNode
from squid_layouts.topics import TopicBus

type DevToolsCheck[BotT: commands.Bot] = Callable[[Context[BotT]], Awaitable[bool]]

SESSION_SECONDS = 300


async def _owner_only[BotT: commands.Bot](ctx: Context[BotT]) -> bool:
    """Use discord.py's configured owner as the safe default authorization policy."""
    return await ctx.bot.is_owner(ctx.author)


class DevTools[BotT: commands.Bot](commands.Cog):
    """The unified ``!dev ui`` operational control plane."""

    def __init__(
        self,
        check: DevToolsCheck[BotT] = _owner_only,
        registry: SessionRegistry | None = None,
        *,
        profiler: Profiler | None = None,
        reactor: Reactor | None = None,
        bus: TopicBus | None = None,
        runtime: DevToolsRuntime | None = None,
    ) -> None:
        self._check = check
        self._runtime = runtime or DevToolsRuntime(
            sessions=registry,
            reactor=reactor,
            bus=bus,
            profiler=profiler,
        )
        self._registry = self._runtime.sessions
        self._reactor = self._runtime.reactor
        self._bus = self._runtime.bus
        self._profiler = self._runtime.profiler

    # pyrefly: ignore[bad-override]  # MaybeCoro[bool] covers a coroutine; pyrefly drops the parameter
    async def cog_check(self, ctx: Context[BotT]) -> bool:
        """Authorize every command through the single injected gate."""
        return await self._check(ctx)

    @commands.group(name="dev", hidden=True, invoke_without_command=True)
    async def dev_group(self, ctx: Context[BotT]) -> None:
        """Development-only operational diagnostics."""
        await ctx.send_help("dev")

    @dev_group.group(name="ui", invoke_without_command=True)
    async def ui_group(self, ctx: Context[BotT]) -> None:
        """Open the unified operational dashboard."""
        await self._open(ctx)

    @ui_group.command(name="mounts")
    async def list_mounts(self, ctx: Context[BotT]) -> None:
        """Open the dashboard focused on live mounts."""
        await self._open(ctx, focus="mounts")

    @ui_group.command(name="mount")
    async def inspect_mount(self, ctx: Context[BotT], mount_id: str) -> None:
        """Open the dashboard focused on one live mount."""
        await self._open(ctx, focus="mounts", mount_id=mount_id)

    @ui_group.command(name="sessions")
    async def list_sessions(self, ctx: Context[BotT]) -> None:
        """Open the dashboard focused on logical sessions."""
        await self._open(ctx, focus="sessions")

    @ui_group.command(name="session")
    async def inspect_session(self, ctx: Context[BotT], session_id: str) -> None:
        """Open the dashboard focused on one session."""
        await self._open(ctx, focus="sessions", session_id=session_id)

    @ui_group.command(name="routes")
    async def list_routes(self, ctx: Context[BotT]) -> None:
        """Show routed controls and effective middleware."""
        lines: list[str] = []
        for index, router in enumerate(routers(ctx.bot), start=1):
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
        await self._send(ctx, [section(sl.heading("Routed controls"), code(body))])

    @ui_group.command(name="queues")
    async def inspect_queues(self, ctx: Context[BotT]) -> None:
        """Show queue pressure and subscriber registrations."""
        snapshot = self._runtime.snapshot()
        lines = [
            f"reactor  {_reactor_text(snapshot.reactor)}",
            f"topics   {_topics_text(snapshot.topics)}",
        ]
        if snapshot.topics is not None:
            lines.extend(
                f"  {topic.topic} subscribers={topic.subscribers} labels={','.join(topic.labels) or '-'} "
                f"queued={topic.queued} in_flight={topic.in_flight} delivered={topic.delivered} failed={topic.failed}"
                for topic in snapshot.topics.topics
            )
        await self._send(ctx, [section(sl.heading("Queues and subscribers"), code("\n".join(lines)))])

    @ui_group.command(name="history")
    async def inspect_history(self, ctx: Context[BotT], mount_id: str) -> None:
        """Show action-history stacks for a mount without invoking inverses."""
        try:
            inspection = self._runtime.inspect_mount(mount_id)
        except (TargetNotFound, RuntimeError) as error:
            await self._refuse(ctx, str(error))
            return
        body = (
            "\n\n".join(_history_text(history) for history in inspection.histories) or "No history stacks are declared."
        )
        await self._send(ctx, [section(sl.heading(f"History for mount {mount_id}"), code(body))])

    @ui_group.command(name="profile")
    async def inspect_profile(self, ctx: Context[BotT], mount_id: str | None = None) -> None:
        """Show profiler health, or retained traces for one mount."""
        if mount_id is None:
            snapshot = self._runtime.snapshot().profiler
            health = snapshot.health
            body = "\n".join(
                (
                    f"process={snapshot.process_id}",
                    f"active={health.active}",
                    f"recent={health.retained_recent}",
                    f"slow={health.retained_slow}",
                    f"failed={health.retained_failed}",
                    f"deadline_misses={health.retained_deadline_misses}",
                )
            )
            aggregates = [
                aggregate
                for aggregate in snapshot.aggregates
                if aggregate.key.operation in {OperationKind.DISPATCH, OperationKind.ROUTE_DISPATCH}
            ]
            if aggregates:
                body += "\n" + "\n".join(
                    f"{aggregate.key.operation} {aggregate.key.name} n={_histogram_count(aggregate)}"
                    for aggregate in aggregates
                )
            await self._send(ctx, [section(sl.heading("Profiler"), code(body))])
            return
        await self._profile_mount(ctx, mount_id)

    @ui_group.command(name="persistence")
    async def inspect_persistence(self, ctx: Context[BotT]) -> None:
        """Show durable-runtime health and persisted record metadata."""
        snapshot = self._runtime.snapshot()
        durable = snapshot.durable
        if durable is None:
            await self._send(
                ctx, [section(sl.heading("Persistence"), paragraph("No durable session runtime is configured."))]
            )
            return
        try:
            records = await self._runtime.records()
        except RuntimeUnavailable as error:
            await self._refuse(ctx, str(error))
            return
        lines = [
            f"running={durable.running} active={len(durable.active)} dirty={len(durable.dirty)}",
            f"recovery={_recovery_text(durable.last_recovery)}",
            "",
        ]
        lines.extend(
            f"{record.key} scope={record.scope} summary={record.summary_bytes}B snapshot={record.snapshot_bytes}B"
            for record in records
        )
        await self._send(ctx, [section(sl.heading("Persistence"), code("\n".join(lines)))])

    @ui_group.command(name="export")
    async def export_ui(self, ctx: Context[BotT]) -> None:
        """Attach the complete bounded operational snapshot as JSON."""
        snapshot = self._runtime.snapshot()
        encoded = json.dumps(dataclasses.asdict(snapshot), default=_json_default, indent=2, sort_keys=True).encode()
        await self._send(
            ctx,
            [paragraph(f"Operational snapshot ??{len(encoded)} bytes.")],
            files=[discord.File(io.BytesIO(encoded), filename="squid-operational-snapshot.json")],
        )

    @ui_group.command(name="refresh")
    async def refresh_mount(self, ctx: Context[BotT], mount_id: str) -> None:
        """Force an immediate refresh for one mount."""
        await self._run_operation(ctx, self._runtime.refresh_mount(mount_id))

    @ui_group.command(name="close")
    async def close_session(self, ctx: Context[BotT], session_id: str, confirmation: str | None = None) -> None:
        """Close a session after repeating its id as confirmation."""
        await self._run_operation(ctx, self._runtime.close_session(session_id, confirmed=confirmation == session_id))

    @ui_group.command(name="idle")
    async def wait_idle(self, ctx: Context[BotT]) -> None:
        """Wait for the configured refresh and topic queues to settle."""
        await self._run_operation(ctx, self._runtime.wait_idle())

    @ui_group.command(name="flush")
    async def flush_persistence(self, ctx: Context[BotT]) -> None:
        """Flush pending durable checkpoints."""
        await self._run_operation(ctx, self._runtime.flush_persistence())

    @ui_group.command(name="recover")
    async def recover_persistence(self, ctx: Context[BotT], confirmation: str | None = None) -> None:
        """Run durable recovery after the caller supplies ``recover`` as confirmation."""
        await self._run_operation(ctx, self._runtime.recover_persistence(confirmed=confirmation == "recover"))

    @ui_group.command(name="clear-profile")
    async def clear_profile(self, ctx: Context[BotT]) -> None:
        """Clear bounded profiler diagnostics."""
        try:
            result = self._runtime.clear_profile()
        except ActionDisabled as error:
            await self._refuse(ctx, str(error))
            return
        await self._send(ctx, [section(sl.heading("Devtools operation"), paragraph(result.detail))])

    @ui_group.command(name="purge")
    async def purge_persistence(self, ctx: Context[BotT], record_keys: str, confirmation: str | None = None) -> None:
        """Purge comma-separated durable keys after supplying ``purge`` as confirmation."""
        keys = tuple(key.strip() for key in record_keys.split(",") if key.strip())
        try:
            results = await self._runtime.purge_persistence(keys, confirmed=confirmation == "purge")
        except Exception as error:
            await self._refuse(ctx, str(error))
            return
        body = "\n".join(f"{result.record_key}: {'deleted' if result.deleted else result.reason}" for result in results)
        await self._send(ctx, [section(sl.heading("Persistence purge"), code(body or "No record keys supplied."))])

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
            [paragraph(f"Scene for mount `{mount_id}` ??{len(asset.source.data)} bytes.")],
            files=[discord.File(io.BytesIO(asset.source.data), filename=asset.name)],
        )

    @ui_group.command(name="plan")
    async def dump_plan(self, ctx: Context[BotT], mount_id: str) -> None:
        """Show the retained plan report for a mount."""
        snapshot = await self._snapshot_or_refuse(ctx, mount_id)
        if snapshot is not None:
            await self._send(ctx, [section(sl.heading(f"Plan for mount {mount_id}"), code(plan_text(snapshot)))])

    @ui_group.command(name="metrics")
    async def dump_metrics(self, ctx: Context[BotT], mount_id: str) -> None:
        """Show planner search and cache metrics for a mount."""
        snapshot = await self._snapshot_or_refuse(ctx, mount_id)
        if snapshot is not None:
            await self._send(ctx, [section(sl.heading(f"Metrics for mount {mount_id}"), code(metrics_text(snapshot)))])

    async def _profile_mount(self, ctx: Context[BotT], mount_id: str) -> None:
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
        await self._send(ctx, [section(sl.heading(f"Profile for mount {mount_id}"), code(body))])

    async def _open(
        self,
        ctx: Context[BotT],
        *,
        focus: str | None = None,
        mount_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        inspector = OperationalInspector(self._runtime)
        if focus is not None:
            inspector.section = focus
        if mount_id is not None:
            inspector.mount_id = mount_id
        if session_id is not None:
            inspector.session_id = session_id
        mount = owned_mount(inspector, ctx.author.id, timeout=SESSION_SECONDS)
        await mount.send(delivery.reply_to(ctx, ephemeral=ctx.interaction is not None))

    async def _snapshot_or_refuse(self, ctx: Context[BotT], mount_id: str) -> MountSnapshot | None:
        mount = find(mount_id)
        if mount is None:
            await self._refuse(ctx, f"No live mount `{mount_id}`. Run `dev ui mounts` for the current ids.")
            return None
        return mount.snapshot()

    async def _run_operation(self, ctx: Context[BotT], operation: Awaitable[object]) -> None:
        try:
            result = await operation
        except (ActionDisabled, ConfirmationRequired, RuntimeUnavailable, TargetNotFound) as error:
            await self._refuse(ctx, str(error))
            return
        await self._send(
            ctx, [section(sl.heading("Devtools operation"), paragraph(str(getattr(result, "detail", result))))]
        )

    async def _refuse(self, ctx: Context[BotT], message: str) -> None:
        await self._send(ctx, [section(sl.heading("Devtools operation refused"), paragraph(message))])

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


def _json_default(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return repr(value)


def _history_text(history: object) -> str:
    name = getattr(history, "name", "history")
    undo = getattr(history, "undo", ())
    redo = getattr(history, "redo", ())
    entries = [f"{entry.label} ({'undo' if entry.has_undo else 'state-only'})" for entry in (*undo, *redo)]
    return f"{name}: undo={len(undo)} redo={len(redo)}\n" + "\n".join(entries or ["(empty)"])


def _reactor_text(snapshot: object) -> str:
    if snapshot is None:
        return "unconfigured"
    return f"queued={snapshot.queued} in_flight={snapshot.in_flight} failed={snapshot.failed}"


def _topics_text(snapshot: object) -> str:
    if snapshot is None:
        return "unconfigured"
    return f"known={len(snapshot.topics)} queued={snapshot.queued} failed={snapshot.failed}"


def _recovery_text(report: object) -> str:
    if report is None:
        return "never"
    return f"restored={len(report.restored)} failed={len(report.failed)}"


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


def _milliseconds(seconds: float | None) -> str:
    return "??" if seconds is None else f"{seconds * 1000:.1f}ms"


def _histogram_count(aggregate: object) -> int:
    histogram = aggregate.window if aggregate.window.observations else aggregate.lifetime
    return histogram.observations


__all__ = ["DevTools", "DevToolsCheck"]
