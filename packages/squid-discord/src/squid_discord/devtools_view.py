"""The `/dev ui` inspector: every live mount, and why one of them is odd.

squid-layouts has excellent *planning* diagnostics — reports, fingerprints, plan metrics —
and every one of them describes a render that already happened. Nothing answered "show me
the UI sessions this process is holding right now". `squid_discord.mounts()` is that list, and
this component is the reading of it.

Deliberately untranslated, unlike the rest of `squid.bot`: it is owner-only, and most of what
it prints is Python identifiers, state field names and planner event codes, which no
catalogue would improve.
"""

import pprint
from collections.abc import Hashable, Iterable, Sequence
from typing import TYPE_CHECKING, Any

# The package this module belongs to, imported by name so the devtools panel reads the way
# a host would write it. Safe despite `__init__` importing this module: every use below is
# either a deferred annotation or inside a function body, so nothing resolves at import.
import squid_discord
import squid_layouts as sl
from squid_discord.operations import DevToolsRuntime
from squid_layouts.profiling import RuntimeSnapshot
from squid_layouts.runtime.topics import BusSnapshot

if TYPE_CHECKING:
    # Annotations only; see the note in operations.py about the `durable` extra.
    from squid_discord.durability import DurableRuntimeSnapshot, RecoveryReport


SESSION_SECONDS = 300
"""Short-lived on purpose: an inspector left open is one more mount in its own list."""

_SELECT_LIMIT = 25
"""Discord's option cap. The list itself pages; the picker offers the newest of them."""


class MountInspector(sl.Component):
    """The live mounts, and one of them opened.

    Reads `squid_discord.mounts()` on every render rather than holding a list, so a panel left
    open keeps telling the truth: sessions that finished while it was open are simply gone
    from the next render.
    """

    focus: str | None = sl.state(None)
    revision: int = sl.state(0)
    """Bumped by Refresh. A handler that changes nothing leaves the mount clean and the
    message stale, so re-reading the world has to be a state change like any other."""

    own_id: str | None = sl.state(None)
    """This panel's own mount id, set by the cog once the mount exists — it is in the list
    like everything else, and unlabelled it reads as a mystery session."""

    def __init__(self, *, focus: str | None = None, registry: squid_discord.SessionRegistry | None = None) -> None:
        self.focus = focus
        self._registry = registry

    def render(self) -> Sequence[sl.LayoutNode]:
        mounts = squid_discord.mounts()
        if self.focus is not None:
            target = squid_discord.live.find(self.focus)
            if target is not None:
                return self._detail(target.snapshot(), target)
        return self._list(mounts)

    # --- List ---------------------------------------------------------------------------

    def _list(self, mounts: Sequence[squid_discord.Mount]) -> Sequence[sl.LayoutNode]:
        # Newest first: the session someone is asking about is almost always the one they
        # just opened.
        snapshots = sorted((mount.snapshot() for mount in mounts), key=lambda snapshot: snapshot.age)
        missing = self.focus is not None
        body: sl.LayoutNode
        if snapshots:
            body = sl.bullets(*(self._row(snapshot) for snapshot in snapshots), key="mounts", page_size=8)
        else:
            body = sl.paragraph("Nothing is mounted. Open a panel and run this again.")

        nodes: list[sl.LayoutNode] = [sl.section(sl.heading(f"Live mounts — {len(snapshots)}"), body)]
        if missing:
            nodes.insert(0, sl.status(f"Mount `{self.focus}` is no longer live.", tone=sl.Tone.WARNING))
        if snapshots:
            nodes.append(
                sl.choices(
                    *(
                        sl.choice(
                            f"{snapshot.id} — {snapshot.component.rsplit('.', 1)[-1]}",
                            key=snapshot.id,
                            description=_option_description(snapshot),
                        )
                        for snapshot in snapshots[:_SELECT_LIMIT]
                    ),
                    key="open",
                    selection=sl.controlled((), self._open),
                )
            )
        nodes.append(self._controls(back=False))
        return nodes

    def _row(self, snapshot: squid_discord.mount.MountSnapshot) -> str:
        mine = " *(this panel)*" if snapshot.id == self.own_id else ""
        key = self._session_key(snapshot.id)
        session = "" if key is None else f" · session `{key!r}`"
        location = f" · [jump]({snapshot.address.jump_url})" if snapshot.address is not None else ""
        return (
            f"`{snapshot.id}` **{snapshot.component.rsplit('.', 1)[-1]}**{mine}\n"
            f"gen {snapshot.generation} · {_flags(snapshot)} · age {_duration(snapshot.age)}"
            f" · idle {_duration(snapshot.idle)} · {_expiry(snapshot)}{session}{location}"
        )

    def _session_key(self, mount_id: str) -> Hashable | None:
        if self._registry is None:
            return None
        return next(
            (
                session.key
                for session in self._registry.active()
                if any(mount.id == mount_id for mount in session.mounts)
            ),
            None,
        )

    # --- Detail -------------------------------------------------------------------------

    def _detail(self, snapshot: squid_discord.mount.MountSnapshot, mount: squid_discord.Mount) -> Sequence[sl.LayoutNode]:
        children: list[sl.LayoutNode] = [sl.bullets(*_summary(snapshot), key="summary")]
        if snapshot.handler_keys:
            children.append(
                sl.section(
                    sl.heading("Handlers"),
                    sl.note(f"generation {snapshot.generation}"),
                    sl.code("\n".join(snapshot.handler_keys)),
                )
            )
        children.extend(self._plan_section(snapshot))
        children.append(
            sl.section(
                sl.heading("Component state"),
                sl.note("persisted fields only"),
                sl.code(_dump(_exported_state(mount))),
            )
        )
        children.append(
            sl.section(
                sl.heading("Reactivity"),
                sl.note("cell versions and what each computed last read"),
                sl.code(_dump_lines(_reactivity(mount))),
            )
        )
        children.append(
            sl.section(
                sl.heading("Presentation"),
                sl.note("cursors, selections, disclosures, strategies"),
                sl.code(_dump(_presentation(mount.presentation))),
            )
        )
        return [
            sl.section(sl.heading(f"Mount {snapshot.id}"), *children),
            self._controls(back=True),
        ]

    def _plan_section(self, snapshot: squid_discord.mount.MountSnapshot) -> Iterable[sl.LayoutNode]:
        if snapshot.report is None or snapshot.metrics is None:
            yield sl.section(sl.heading("Plan"), sl.note("nothing has been committed yet"))
            return
        metrics = snapshot.metrics
        yield sl.section(
            sl.heading("Plan"),
            sl.note(
                f"{metrics.states_explored} states explored · "
                f"cache {'hit' if metrics.cache_hit else 'miss'}"
                f"{' · search fell back' if metrics.search_fallback else ''}"
            ),
            sl.code(plan_text(snapshot)),
        )

    # --- Controls -----------------------------------------------------------------------

    def _controls(self, *, back: bool) -> sl.semantic.Actions:
        controls: list[sl.semantic.Action] = []
        if back:
            controls.append(sl.action("Back", self._back, key="back"))
        controls.extend(
            (
                sl.action("Refresh", self._refresh, key="refresh", emphasis=sl.semantic.Emphasis.SUBTLE),
                sl.action("Close", self._close, key="close", emphasis=sl.semantic.Emphasis.SUBTLE),
            )
        )
        return sl.actions(*controls, key="controls")

    async def _open(self, event: sl.ChoiceEvent) -> None:
        self.focus = event.selected[0]

    async def _back(self, event: sl.ActionEvent) -> None:
        self.focus = None

    async def _refresh(self, event: sl.ActionEvent) -> None:
        self.revision += 1

    async def _close(self, event: sl.ActionEvent) -> None:
        await event.finish()


class OperationalInspector(sl.Component):
    """The process-wide operational dashboard for the development owner."""

    section: str = sl.state("overview")
    mount_id: str | None = sl.state(None)
    session_id: str | None = sl.state(None)
    revision: int = sl.state(0)
    notice: str | None = sl.state(None)
    confirming_session: str | None = sl.state(None)

    def __init__(self, runtime: DevToolsRuntime) -> None:
        self._devtools_runtime = runtime

    def render(self) -> Sequence[sl.LayoutNode]:
        snapshot = self._devtools_runtime.snapshot()
        if self.section == "mounts":
            nodes = self._mounts(snapshot)
        elif self.section == "sessions":
            nodes = self._sessions(snapshot)
        elif self.section == "queues":
            nodes = self._queues(snapshot)
        elif self.section == "profile":
            nodes = self._profile(snapshot)
        elif self.section == "persistence":
            nodes = self._persistence(snapshot)
        else:
            nodes = self._overview(snapshot)
        if self.notice is None:
            return nodes
        return (sl.status(self.notice, tone=sl.Tone.INFO), *nodes)

    def _overview(self, snapshot: squid_discord.operations.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        counts = [
            f"mounts       {len(snapshot.mounts)}",
            f"sessions     {len(snapshot.sessions)}",
            f"reactor     {_reactor_summary(snapshot.reactor)}",
            f"topics      {_topic_summary(snapshot.topics)}",
            f"profile     {_profile_summary(snapshot.profiler)}",
            f"persistence {_durable_summary(snapshot.durable)}",
        ]
        return [
            sl.section(sl.heading("Operational dashboard"), sl.code("\n".join(counts))),
            self._section_choices(),
            self._controls(),
        ]

    def _mounts(self, snapshot: squid_discord.operations.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        if self.mount_id is not None:
            mount = squid_discord.live.find(self.mount_id)
            if mount is not None:
                detail = self._devtools_runtime.inspect_mount(self.mount_id)
                histories = (
                    "\n".join(
                        f"{history.name}: undo={len(history.undo)} redo={len(history.redo)} limit={history.limit}"
                        for history in detail.histories
                    )
                    or "(none)"
                )
                return [
                    sl.section(
                        sl.heading(f"Mount {self.mount_id}"),
                        sl.bullets(
                            *_summary(detail.snapshot),
                            f"**Middleware**\n{_dump(detail.middleware)}",
                            f"**Observed**\n{_dump(detail.observed)}",
                            f"**Followed**\n{_dump(detail.followed)}",
                            f"**History**\n{histories}",
                        ),
                    ),
                    self._controls(back=True),
                ]
            notice = f"Mount `{self.mount_id}` is no longer live."
        else:
            notice = None

        rows = [
            f"`{mount.id}` **{mount.component.rsplit('.', 1)[-1]}** · gen {mount.generation} · {_flags(mount)}"
            for mount in snapshot.mounts
        ]
        nodes: list[sl.LayoutNode] = [
            sl.section(
                sl.heading("Live mounts"),
                sl.bullets(*rows, key="mounts", page_size=8) if rows else sl.paragraph("No live mounts."),
            ),
        ]
        if snapshot.mounts:
            nodes.append(
                sl.choices(
                    *(
                        sl.choice(mount.id, key=mount.id, description=mount.component.rsplit(".", 1)[-1])
                        for mount in snapshot.mounts[:25]
                    ),
                    key="mount",
                    selection=sl.controlled((), self._open_mount),
                )
            )
        if notice is not None:
            nodes.insert(0, sl.status(notice, tone=sl.Tone.WARNING))
        return (*nodes, self._controls(back=True))

    def _sessions(self, snapshot: squid_discord.operations.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        if self.session_id is not None:
            session = next((item for item in snapshot.sessions if item.id == self.session_id), None)
            if session is not None:
                confirmation = (
                    sl.actions(
                        sl.action("Confirm close", self._confirm_close_session, key="confirm-close"),
                        sl.action("Cancel", self._cancel_close_session, key="cancel-close"),
                        key="confirmation",
                    )
                    if self.confirming_session == session.id
                    else sl.actions(
                        sl.action("Close session", self._request_close_session, key="close-session"),
                        key="session-actions",
                    )
                )
                return [
                    sl.section(
                        sl.heading(f"Session {session.id}"),
                        sl.bullets(
                            f"**Id**\n`{session.id}`",
                            f"**Key**\n`{session.key}`",
                            f"**Actor**\n{session.actor_id if session.actor_id is not None else 'none'}",
                            f"**Members**\n{_members(session)}",
                            f"**Mounts**\n{_dump(session.mounts)}",
                        ),
                    ),
                    confirmation,
                    self._controls(back=True),
                ]
            notice = f"Session `{self.session_id}` is no longer live."
        else:
            notice = None

        rows = [
            f"`{session.id}` · key `{session.key}` · mounts={len(session.mounts)} · members={_members(session)}"
            for session in snapshot.sessions
        ]
        nodes: list[sl.LayoutNode] = [
            sl.section(
                sl.heading("Live sessions"),
                sl.bullets(*rows, key="sessions", page_size=8) if rows else sl.paragraph("No live sessions."),
            ),
        ]
        if snapshot.sessions:
            nodes.append(
                sl.choices(
                    *(
                        sl.choice(session.id, key=session.id, description=f"mounts={len(session.mounts)}")
                        for session in snapshot.sessions[:25]
                    ),
                    key="session",
                    selection=sl.controlled((), self._open_session),
                )
            )
        if notice is not None:
            nodes.insert(0, sl.status(notice, tone=sl.Tone.WARNING))
        return (*nodes, self._controls(back=True))

    def _queues(self, snapshot: squid_discord.operations.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        lines = [
            f"reactor  {_reactor_summary(snapshot.reactor)}",
            f"topics   {_topic_summary(snapshot.topics)}",
        ]
        if snapshot.topics is not None:
            lines.extend(
                f"  {topic.topic} subscribers={topic.subscribers} queued={topic.queued} in_flight={topic.in_flight}"
                for topic in snapshot.topics.topics
            )
        return [sl.section(sl.heading("Queues and subscribers"), sl.code("\n".join(lines))), self._controls(back=True)]

    def _profile(self, snapshot: squid_discord.operations.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        health = snapshot.profiler.health
        lines = [
            f"process  {snapshot.profiler.process_id}",
            f"active   {health.active}",
            f"recent   {health.recent}",
            f"slow     {health.slow}",
            f"failed   {health.failed}",
            f"deadline {health.deadline_misses}",
        ]
        return [sl.section(sl.heading("Profiler"), sl.code("\n".join(lines))), self._controls(back=True)]

    def _persistence(self, snapshot: squid_discord.operations.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        durable = snapshot.durable
        if durable is None:
            body = "No durable session runtime is configured."
        else:
            body = "\n".join(
                (
                    f"running  {durable.running}",
                    f"active   {len(durable.active)}",
                    f"dirty    {len(durable.dirty)}",
                    f"recovery {_recovery_summary(durable.last_recovery)}",
                )
            )
        return [sl.section(sl.heading("Persistence"), sl.code(body)), self._controls(back=True)]

    def _section_choices(self) -> sl.LayoutNode:
        return sl.choices(
            sl.choice("Mounts", key="mounts"),
            sl.choice("Sessions", key="sessions"),
            sl.choice("Queues", key="queues"),
            sl.choice("Profiler", key="profile"),
            sl.choice("Persistence", key="persistence"),
            key="section",
            selection=sl.controlled((), self._select_section),
        )

    def _controls(self, *, back: bool = False) -> sl.semantic.Actions:
        controls: list[sl.semantic.Action] = []
        if back:
            controls.append(sl.action("Back", self._back, key="back"))
        controls.extend(
            (
                sl.action("Refresh", self._refresh, key="refresh", emphasis=sl.semantic.Emphasis.SUBTLE),
                sl.action("Close", self._close, key="close", emphasis=sl.semantic.Emphasis.SUBTLE),
            )
        )
        return sl.actions(*controls, key="controls")

    async def _select_section(self, event: sl.ChoiceEvent) -> None:
        self.section = event.selected[0]
        self.mount_id = None
        self.session_id = None

    async def _open_mount(self, event: sl.ChoiceEvent) -> None:
        self.mount_id = event.selected[0]

    async def _open_session(self, event: sl.ChoiceEvent) -> None:
        self.session_id = event.selected[0]

    async def _request_close_session(self, event: sl.ActionEvent) -> None:
        self.confirming_session = self.session_id

    async def _cancel_close_session(self, event: sl.ActionEvent) -> None:
        self.confirming_session = None

    async def _confirm_close_session(self, event: sl.ActionEvent) -> None:
        if self.session_id is None:
            return
        try:
            await self._devtools_runtime.close_session(self.session_id, confirmed=True)
            self.notice = f"Session `{self.session_id}` closed."
            self.session_id = None
        except Exception as error:
            self.notice = f"Close failed: {type(error).__name__}: {error}"
        finally:
            self.confirming_session = None

    async def _back(self, event: sl.ActionEvent) -> None:
        self.mount_id = None
        self.session_id = None
        self.confirming_session = None
        self.section = "overview"

    async def _refresh(self, event: sl.ActionEvent) -> None:
        self.revision += 1

    async def _close(self, event: sl.ActionEvent) -> None:
        await event.finish()


def scene_attachment(snapshot: squid_discord.mount.MountSnapshot) -> sl.document.Asset | None:
    """The mount's committed scene as the protocol JSON, for reading outside Discord."""
    if snapshot.scene is None:
        return None
    return sl.document.Asset(
        key="scene",
        name=f"scene-{snapshot.id}-gen{snapshot.generation}.json",
        media_type="application/json",
        source=sl.document.InlineAsset(sl.scene.Codec.dumps(snapshot.scene).encode()),
    )


def plan_text(snapshot: squid_discord.mount.MountSnapshot) -> str:
    """Render the retained plan report, grouping adaptations by severity."""
    report = snapshot.report
    if report is None:
        return "Nothing has been committed yet, so this mount has no plan report."
    lines = [
        f"logical  {report.logical_fingerprint or '—'}",
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


def metrics_text(snapshot: squid_discord.mount.MountSnapshot) -> str:
    """Render the planner work and cache disposition retained by a mount."""
    metrics = snapshot.metrics
    if metrics is None:
        return "Nothing has been committed yet, so this mount has no planner metrics."
    return "\n".join(
        (
            f"states_explored: {metrics.states_explored}",
            f"search_fallback: {metrics.search_fallback}",
            f"cache: {'hit' if metrics.cache_hit else 'miss'}",
        )
    )


def _summary(snapshot: squid_discord.mount.MountSnapshot) -> list[str]:
    entries = [
        f"**Component**\n`{snapshot.component}`",
        f"**Generation**\n{snapshot.generation} · {_flags(snapshot)} · {snapshot.suppressed} suppressed",
        f"**Timing**\nage {_duration(snapshot.age)} · idle {_duration(snapshot.idle)} · {_expiry(snapshot)}",
        f"**Access**\n{_access_text(snapshot.access)}",
    ]
    address = snapshot.address
    if address is None:
        # A mount sent through an unwaited interaction response has never seen its message.
        entries.append("**Message**\nnot located yet — nobody has clicked it")
    else:
        where = "ephemeral" if address.ephemeral else f"<#{address.channel_id}>"
        entries.append(f"**Message**\n[{address.message_id}]({address.jump_url}) in {where}")
    return entries


def _access_text(access: squid_discord.AccessPolicy) -> str:
    """Describe the mount's admission policy without exposing callback internals."""
    if isinstance(access, squid_discord.Everyone):
        return "Everyone"
    if isinstance(access, squid_discord.Owner):
        return f"Owner (<@{access.user_id}>)"
    if isinstance(access, squid_discord.Users):
        users = ", ".join(f"<@{user_id}>" for user_id in sorted(access.user_ids))
        return f"Users ({users})"
    if isinstance(access, squid_discord.access.Check):
        return "Check"
    return type(access).__name__


def _option_description(snapshot: squid_discord.mount.MountSnapshot) -> str:
    return f"gen {snapshot.generation} · idle {_duration(snapshot.idle)} · {_expiry(snapshot)}"


def _flags(snapshot: squid_discord.mount.MountSnapshot) -> str:
    flags = []
    if snapshot.lifecycle is squid_discord.mount.MountLifecycle.RENEWAL_ARMED:
        flags.append("renewal armed")
    if snapshot.pending:
        flags.append("dirty")
    if snapshot.finished:
        flags.append("finished")
    if snapshot.address is not None and snapshot.address.ephemeral:
        flags.append("ephemeral")
    return " ".join(flags) if flags else "clean"


def _expiry(snapshot: squid_discord.mount.MountSnapshot) -> str:
    timeout = "no timeout" if snapshot.expires_in is None else f"timeout in {_duration(snapshot.expires_in)}"
    if snapshot.handle_expires_in is None:
        return timeout
    return f"{timeout} · edit handle {_duration(snapshot.handle_expires_in)} left"


def _duration(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{total % 3600 // 60:02d}m"


def _reactor_summary(snapshot: squid_discord.ReactorSnapshot | None) -> str:
    if snapshot is None:
        return "unconfigured"
    return f"queued={snapshot.queued} in_flight={snapshot.in_flight} failed={snapshot.failed}"


def _topic_summary(snapshot: BusSnapshot | None) -> str:
    if snapshot is None:
        return "unconfigured"
    return f"known={len(snapshot.topics)} queued={snapshot.queued} failed={snapshot.failed}"


def _profile_summary(snapshot: RuntimeSnapshot) -> str:
    health = snapshot.health
    return f"active={health.active} failed={health.retained_failed}"


def _durable_summary(snapshot: DurableRuntimeSnapshot | None) -> str:
    if snapshot is None:
        return "unconfigured"
    return f"running={snapshot.running} active={len(snapshot.active)} dirty={len(snapshot.dirty)}"


def _recovery_summary(report: RecoveryReport | None) -> str:
    if report is None:
        return "never"
    return f"restored={len(report.restored)} failed={len(report.failed)}"


def _exported_state(mount: squid_discord.Mount) -> dict[str, object]:
    """Declared persistent state for every component in the committed tree, by path.

    Each component is exported on its own, so one field whose value refuses to be formatted
    does not cost the whole dump.
    """
    exported: dict[str, object] = {}
    for path, component in mount.runtime.components.items():
        try:
            exported[path] = sl.runtime.export_state(component)
        except Exception as error:
            exported[path] = f"<unreadable: {type(error).__name__}: {error}>"
    return exported


def _reactivity(mount: squid_discord.Mount) -> list[str]:
    """Every cell's version and every computed's current source set, resolved to names.

    Names are resolved across the whole tree rather than per component, because a computed
    may read another component's state and printing an identity would say nothing.
    """
    components = mount.runtime.components
    # Deduplicated by identity: several panels usually hold the very same namespace, and
    # that is the point of one.
    namespaces = {id(owner): owner for owner, _ in (_pair(topic) for topic in mount.observed) if owner is not None}
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
        lines.append(f"{namespace!r} (shared)")
        for name, cell in sl.runtime.inspect_cells(namespace).items():
            marks = " ".join(mark for mark, on in (("opaque", cell.opaque), ("default", not cell.assigned)) if on)
            lines.append(f"  {name} v{cell.version}{f'  [{marks}]' if marks else ''}")
    return lines


def _pair(topic: object) -> tuple[sl.runtime.Shared[Any] | None, object]:
    """An observed address split into its namespace and cell, or `(None, topic)` otherwise.

    Read from what the render observed rather than from what it subscribed to, so a mount
    with no reactor still reports the shared state it is showing.
    """
    match topic:
        case (sl.runtime.Shared() as owner, descriptor):
            return owner, descriptor
        case _:
            return None, topic


def _dump_lines(lines: list[str]) -> str:
    return "\n".join(lines) or "(no components)"


def _presentation(session: sl.runtime.PresentationSession) -> dict[str, object]:
    return {
        "cursors": dict(session.cursors),
        "selections": dict(session.selections),
        "disclosures": dict(session.disclosures),
        "strategies": dict(session.strategies),
    }


def _members(session: squid_discord.operations.SessionInspection) -> str:
    """Membership as `used/limit`, or a bare count when the session is unbounded."""
    count = len(session.members)
    return f"{count}" if session.capacity is None else f"{count}/{session.capacity}"


def _dump(value: object) -> str:
    """Whatever this is, as something readable. `repr` is the floor, not the ideal."""
    return pprint.pformat(value, width=88, sort_dicts=True) or "{}"
