"""Format diagnostic snapshots and downloadable reports for developer dashboards."""

import dataclasses
import json
import pprint
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

import squid_ui as sl
from squid_reactivity.diagnostics import (
    CausalEventSnapshot,
    ContinuationFailureSnapshot,
    OperationEventSnapshot,
    ResourceEventSnapshot,
    TransactionResultSnapshot,
    TransactionValidationSnapshot,
)
from squid_reactivity.topics import TopicBusStats
from squid_ui.ext.discord.access import AccessPolicy, Check, Everyone, Owner, Users
from squid_ui.ext.discord.devtools.runtime import OperationalSnapshot, SessionInspection
from squid_ui.ext.discord.messages.contracts import MessageRootSnapshot, MessageRootStatus
from squid_ui.ext.discord.messages.root import MessageRoot
from squid_ui.ext.discord.messages.scheduler import MessageRootSchedulerSnapshot
from squid_ui.profiling import RuntimeSnapshot, RuntimeTrace

if TYPE_CHECKING:
    # Annotations only; keep the optional `durable` extra out of runtime imports.
    from squid_ui.ext.discord.durability import DurableRuntimeSnapshot, RecoveryReport


def scheduler_summary(snapshot: MessageRootSchedulerSnapshot | None) -> str:
    if snapshot is None:
        return "unconfigured"
    return f"queued={snapshot.queued} in_flight={snapshot.in_flight} failed={snapshot.failed}"


def topic_summary(snapshot: TopicBusStats | None) -> str:
    if snapshot is None:
        return "unconfigured"
    return f"known={len(snapshot.topics)} queued={snapshot.queued} failed={snapshot.failed}"


def recovery_summary(report: RecoveryReport | None) -> str:
    if report is None:
        return "never"
    return f"restored={len(report.restored)} failed={len(report.failed)}"


def scene_attachment(snapshot: MessageRootSnapshot) -> sl.document.Asset | None:
    """The message root's committed scene as the protocol JSON, for reading outside Discord."""
    if snapshot.scene is None:
        return None
    return sl.document.Asset(
        key="scene",
        name=f"scene-{snapshot.id}-gen{snapshot.generation}.json",
        media_type="application/json",
        source=sl.document.InlineAsset(sl.scene.Codec.dumps(snapshot.scene).encode()),
    )


def operational_attachment(
    snapshot: OperationalSnapshot,
) -> sl.document.Asset:
    """The operational snapshot as an inline JSON download."""
    encoded = json.dumps(snapshot, default=_json_default, indent=2, sort_keys=True).encode()
    return sl.document.Asset(
        key="operational-snapshot",
        name="squid-operational-snapshot.json",
        media_type="application/json",
        source=sl.document.InlineAsset(encoded),
    )


def _json_default(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return repr(value)


def trace_summary(trace: RuntimeTrace) -> str:
    duration_ms = trace.duration * 1000
    return f"{trace.operation.value}:{trace.name} {trace.result.status.value} {duration_ms:.1f}ms id={trace.trace_id}"


def _action_text(status: TransactionResultSnapshot) -> str:
    cause = "root" if status.cause is None else f"{status.cause.kind}:{status.cause.identity}"
    detail = status.terminal if status.reason is None else f"{status.terminal}:{status.reason}"
    relation = "" if status.relation is None else f" {status.relation.kind}={status.relation.transaction_id}"
    conflict = "" if status.conflict is None else f" conflict={status.conflict.target_id}"
    return (
        f"{status.transaction_id} {status.kind} {status.name} {detail} cause={cause} "
        f"cells={status.changes.cells} participants={status.changes.participants}{relation}{conflict}"
    )


def event_summary(event: CausalEventSnapshot) -> str:
    match event:
        case TransactionResultSnapshot():
            return _action_text(event)
        case TransactionValidationSnapshot():
            return f"transaction:{event.transaction_id} validation: {len(event.preconditions)} version conditions"
        case OperationEventSnapshot():
            cause = "root" if event.cause is None else f"{event.cause.kind}:{event.cause.identity}"
            return f"operation:{event.execution_id} {event.name} {event.status} cause={cause}"
        case ResourceEventSnapshot():
            cause = "root" if event.cause is None else f"{event.cause.kind}:{event.cause.identity}"
            return f"resource:{event.generation_id} {event.name} {event.status} cause={cause}"
        case ContinuationFailureSnapshot():
            return (
                f"continuation:{event.failure_id} failed {event.stage} {event.callback} "
                f"cause={event.cause.kind}:{event.cause.identity}"
            )


def plan_text(snapshot: MessageRootSnapshot) -> str:
    """Render the retained plan report, grouping adaptations by severity."""
    report = snapshot.report
    if report is None:
        return "Nothing has been committed yet, so this message root has no plan report."
    lines = [
        f"scene    {report.scene_fingerprint or '—'}",
    ]
    if not report.events:
        return "\n".join((*lines, "", "no adaptations — the layout fit as authored"))
    severities = dict.fromkeys(event.severity for event in report.events)
    for severity in severities:
        lines.extend(("", severity.value.upper()))
        lines.extend(
            f"{event.code} at {event.path}\n    {event.message}"
            for event in report.events
            if event.severity is severity
        )
    return "\n".join(lines)


def metrics_text(snapshot: MessageRootSnapshot) -> str:
    """Render the planner work and cache disposition retained by a message root."""
    metrics = snapshot.metrics
    if metrics is None:
        return "Nothing has been committed yet, so this message root has no planner metrics."
    return "\n".join(
        (
            f"work_used: {metrics.work_used}",
            f"target_evaluations: {metrics.target_evaluations}",
            f"generated_states: {metrics.generated_states}",
            f"reuse: {metrics.reuse.value}",
            f"cache: {'hit' if metrics.cache_hit else 'miss'}",
        )
    )


def root_summary(snapshot: MessageRootSnapshot) -> list[sl.TextLike]:
    entries: list[sl.TextLike] = [
        sl.md(t"**Component**\n`{snapshot.component}`"),
        sl.md(t"**Generation**\n{snapshot.generation} · {flags(snapshot)} · {snapshot.suppressed} suppressed"),
        sl.md(
            t"**Timing**\nage {duration_text(snapshot.age)} · idle {duration_text(snapshot.idle)} · {expiry(snapshot)}"
        ),
        sl.md(t"**Access**\n{_access_text(snapshot.access)}"),
    ]
    address = snapshot.address
    if address is None:
        entries.append(sl.md("**Message**\nnot located yet — nobody has clicked it"))
    else:
        where = "ephemeral" if address.ephemeral else sl.mention(address.channel_id, kind="channel")
        link = sl.text_link(str(address.message_id), address.jump_url)
        entries.append(sl.md(t"**Message**\n{link} in {where}"))
    return entries


def _access_text(access: AccessPolicy) -> sl.TextLike:
    """Describe the message root's access policy without exposing callback internals."""
    if isinstance(access, Everyone):
        return "Everyone"
    if isinstance(access, Owner):
        return sl.plain(t"Owner ({sl.mention(access.user_id)})")
    if isinstance(access, Users):
        users = sl.text.Text.join(", ", (sl.mention(user_id) for user_id in sorted(access.user_ids)))
        return sl.plain(t"Users ({users})")
    if isinstance(access, Check):
        return "Check"
    return type(access).__name__


def flags(snapshot: MessageRootSnapshot) -> str:
    flags = []
    if snapshot.lifecycle is MessageRootStatus.RENEWAL_ARMED:
        flags.append("renewal armed")
    if snapshot.pending:
        flags.append("dirty")
    if snapshot.finished:
        flags.append("finished")
    if snapshot.address is not None and snapshot.address.ephemeral:
        flags.append("ephemeral")
    return " ".join(flags) if flags else "clean"


def expiry(snapshot: MessageRootSnapshot) -> str:
    timeout = "no timeout" if snapshot.expires_in is None else f"timeout in {duration_text(snapshot.expires_in)}"
    if snapshot.handle_expires_in is None:
        return timeout
    return f"{timeout} · edit handle {duration_text(snapshot.handle_expires_in)} left"


def duration_text(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{total % 3600 // 60:02d}m"


def profile_summary(snapshot: RuntimeSnapshot) -> str:
    health = snapshot.health
    return f"active={health.active} failed={health.retained_failed}"


def durable_summary(snapshot: DurableRuntimeSnapshot | None) -> str:
    if snapshot is None:
        return "unconfigured"
    return f"running={snapshot.running} active={len(snapshot.active)} dirty={len(snapshot.dirty)}"


def preparation(message_root: MessageRoot) -> list[str]:
    """Required loading durations, separate from layout timing and work units."""
    loading = message_root.runtime.preparation
    lines = [f"latest batch: {loading.latest.elapsed * 1000:.1f}ms (concurrent)"]
    for report in loading.reports.values():
        reused = " reused" if report.reused else ""
        lines.append(
            f"{type(report.component).__name__} at {', '.join(report.paths)}: "
            f"{report.elapsed * 1000:.1f}ms {report.outcome}{reused}"
        )
    return lines


def component_state(message_root: MessageRoot) -> dict[str, object]:
    """Declared field values for every component in the committed tree, by path.

    Opaque fields are left out: they hold collaborators, whose representations say little.
    Each component is read on its own, so one field that cannot be read does not cost the
    whole dump.
    """
    states: dict[str, object] = {}
    for path, component in message_root.runtime.components.items():
        try:
            states[path] = {
                name: getattr(component, name)
                for name, cell in sl.runtime.inspect_cells(component).items()
                if not cell.opaque
            }
        except Exception as error:
            states[path] = f"<unreadable: {type(error).__name__}: {error}>"
    return states


def reactivity(message_root: MessageRoot) -> list[str]:
    """Every cell's version and every computed's current source set, resolved to names.

    Names are resolved across the whole tree rather than per component, because a computed
    may read another component's state and printing an identity would say nothing.
    """
    components = message_root.runtime.components
    # A model can be read through several components; inspect it once, outside the tree.
    component_ids = {id(component) for component in components.values()}
    namespaces = {
        id(address.owner): address.owner
        for address in message_root.observed
        if isinstance(address, sl.runtime.CellAddress)
        and isinstance(address.owner, sl.Reactive)
        and id(address.owner) not in component_ids
    }
    labels: dict[int, str] = {}
    for path, component in components.items():
        for name, cell in sl.runtime.inspect_cells(component).items():
            labels[cell.identity] = f"{path}.{name}"
        for name, node in sl.runtime.inspect_computed(component).items():
            labels[node.identity] = f"{path}.{name}"
    for namespace in namespaces.values():
        for name, cell in sl.runtime.inspect_cells(namespace).items():
            labels[cell.identity] = f"{namespace!r}.{name}"

    lines: list[str] = []
    for path, component in components.items():
        lines.append(path)
        for name, cell in sl.runtime.inspect_cells(component).items():
            marks = " ".join(mark for mark, on in (("opaque", cell.opaque), ("default", not cell.assigned)) if on)
            lines.append(f"  {name} v{cell.version}{f'  [{marks}]' if marks else ''}")
        for name, node in sl.runtime.inspect_computed(component).items():
            if not node.evaluated:
                lines.append(f"  {name} (never evaluated)")
                continue
            read = ", ".join(labels.get(source, "<external>") for source in node.sources) or "nothing"
            lines.append(f"  {name} v{node.version} <- {read}")
    for namespace in namespaces.values():
        lines.append(f"{namespace!r} (model)")
        for name, cell in sl.runtime.inspect_cells(namespace).items():
            marks = " ".join(mark for mark, on in (("opaque", cell.opaque), ("default", not cell.assigned)) if on)
            lines.append(f"  {name} v{cell.version}{f'  [{marks}]' if marks else ''}")
    return lines


def dump_lines(lines: list[str]) -> str:
    return "\n".join(lines) or "(no components)"


def presentation_state(session: sl.runtime.PresentationState) -> dict[str, object]:
    return {
        "cursors": dict(session.cursors),
        "node_values": dict(session.node_values),
        "declared": session.declared.inspect(),
    }


def members(session: SessionInspection) -> str:
    """Membership as `used/limit`, or a bare count when the session is unbounded."""
    count = len(session.members)
    return f"{count}" if session.capacity is None else f"{count}/{session.capacity}"


def dump(value: object) -> str:
    """Format diagnostic values as readable Python representations."""
    return pprint.pformat(value, width=88, sort_dicts=True) or "{}"
