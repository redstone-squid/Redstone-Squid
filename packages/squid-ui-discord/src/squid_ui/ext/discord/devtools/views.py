"""Discord dashboards for live message roots and operational diagnostics.

The host authorizes access before opening a dashboard. Labels remain untranslated
because the panels primarily display Python identifiers and diagnostic event codes.
"""

from collections.abc import Awaitable, Callable, Hashable, Iterable, Sequence
from enum import StrEnum
from typing import override

import discord
import squid_ui as sl

# The package this module belongs to, imported by name so the devtools panel reads the way
# a host would write it. Safe despite `__init__` importing this module: every use below is
# either a deferred annotation or inside a function body, so nothing resolves at import.
import squid_ui.ext.discord
from squid_reactivity.diagnostics import (
    EventLedger,
)
from squid_ui.ext.discord.devtools.presentation import (
    component_state,
    dump,
    dump_lines,
    durable_summary,
    duration_text,
    event_summary,
    expiry,
    flags,
    members,
    operational_attachment,
    plan_text,
    presentation_state,
    profile_summary,
    reactivity,
    recovery_summary,
    root_summary,
    scene_attachment,
    scheduler_summary,
    topic_summary,
    trace_summary,
)
from squid_ui.ext.discord.devtools.runtime import (
    DevToolsAction,
    DevToolsRuntime,
    DurableRecordInspection,
    OperationalSnapshot,
)

SESSION_SECONDS = 300
"""Short-lived on purpose: an inspector left open is one more message root in its own list."""

_SELECT_LIMIT = 25
"""Discord's option cap. The list itself pages; the picker offers the newest of them."""


class DevToolsSection(StrEnum):
    """Stable deep-link targets in the operational dashboard."""

    OVERVIEW = "overview"
    ROOTS = "roots"
    SESSIONS = "sessions"
    ROUTES = "routes"
    ACTIVITY = "activity"
    QUEUES = "queues"
    PROFILE = "profile"
    PERSISTENCE = "persistence"


class MessageRootInspector(sl.Component):
    """Inspect current roots and one selected root, rereading process state each render.

    The Refresh action invalidates the panel so finished roots disappear on its next render.
    """

    focus: str | None = sl.state(None)
    revision: int = sl.state(0)
    """Bumped by Refresh. A handler that changes nothing leaves the message root clean and the
    message stale, so re-reading the world has to be a state change like any other."""

    own_id: str | None = sl.state(None)
    """This panel's own message root id, set by the cog once the message root exists — it is in the list
    like everything else, and unlabelled it reads as a mystery session."""

    def __init__(self, *, focus: str | None = None, manager: squid_ui.ext.discord.SessionManager | None = None) -> None:
        self.focus = focus
        self._manager = manager

    @override
    def render(self) -> Sequence[sl.LayoutNode]:
        # Both are explicit invalidation tokens for process state that is not itself
        # reactive. Observe them even when an empty manager makes their usual branches
        # unreachable, otherwise a cached empty list cannot be refreshed.
        _ = self.revision, self.own_id
        message_roots = squid_ui.ext.discord.message_roots()
        if self.focus is not None:
            target = squid_ui.ext.discord.live.find(self.focus)
            if target is not None:
                return self._detail(target.snapshot(), target)
        return self._list(message_roots)

    # --- List ---------------------------------------------------------------------------

    def _list(self, message_roots: Sequence[squid_ui.ext.discord.MessageRoot]) -> Sequence[sl.LayoutNode]:
        # Newest first: the session someone is asking about is almost always the one they
        # just opened.
        snapshots = sorted(
            (message_root.snapshot() for message_root in message_roots), key=lambda snapshot: snapshot.age
        )
        missing = self.focus is not None
        body: sl.LayoutNode
        if snapshots:
            body = sl.bullets(*(self._row(snapshot) for snapshot in snapshots), key="roots", page_size=8)
        else:
            body = sl.paragraph("Nothing is mounted. Open a panel and run this again.")

        nodes: list[sl.LayoutNode] = [sl.section(sl.heading(f"Message roots — {len(snapshots)}"), body)]
        if missing:
            nodes.insert(0, sl.status(sl.md(t"MessageRoot `{self.focus}` is no longer live."), tone=sl.Tone.WARNING))
        if snapshots:
            nodes.append(
                sl.choices(
                    *(
                        sl.choice(
                            f"{snapshot.id} — {snapshot.component.rsplit('.', 1)[-1]}",
                            key=snapshot.id,
                            description=f"gen {snapshot.generation} · idle {duration_text(snapshot.idle)} · {expiry(snapshot)}",
                        )
                        for snapshot in snapshots[:_SELECT_LIMIT]
                    ),
                    key="open",
                    selection=sl.controlled((), self._open),
                )
            )
        nodes.append(self._controls(back=False))
        return nodes

    def _row(self, snapshot: squid_ui.ext.discord.MessageRootSnapshot) -> sl.TextLike:
        mine = sl.md(" *(this panel)*") if snapshot.id == self.own_id else ""
        key = self._session_key(snapshot.id)
        session = "" if key is None else sl.md(t" · session `{key!r}`")
        location = (
            "" if snapshot.address is None else sl.text.Text(t" · {sl.text_link('jump', snapshot.address.jump_url)}")
        )
        return sl.md(
            t"`{snapshot.id}` **{snapshot.component.rsplit('.', 1)[-1]}**{mine}\n"
            t"gen {snapshot.generation} · {flags(snapshot)} · age {duration_text(snapshot.age)}"
            t" · idle {duration_text(snapshot.idle)} · {expiry(snapshot)}{session}{location}"
        )

    def _session_key(self, message_root_id: str) -> Hashable | None:
        if self._manager is None:
            return None
        return next(
            (
                session.key
                for session in self._manager.active()
                if any(message_root.id == message_root_id for message_root in session.message_roots)
            ),
            None,
        )

    # --- Detail -------------------------------------------------------------------------

    def _detail(
        self, snapshot: squid_ui.ext.discord.MessageRootSnapshot, message_root: squid_ui.ext.discord.MessageRoot
    ) -> Sequence[sl.LayoutNode]:
        children: list[sl.LayoutNode] = [sl.bullets(*root_summary(snapshot), key="summary")]
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
                sl.note("declared fields; collaborators omitted"),
                sl.code(dump(component_state(message_root))),
            )
        )
        children.append(
            sl.section(
                sl.heading("Reactivity"),
                sl.note("cell versions and what each computed last read"),
                sl.code(dump_lines(reactivity(message_root))),
            )
        )
        children.append(
            sl.section(
                sl.heading("Presentation"),
                sl.note("cursors, selections, disclosures, strategies"),
                sl.code(dump(presentation_state(message_root.presentation))),
            )
        )
        return [
            sl.section(sl.heading(f"MessageRoot {snapshot.id}"), *children),
            self._controls(back=True),
        ]

    def _plan_section(self, snapshot: squid_ui.ext.discord.MessageRootSnapshot) -> Iterable[sl.LayoutNode]:
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

    def _controls(self, *, back: bool) -> sl.semantic.ActionControls:
        controls: list[sl.semantic.ActionControl] = []
        if back:
            controls.append(sl.action_control("Back", self._back, key="back"))
        controls.extend(
            (
                sl.action_control("Refresh", self._refresh, key="refresh", emphasis=sl.semantic.Emphasis.SUBTLE),
                sl.action_control("Close", self._close, key="close", emphasis=sl.semantic.Emphasis.SUBTLE),
            )
        )
        return sl.action_controls(*controls, key="controls")

    async def _open(self, event: sl.ChoiceEvent) -> None:
        self.focus = event.selected[0]

    async def _back(self, event: sl.ActionEvent) -> None:
        self.focus = None

    async def _refresh(self, event: sl.ActionEvent) -> None:
        self.revision += 1

    async def _close(self, event: sl.ActionEvent) -> None:
        event.finish_on_success()


class OperationalInspector(sl.Component):
    """The process-wide operational dashboard for the development owner."""

    section: str = sl.state("overview")
    message_root_id: str | None = sl.state(None)
    session_id: str | None = sl.state(None)
    revision: int = sl.state(0)
    notice: sl.TextLike | None = sl.state(None)
    confirming_session: str | None = sl.state(None)
    confirming_action: str | None = sl.state(None)
    selected_records: tuple[str, ...] = sl.state(())

    def __init__(
        self,
        runtime: DevToolsRuntime,
        *,
        client: discord.Client | None = None,
        action_ledger: EventLedger | None = None,
    ) -> None:
        self._devtools_runtime = runtime
        self._client = client
        self._action_ledger = action_ledger

    @sl.resource(pending=sl.resources.PendingMode.EXPLICIT)
    async def persistence_records(self) -> tuple[DurableRecordInspection, ...]:
        """Load persisted record metadata while the persistence section exists."""
        return await self._devtools_runtime.records()

    @override
    def render(self) -> Sequence[sl.LayoutNode]:
        snapshot = self._devtools_runtime.snapshot()
        if self.section == DevToolsSection.ROOTS:
            nodes = self._roots(snapshot)
        elif self.section == DevToolsSection.SESSIONS:
            nodes = self._sessions(snapshot)
        elif self.section == DevToolsSection.ROUTES:
            nodes = self._routes()
        elif self.section == DevToolsSection.ACTIVITY:
            nodes = self._activity(snapshot)
        elif self.section == DevToolsSection.QUEUES:
            nodes = self._queues(snapshot)
        elif self.section == DevToolsSection.PROFILE:
            nodes = self._profile(snapshot)
        elif self.section == DevToolsSection.PERSISTENCE:
            nodes = self._persistence(snapshot)
        else:
            nodes = self._overview(snapshot)
        content = (self._section_choices(), *nodes)
        if self.notice is None:
            return content
        return (sl.status(self.notice, tone=sl.Tone.INFO), *content)

    def _overview(self, snapshot: OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        counts = [
            f"roots        {len(snapshot.message_roots)}",
            f"sessions     {len(snapshot.sessions)}",
            f"scheduler     {scheduler_summary(snapshot.scheduler)}",
            f"topics      {topic_summary(snapshot.topics)}",
            f"profile     {profile_summary(snapshot.profiler)}",
            f"persistence {durable_summary(snapshot.durable)}",
        ]
        return [
            sl.section(
                sl.heading("Operational dashboard"),
                sl.code("\n".join(counts)),
                sl.download(
                    operational_attachment(snapshot),
                    label="Download snapshot",
                    key="operational-snapshot",
                    description="Bounded JSON diagnostics from this refresh",
                ),
            ),
            self._controls(),
        ]

    def _roots(self, snapshot: OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        if self.message_root_id is not None:
            message_root = squid_ui.ext.discord.live.find(self.message_root_id)
            if message_root is not None:
                detail = self._devtools_runtime.inspect_root(self.message_root_id)
                histories = (
                    "\n".join(
                        f"{history.name}: undo={len(history.undo)} redo={len(history.redo)} limit={history.limit}"
                        for history in detail.histories
                    )
                    or "(none)"
                )
                children: list[sl.LayoutNode] = [
                    sl.section(
                        sl.heading(f"MessageRoot {self.message_root_id}"),
                        sl.bullets(
                            *root_summary(detail.snapshot),
                            sl.md(t"**Middleware**\n{dump(detail.middleware)}"),
                            sl.md(t"**Observed**\n{dump(detail.observed)}"),
                            sl.md(t"**Followed**\n{dump(detail.followed)}"),
                            sl.md(t"**History**\n{histories}"),
                            key="root_detail",
                        ),
                    ),
                ]
                children.extend(
                    (
                        sl.section(sl.heading("Plan"), sl.code(plan_text(detail.snapshot))),
                        sl.section(
                            sl.heading("Component state"),
                            sl.code(dump(component_state(message_root))),
                        ),
                        sl.section(sl.heading("Reactivity"), sl.code(dump_lines(reactivity(message_root)))),
                        sl.section(
                            sl.heading("Presentation"),
                            sl.code(dump(presentation_state(message_root.presentation))),
                        ),
                    )
                )
                asset = scene_attachment(detail.snapshot)
                if asset is not None:
                    children.append(
                        sl.download(
                            asset,
                            label="Download scene",
                            key="scene",
                            description="Committed scene protocol JSON",
                        )
                    )
                if self._devtools_runtime.policy.permits(DevToolsAction.REFRESH_MOUNT):
                    children.append(
                        sl.action_controls(
                            sl.action_control("Refresh root", self._refresh_selected_root, key="refresh-root"),
                            key="root-actions",
                        )
                    )
                return (*children, self._controls(back=True))
            notice = sl.md(t"MessageRoot `{self.message_root_id}` is no longer live.")
        else:
            notice = None

        rows = [
            sl.md(
                t"`{message_root.id}` **{message_root.component.rsplit('.', 1)[-1]}** · gen {message_root.generation} · {flags(message_root)}"
            )
            for message_root in snapshot.message_roots
        ]
        nodes: list[sl.LayoutNode] = [
            sl.section(
                sl.heading("Message roots"),
                sl.bullets(*rows, key="roots", page_size=8) if rows else sl.paragraph("No message roots."),
            ),
        ]
        if snapshot.message_roots:
            nodes.append(
                sl.choices(
                    *(
                        sl.choice(
                            message_root.id, key=message_root.id, description=message_root.component.rsplit(".", 1)[-1]
                        )
                        for message_root in snapshot.message_roots[:25]
                    ),
                    key="message root",
                    selection=sl.controlled((), self._open_root),
                )
            )
        if notice is not None:
            nodes.insert(0, sl.status(notice, tone=sl.Tone.WARNING))
        return (*nodes, self._controls(back=True))

    def _sessions(self, snapshot: OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        if self.session_id is not None:
            session = next((item for item in snapshot.sessions if item.id == self.session_id), None)
            if session is not None:
                detail_nodes: list[sl.LayoutNode] = [
                    sl.section(
                        sl.heading(f"Session {session.id}"),
                        sl.bullets(
                            sl.md(t"**Id**\n`{session.id}`"),
                            sl.md(t"**Key**\n`{session.key}`"),
                            sl.md(t"**Actor**\n{session.actor_id if session.actor_id is not None else 'none'}"),
                            sl.md(t"**Members**\n{members(session)}"),
                            sl.md(t"**Mounts**\n{dump(session.message_roots)}"),
                            key="session_detail",
                        ),
                    ),
                ]
                if self._devtools_runtime.policy.permits(DevToolsAction.CLOSE_SESSION):
                    confirmation = (
                        sl.action_controls(
                            sl.action_control("Confirm close", self._confirm_close_session, key="confirm-close"),
                            sl.action_control("Cancel", self._cancel_close_session, key="cancel-close"),
                            key="confirmation",
                        )
                        if self.confirming_session == session.id
                        else sl.action_controls(
                            sl.action_control("Close session", self._request_close_session, key="close-session"),
                            key="session-actions",
                        )
                    )
                    detail_nodes.append(confirmation)
                return (*detail_nodes, self._controls(back=True))
            notice = sl.md(t"Session `{self.session_id}` is no longer live.")
        else:
            notice = None

        rows = [
            sl.md(
                t"`{session.id}` · key `{session.key}` · roots={len(session.message_roots)} · members={members(session)}"
            )
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
                        sl.choice(session.id, key=session.id, description=f"roots={len(session.message_roots)}")
                        for session in snapshot.sessions[:25]
                    ),
                    key="session",
                    selection=sl.controlled((), self._open_session),
                )
            )
        if notice is not None:
            nodes.insert(0, sl.status(notice, tone=sl.Tone.WARNING))
        return (*nodes, self._controls(back=True))

    def _queues(self, snapshot: OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        lines = [
            f"scheduler  {scheduler_summary(snapshot.scheduler)}",
            f"topics   {topic_summary(snapshot.topics)}",
        ]
        if snapshot.topics is not None:
            lines.extend(
                f"  {topic.topic} subscribers={topic.subscribers} queued={topic.queued} in_flight={topic.in_flight}"
                for topic in snapshot.topics.topics
            )
        nodes: list[sl.LayoutNode] = [sl.section(sl.heading("Queues and subscribers"), sl.code("\n".join(lines)))]
        if self._devtools_runtime.policy.permits(DevToolsAction.WAIT_IDLE):
            nodes.append(
                sl.action_controls(
                    sl.action_control("Wait for idle", self._wait_idle, key="wait-idle"), key="queue-actions"
                )
            )
        return (*nodes, self._controls(back=True))

    def _profile(self, snapshot: OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        health = snapshot.profiler.health
        lines = [
            f"process  {snapshot.profiler.process_id}",
            f"active   {health.active}",
            f"recent   {health.retained_recent}",
            f"slow     {health.retained_slow}",
            f"failed   {health.retained_failed}",
            f"deadline {health.retained_deadline_misses}",
        ]
        retained = (
            *snapshot.profiler.recent,
            *snapshot.profiler.slow,
            *snapshot.profiler.failed,
            *snapshot.profiler.deadline_misses,
        )
        traces = sorted({trace.trace_id: trace for trace in retained}.values(), key=lambda trace: trace.started)[-12:]
        nodes: list[sl.LayoutNode] = [
            sl.section(sl.heading("Profiler"), sl.code("\n".join(lines))),
            sl.section(
                sl.heading("Recent traces"),
                sl.code("\n".join(trace_summary(trace) for trace in traces) or "No retained traces."),
            ),
        ]
        if self._devtools_runtime.policy.permits(DevToolsAction.CLEAR_PROFILE):
            nodes.append(
                sl.action_controls(
                    sl.action_control("Clear profile", self._clear_profile, key="clear-profile"),
                    key="profile-actions",
                )
            )
        return (*nodes, self._controls(back=True))

    def _persistence(self, snapshot: OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        durable = snapshot.durable
        record_nodes: list[sl.LayoutNode] = []
        if durable is None:
            body = "No durable session runtime is configured."
        else:
            body = "\n".join(
                (
                    f"running  {durable.running}",
                    f"active   {len(durable.active)}",
                    f"dirty    {len(durable.dirty)}",
                    f"recovery {recovery_summary(durable.last_recovery)}",
                )
            )
            record_state = self.persistence_records.status
            previous = (
                record_state.previous if isinstance(record_state, sl.resources.Pending | sl.resources.Failed) else None
            )
            if isinstance(record_state, sl.resources.Ready):
                records = record_state.value
            elif previous is not None:
                records = previous.value
            else:
                records = ()
            if isinstance(record_state, sl.resources.Pending) and previous is None:
                record_nodes.append(sl.status("Loading persisted records."))
            elif isinstance(record_state, sl.resources.Failed) and previous is None:
                record_nodes.append(sl.status(f"Record load failed: {record_state.error}", tone=sl.Tone.WARNING))
            else:
                record_lines = (
                    "No persisted records."
                    if not records
                    else "\n".join(
                        f"{record.key} scope={record.scope} snapshot={record.snapshot_bytes}B record={record.record_bytes}B"
                        for record in records
                    )
                )
                record_nodes.append(sl.section(sl.heading("Persisted records"), sl.code(record_lines)))
                if records and self._devtools_runtime.policy.permits(DevToolsAction.PURGE_PERSISTENCE):
                    record_nodes.append(
                        sl.choices(
                            *(
                                sl.choice(record.key, key=record.key, description=record.scope)
                                for record in records[:25]
                            ),
                            key="purge-records",
                            selection=sl.controlled(self.selected_records, self._select_records),
                            minimum=0,
                            maximum=min(25, len(records)),
                        )
                    )
        nodes: list[sl.LayoutNode] = [sl.section(sl.heading("Persistence"), sl.code(body)), *record_nodes]
        actions: list[sl.semantic.ActionControl] = []
        if durable is not None and self._devtools_runtime.policy.permits(DevToolsAction.FLUSH_PERSISTENCE):
            actions.append(sl.action_control("Flush", self._flush_persistence, key="flush"))
        if durable is not None and self._devtools_runtime.policy.permits(DevToolsAction.RECOVER_PERSISTENCE):
            if self.confirming_action == DevToolsAction.RECOVER_PERSISTENCE:
                actions.extend(
                    (
                        sl.action_control("Confirm recovery", self._confirm_recovery, key="confirm-recovery"),
                        sl.action_control("Cancel", self._cancel_action, key="cancel-recovery"),
                    )
                )
            else:
                actions.append(sl.action_control("Recover", self._request_recovery, key="recover"))
        if (
            durable is not None
            and self.selected_records
            and self._devtools_runtime.policy.permits(DevToolsAction.PURGE_PERSISTENCE)
        ):
            if self.confirming_action == DevToolsAction.PURGE_PERSISTENCE:
                actions.extend(
                    (
                        sl.action_control("Confirm purge", self._confirm_purge, key="confirm-purge"),
                        sl.action_control("Cancel", self._cancel_action, key="cancel-purge"),
                    )
                )
            else:
                actions.append(sl.action_control("Purge selected", self._request_purge, key="purge"))
        if actions:
            nodes.append(sl.action_controls(*actions, key="persistence-actions"))
        return (*nodes, self._controls(back=True))

    def _routes(self) -> Sequence[sl.LayoutNode]:
        if self._client is None:
            body = "Route inspection is unavailable because no Discord client was supplied."
        else:
            from squid_ui.ext.discord.routing import routers

            lines: list[str] = []
            for index, router in enumerate(routers(self._client), start=1):
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
        return (sl.section(sl.heading("Routed controls"), sl.code(body)), self._controls(back=True))

    def _activity(self, snapshot: OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        events = () if self._action_ledger is None else self._action_ledger.events[-20:]
        event_text = "No retained causal events." if not events else "\n".join(event_summary(event) for event in events)
        retained = (
            *snapshot.profiler.recent,
            *snapshot.profiler.slow,
            *snapshot.profiler.failed,
            *snapshot.profiler.deadline_misses,
        )
        dispatches = sorted(
            {
                trace.trace_id: trace for trace in retained if trace.operation.value in {"dispatch", "route_dispatch"}
            }.values(),
            key=lambda trace: trace.started,
        )[-20:]
        timeline = (
            "No retained dispatches." if not dispatches else "\n".join(trace_summary(trace) for trace in dispatches)
        )
        return (
            sl.section(sl.heading("Action results"), sl.code(event_text)),
            sl.section(sl.heading("Dispatch timeline"), sl.code(timeline)),
            self._controls(back=True),
        )

    def _section_choices(self) -> sl.LayoutNode:
        return sl.choices(
            sl.choice("Overview", key="overview"),
            sl.choice("Roots", key="roots"),
            sl.choice("Sessions", key="sessions"),
            sl.choice("Routes", key="routes"),
            sl.choice("Activity", key="activity"),
            sl.choice("Queues", key="queues"),
            sl.choice("Profiler", key="profile"),
            sl.choice("Persistence", key="persistence"),
            key="section",
            selection=sl.controlled((self.section,), self._select_section),
        )

    def _controls(self, *, back: bool = False) -> sl.semantic.ActionControls:
        controls: list[sl.semantic.ActionControl] = []
        if back:
            controls.append(sl.action_control("Back", self._back, key="back"))
        controls.extend(
            (
                sl.action_control("Refresh", self._refresh, key="refresh", emphasis=sl.semantic.Emphasis.SUBTLE),
                sl.action_control("Close", self._close, key="close", emphasis=sl.semantic.Emphasis.SUBTLE),
            )
        )
        return sl.action_controls(*controls, key="controls")

    async def _select_section(self, event: sl.ChoiceEvent) -> None:
        self.section = event.selected[0]
        self.message_root_id = None
        self.session_id = None
        self.confirming_session = None
        self.selected_records = ()
        self.confirming_action = None

    async def _open_root(self, event: sl.ChoiceEvent) -> None:
        self.message_root_id = event.selected[0]

    async def _open_session(self, event: sl.ChoiceEvent) -> None:
        self.session_id = event.selected[0]

    async def _select_records(self, event: sl.ChoiceEvent) -> None:
        self.selected_records = event.selected
        self.confirming_action = None

    async def _request_close_session(self, event: sl.ActionEvent) -> None:
        self.confirming_session = self.session_id

    async def _cancel_close_session(self, event: sl.ActionEvent) -> None:
        self.confirming_session = None

    async def _confirm_close_session(self, event: sl.ActionEvent) -> None:
        session_id = self.session_id
        if session_id is None:
            return

        async def close() -> None:
            try:
                await self._devtools_runtime.close_session(session_id, confirmed=True)
            except Exception as error:
                with sl.runtime.transaction():
                    self.notice = f"Close failed: {type(error).__name__}: {error}"
                raise
            else:
                with sl.runtime.transaction():
                    self.notice = sl.md(t"Session `{session_id}` closed.")
                    self.session_id = None

        self.confirming_session = None
        event.on_success(close, name="Close session from devtools")

    def _run_operation(
        self,
        event: sl.ActionEvent,
        operation: Callable[[], Awaitable[str]],
        *,
        name: str,
    ) -> None:
        async def run() -> None:
            try:
                detail = await operation()
            except Exception as error:
                with sl.runtime.transaction():
                    self.notice = f"{name} failed: {type(error).__name__}: {error}"
                raise
            else:
                with sl.runtime.transaction():
                    self.notice = detail
                    self.revision += 1

        event.on_success(run, name=f"{name} from devtools")

    async def _refresh_selected_root(self, event: sl.ActionEvent) -> None:
        message_root_id = self.message_root_id
        if message_root_id is None:
            return

        async def refresh() -> str:
            return (await self._devtools_runtime.refresh_root(message_root_id)).detail

        self._run_operation(event, refresh, name="Refresh")

    async def _wait_idle(self, event: sl.ActionEvent) -> None:
        async def wait() -> str:
            return (await self._devtools_runtime.wait_idle()).detail

        self._run_operation(event, wait, name="Wait")

    async def _clear_profile(self, event: sl.ActionEvent) -> None:
        async def clear() -> str:
            return self._devtools_runtime.clear_profile().detail

        self._run_operation(event, clear, name="Clear")

    async def _flush_persistence(self, event: sl.ActionEvent) -> None:
        async def flush() -> str:
            return (await self._devtools_runtime.flush_persistence()).detail

        self._run_operation(event, flush, name="Flush")

    async def _request_recovery(self, event: sl.ActionEvent) -> None:
        del event
        self.confirming_action = DevToolsAction.RECOVER_PERSISTENCE

    async def _cancel_action(self, event: sl.ActionEvent) -> None:
        del event
        self.confirming_action = None

    async def _confirm_recovery(self, event: sl.ActionEvent) -> None:
        async def recover() -> str:
            return (await self._devtools_runtime.recover_persistence(confirmed=True)).detail

        self.confirming_action = None
        self._run_operation(event, recover, name="Recovery")

    async def _request_purge(self, event: sl.ActionEvent) -> None:
        self.confirming_action = DevToolsAction.PURGE_PERSISTENCE

    async def _confirm_purge(self, event: sl.ActionEvent) -> None:
        record_keys = self.selected_records

        async def purge() -> str:
            results = await self._devtools_runtime.purge_persistence(record_keys, confirmed=True)
            with sl.runtime.transaction():
                self.selected_records = ()
            await self.persistence_records.reload()
            return f"Persistence purge completed; deleted={sum(result.deleted for result in results)}."

        self.confirming_action = None
        self._run_operation(event, purge, name="Purge")

    async def _back(self, event: sl.ActionEvent) -> None:
        self.message_root_id = None
        self.session_id = None
        self.confirming_session = None
        self.confirming_action = None
        self.selected_records = ()
        self.section = DevToolsSection.OVERVIEW

    async def _refresh(self, event: sl.ActionEvent) -> None:
        del event
        if self.section == DevToolsSection.PERSISTENCE and self._devtools_runtime.durable is not None:
            await self.persistence_records.reload()
        self.revision += 1

    async def _close(self, event: sl.ActionEvent) -> None:
        event.finish_on_success()
