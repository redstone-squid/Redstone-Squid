"""The `/dev ui` inspector: every live message root, and why one of them is odd.

squid-ui has excellent *planning* diagnostics — reports, fingerprints, plan metrics —
and every one of them describes a render that already happened. Nothing answered "show me
the UI sessions this process is holding right now". `squid_ui_discord.message_roots()` is that list, and
this component is the reading of it.

Deliberately untranslated, unlike the rest of `squid.bot`: it is owner-only, and most of what
it prints is Python identifiers, state field names and planner event codes, which no
catalogue would improve.
"""

import dataclasses
import json
import pprint
from collections.abc import Hashable, Iterable, Sequence
from datetime import datetime
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Any

import discord

import squid_ui as sl

# The package this module belongs to, imported by name so the devtools panel reads the way
# a host would write it. Safe despite `__init__` importing this module: every use below is
# either a deferred annotation or inside a function body, so nothing resolves at import.
import squid_ui_discord
from squid_reactivity.actions import ActionLedger
from squid_ui.profiling import RuntimeSnapshot, RuntimeTrace
from squid_ui.runtime.topics import BusSnapshot
from squid_ui_discord.devtools_runtime import DevToolsAction, DevToolsRuntime, DurableRecordInspection

if TYPE_CHECKING:
    # Annotations only; see the note in operations.py about the `durable` extra.
    from squid_ui_discord.durability import DurableRuntimeSnapshot, RecoveryReport


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
    """The message roots, and one of them opened.

    Reads `squid_ui_discord.message_roots()` on every render rather than holding a list, so a panel left
    open keeps telling the truth: sessions that finished while it was open are simply gone
    from the next render.
    """

    focus: str | None = sl.state(None)
    revision: int = sl.state(0)
    """Bumped by Refresh. A handler that changes nothing leaves the message root clean and the
    message stale, so re-reading the world has to be a state change like any other."""

    own_id: str | None = sl.state(None)
    """This panel's own message root id, set by the cog once the message root exists — it is in the list
    like everything else, and unlabelled it reads as a mystery session."""

    def __init__(self, *, focus: str | None = None, manager: squid_ui_discord.SessionManager | None = None) -> None:
        self.focus = focus
        self._manager = manager

    def render(self) -> Sequence[sl.LayoutNode]:
        # Both are explicit invalidation tokens for process state that is not itself
        # reactive. Observe them even when an empty manager makes their usual branches
        # unreachable, otherwise a cached empty list cannot be refreshed.
        _ = self.revision, self.own_id
        message_roots = squid_ui_discord.message_roots()
        if self.focus is not None:
            target = squid_ui_discord.live.find(self.focus)
            if target is not None:
                return self._detail(target.snapshot(), target)
        return self._list(message_roots)

    # --- List ---------------------------------------------------------------------------

    def _list(self, message_roots: Sequence[squid_ui_discord.MessageRoot]) -> Sequence[sl.LayoutNode]:
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
            nodes.insert(0, sl.status(f"MessageRoot `{self.focus}` is no longer live.", tone=sl.Tone.WARNING))
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

    def _row(self, snapshot: squid_ui_discord.message_root.MessageRootSnapshot) -> str:
        mine = " *(this panel)*" if snapshot.id == self.own_id else ""
        key = self._session_key(snapshot.id)
        session = "" if key is None else f" · session `{key!r}`"
        location = f" · [jump]({snapshot.address.jump_url})" if snapshot.address is not None else ""
        return (
            f"`{snapshot.id}` **{snapshot.component.rsplit('.', 1)[-1]}**{mine}\n"
            f"gen {snapshot.generation} · {_flags(snapshot)} · age {_duration(snapshot.age)}"
            f" · idle {_duration(snapshot.idle)} · {_expiry(snapshot)}{session}{location}"
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
        self, snapshot: squid_ui_discord.message_root.MessageRootSnapshot, message_root: squid_ui_discord.MessageRoot
    ) -> Sequence[sl.LayoutNode]:
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
                sl.code(_dump(_exported_state(message_root))),
            )
        )
        children.append(
            sl.section(
                sl.heading("Reactivity"),
                sl.note("cell versions and what each computed last read"),
                sl.code(_dump_lines(_reactivity(message_root))),
            )
        )
        children.append(
            sl.section(
                sl.heading("Presentation"),
                sl.note("cursors, selections, disclosures, strategies"),
                sl.code(_dump(_presentation(message_root.presentation))),
            )
        )
        return [
            sl.section(sl.heading(f"MessageRoot {snapshot.id}"), *children),
            self._controls(back=True),
        ]

    def _plan_section(self, snapshot: squid_ui_discord.message_root.MessageRootSnapshot) -> Iterable[sl.LayoutNode]:
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
        await event.finish()


class OperationalInspector(sl.Component):
    """The process-wide operational dashboard for the development owner."""

    section: str = sl.state("overview")
    message_root_id: str | None = sl.state(None)
    session_id: str | None = sl.state(None)
    revision: int = sl.state(0)
    notice: str | None = sl.state(None)
    confirming_session: str | None = sl.state(None)
    confirming_action: str | None = sl.state(None)
    selected_records: tuple[str, ...] = sl.state(())

    def __init__(
        self,
        runtime: DevToolsRuntime,
        *,
        client: discord.Client | None = None,
        action_ledger: ActionLedger | None = None,
    ) -> None:
        self._devtools_runtime = runtime
        self._client = client
        self._action_ledger = action_ledger

    @sl.resource(pending=sl.resources.PendingMode.EXPLICIT)
    async def persistence_records(self) -> tuple[DurableRecordInspection, ...]:
        """Load persisted record metadata while the persistence section exists."""
        return await self._devtools_runtime.records()

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
        if self.notice is None:
            return nodes
        return (sl.status(self.notice, tone=sl.Tone.INFO), *nodes)

    def _overview(self, snapshot: squid_ui_discord.devtools_runtime.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        counts = [
            f"roots        {len(snapshot.message_roots)}",
            f"sessions     {len(snapshot.sessions)}",
            f"scheduler     {_scheduler_summary(snapshot.scheduler)}",
            f"topics      {_topic_summary(snapshot.topics)}",
            f"profile     {_profile_summary(snapshot.profiler)}",
            f"persistence {_durable_summary(snapshot.durable)}",
        ]
        return [
            sl.section(
                sl.heading("Operational dashboard"),
                sl.code("\n".join(counts)),
                sl.download(
                    "Download snapshot",
                    operational_attachment(snapshot),
                    key="operational-snapshot",
                    description="Bounded JSON diagnostics from this refresh",
                ),
            ),
            self._section_choices(),
            self._controls(),
        ]

    def _roots(self, snapshot: squid_ui_discord.devtools_runtime.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        if self.message_root_id is not None:
            message_root = squid_ui_discord.live.find(self.message_root_id)
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
                            *_summary(detail.snapshot),
                            f"**Middleware**\n{_dump(detail.middleware)}",
                            f"**Observed**\n{_dump(detail.observed)}",
                            f"**Followed**\n{_dump(detail.followed)}",
                            f"**History**\n{histories}",
                            key="root_detail",
                        ),
                    ),
                ]
                children.extend(
                    (
                        sl.section(sl.heading("Plan"), sl.code(plan_text(detail.snapshot))),
                        sl.section(
                            sl.heading("Component state"),
                            sl.code(_dump(_exported_state(message_root))),
                        ),
                        sl.section(sl.heading("Reactivity"), sl.code(_dump_lines(_reactivity(message_root)))),
                        sl.section(
                            sl.heading("Presentation"),
                            sl.code(_dump(_presentation(message_root.presentation))),
                        ),
                    )
                )
                asset = scene_attachment(detail.snapshot)
                if asset is not None:
                    children.append(
                        sl.download(
                            "Download scene",
                            asset,
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
            notice = f"MessageRoot `{self.message_root_id}` is no longer live."
        else:
            notice = None

        rows = [
            f"`{message_root.id}` **{message_root.component.rsplit('.', 1)[-1]}** · gen {message_root.generation} · {_flags(message_root)}"
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

    def _sessions(self, snapshot: squid_ui_discord.devtools_runtime.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        if self.session_id is not None:
            session = next((item for item in snapshot.sessions if item.id == self.session_id), None)
            if session is not None:
                nodes: list[sl.LayoutNode] = [
                    sl.section(
                        sl.heading(f"Session {session.id}"),
                        sl.bullets(
                            f"**Id**\n`{session.id}`",
                            f"**Key**\n`{session.key}`",
                            f"**Actor**\n{session.actor_id if session.actor_id is not None else 'none'}",
                            f"**Members**\n{_members(session)}",
                            f"**Mounts**\n{_dump(session.message_roots)}",
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
                    nodes.append(confirmation)
                return (*nodes, self._controls(back=True))
            notice = f"Session `{self.session_id}` is no longer live."
        else:
            notice = None

        rows = [
            f"`{session.id}` · key `{session.key}` · roots={len(session.message_roots)} · members={_members(session)}"
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

    def _queues(self, snapshot: squid_ui_discord.devtools_runtime.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
        lines = [
            f"scheduler  {_scheduler_summary(snapshot.scheduler)}",
            f"topics   {_topic_summary(snapshot.topics)}",
        ]
        if snapshot.topics is not None:
            lines.extend(
                f"  {topic.topic} subscribers={topic.subscribers} queued={topic.queued} in_flight={topic.in_flight}"
                for topic in snapshot.topics.topics
            )
        nodes: list[sl.LayoutNode] = [sl.section(sl.heading("Queues and subscribers"), sl.code("\n".join(lines)))]
        if self._devtools_runtime.policy.permits(DevToolsAction.WAIT_IDLE):
            nodes.append(
                sl.action_controls(sl.action_control("Wait for idle", self._wait_idle, key="wait-idle"), key="queue-actions")
            )
        return (*nodes, self._controls(back=True))

    def _profile(self, snapshot: squid_ui_discord.devtools_runtime.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
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
                sl.code("\n".join(_trace_summary(trace) for trace in traces) or "No retained traces."),
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

    def _persistence(self, snapshot: squid_ui_discord.devtools_runtime.OperationalSnapshot) -> Sequence[sl.LayoutNode]:
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
                    f"recovery {_recovery_summary(durable.last_recovery)}",
                )
            )
            record_state = self.persistence_records.status
            previous = record_state.previous if isinstance(record_state, sl.resources.Pending | sl.resources.Failed) else None
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
                            *(sl.choice(record.key, key=record.key, description=record.scope) for record in records[:25]),
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
            from squid_ui_discord.routing import routers

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

    def _activity(
        self, snapshot: squid_ui_discord.devtools_runtime.OperationalSnapshot
    ) -> Sequence[sl.LayoutNode]:
        events = () if self._action_ledger is None else self._action_ledger.events[-20:]
        event_text = "No retained causal events." if not events else "\n".join(_event_summary(event) for event in events)
        retained = (
            *snapshot.profiler.recent,
            *snapshot.profiler.slow,
            *snapshot.profiler.failed,
            *snapshot.profiler.deadline_misses,
        )
        dispatches = sorted(
            {
                trace.trace_id: trace
                for trace in retained
                if trace.operation.value in {"dispatch", "route_dispatch"}
            }.values(),
            key=lambda trace: trace.started,
        )[-20:]
        timeline = "No retained dispatches." if not dispatches else "\n".join(
            _trace_summary(trace) for trace in dispatches
        )
        return (
            sl.section(sl.heading("Action results"), sl.code(event_text)),
            sl.section(sl.heading("Dispatch timeline"), sl.code(timeline)),
            self._controls(back=True),
        )

    def _section_choices(self) -> sl.LayoutNode:
        return sl.choices(
            sl.choice("Roots", key="roots"),
            sl.choice("Sessions", key="sessions"),
            sl.choice("Routes", key="routes"),
            sl.choice("Activity", key="activity"),
            sl.choice("Queues", key="queues"),
            sl.choice("Profiler", key="profile"),
            sl.choice("Persistence", key="persistence"),
            key="section",
            selection=sl.controlled((), self._select_section),
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

    async def _refresh_selected_root(self, event: sl.ActionEvent) -> None:
        del event
        if self.message_root_id is None:
            return
        try:
            result = await self._devtools_runtime.refresh_root(self.message_root_id)
            self.notice = result.detail
            self.revision += 1
        except Exception as error:
            self.notice = f"Refresh failed: {type(error).__name__}: {error}"

    async def _wait_idle(self, event: sl.ActionEvent) -> None:
        del event
        try:
            result = await self._devtools_runtime.wait_idle()
            self.notice = result.detail
            self.revision += 1
        except Exception as error:
            self.notice = f"Wait failed: {type(error).__name__}: {error}"

    async def _clear_profile(self, event: sl.ActionEvent) -> None:
        del event
        try:
            result = self._devtools_runtime.clear_profile()
            self.notice = result.detail
            self.revision += 1
        except Exception as error:
            self.notice = f"Clear failed: {type(error).__name__}: {error}"

    async def _flush_persistence(self, event: sl.ActionEvent) -> None:
        del event
        try:
            result = await self._devtools_runtime.flush_persistence()
            self.notice = result.detail
            self.revision += 1
        except Exception as error:
            self.notice = f"Flush failed: {type(error).__name__}: {error}"

    async def _request_recovery(self, event: sl.ActionEvent) -> None:
        del event
        self.confirming_action = DevToolsAction.RECOVER_PERSISTENCE

    async def _cancel_action(self, event: sl.ActionEvent) -> None:
        del event
        self.confirming_action = None

    async def _confirm_recovery(self, event: sl.ActionEvent) -> None:
        del event
        try:
            result = await self._devtools_runtime.recover_persistence(confirmed=True)
            self.notice = result.detail
            self.revision += 1
        except Exception as error:
            self.notice = f"Recovery failed: {type(error).__name__}: {error}"
        finally:
            self.confirming_action = None

    async def _request_purge(self, event: sl.ActionEvent) -> None:
        del event
        self.confirming_action = DevToolsAction.PURGE_PERSISTENCE

    async def _confirm_purge(self, event: sl.ActionEvent) -> None:
        del event
        try:
            results = await self._devtools_runtime.purge_persistence(self.selected_records, confirmed=True)
            deleted = sum(result.deleted for result in results)
            self.notice = f"Persistence purge completed; deleted={deleted}."
            self.selected_records = ()
            await self.persistence_records.reload()
            self.revision += 1
        except Exception as error:
            self.notice = f"Purge failed: {type(error).__name__}: {error}"
        finally:
            self.confirming_action = None

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
        await event.finish()


def scene_attachment(snapshot: squid_ui_discord.message_root.MessageRootSnapshot) -> sl.document.Asset | None:
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
    snapshot: squid_ui_discord.devtools_runtime.OperationalSnapshot,
) -> sl.document.Asset:
    """The bounded operational snapshot as an inline JSON download."""
    encoded = json.dumps(dataclasses.asdict(snapshot), default=_json_default, indent=2, sort_keys=True).encode()
    return sl.document.Asset(
        key="operational-snapshot",
        name="squid-operational-snapshot.json",
        media_type="application/json",
        source=sl.document.InlineAsset(encoded),
    )


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return repr(value)


def _trace_summary(trace: RuntimeTrace) -> str:
    duration_ms = trace.duration * 1000
    return f"{trace.operation.value}:{trace.name} {trace.result.status.value} {duration_ms:.1f}ms id={trace.trace_id}"


def _event_summary(event: object) -> str:
    action_id = getattr(event, "action_id", getattr(event, "execution_id", "unknown"))
    status = getattr(event, "status", type(event).__name__)
    return f"{action_id}: {status}"


def plan_text(snapshot: squid_ui_discord.message_root.MessageRootSnapshot) -> str:
    """Render the retained plan report, grouping adaptations by severity."""
    report = snapshot.report
    if report is None:
        return "Nothing has been committed yet, so this message root has no plan report."
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


def metrics_text(snapshot: squid_ui_discord.message_root.MessageRootSnapshot) -> str:
    """Render the planner work and cache disposition retained by a message root."""
    metrics = snapshot.metrics
    if metrics is None:
        return "Nothing has been committed yet, so this message root has no planner metrics."
    return "\n".join(
        (
            f"states_explored: {metrics.states_explored}",
            f"search_fallback: {metrics.search_fallback}",
            f"cache: {'hit' if metrics.cache_hit else 'miss'}",
        )
    )


def _summary(snapshot: squid_ui_discord.message_root.MessageRootSnapshot) -> list[str]:
    entries = [
        f"**Component**\n`{snapshot.component}`",
        f"**Generation**\n{snapshot.generation} · {_flags(snapshot)} · {snapshot.suppressed} suppressed",
        f"**Timing**\nage {_duration(snapshot.age)} · idle {_duration(snapshot.idle)} · {_expiry(snapshot)}",
        f"**Access**\n{_access_text(snapshot.access)}",
    ]
    address = snapshot.address
    if address is None:
        # A message root sent through an unwaited interaction response has never seen its message.
        entries.append("**Message**\nnot located yet — nobody has clicked it")
    else:
        where = "ephemeral" if address.ephemeral else f"<#{address.channel_id}>"
        entries.append(f"**Message**\n[{address.message_id}]({address.jump_url}) in {where}")
    return entries


def _access_text(access: squid_ui_discord.AccessPolicy) -> str:
    """Describe the message root's admission policy without exposing callback internals."""
    if isinstance(access, squid_ui_discord.Everyone):
        return "Everyone"
    if isinstance(access, squid_ui_discord.Owner):
        return f"Owner (<@{access.user_id}>)"
    if isinstance(access, squid_ui_discord.Users):
        users = ", ".join(f"<@{user_id}>" for user_id in sorted(access.user_ids))
        return f"Users ({users})"
    if isinstance(access, squid_ui_discord.access.Check):
        return "Check"
    return type(access).__name__


def _option_description(snapshot: squid_ui_discord.message_root.MessageRootSnapshot) -> str:
    return f"gen {snapshot.generation} · idle {_duration(snapshot.idle)} · {_expiry(snapshot)}"


def _flags(snapshot: squid_ui_discord.message_root.MessageRootSnapshot) -> str:
    flags = []
    if snapshot.lifecycle is squid_ui_discord.message_root.MessageRootStatus.RENEWAL_ARMED:
        flags.append("renewal armed")
    if snapshot.pending:
        flags.append("dirty")
    if snapshot.finished:
        flags.append("finished")
    if snapshot.address is not None and snapshot.address.ephemeral:
        flags.append("ephemeral")
    return " ".join(flags) if flags else "clean"


def _expiry(snapshot: squid_ui_discord.message_root.MessageRootSnapshot) -> str:
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


def _scheduler_summary(snapshot: squid_ui_discord.MessageRootSchedulerSnapshot | None) -> str:
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


def _exported_state(message_root: squid_ui_discord.MessageRoot) -> dict[str, object]:
    """Declared persistent state for every component in the committed tree, by path.

    Each component is exported on its own, so one field whose value refuses to be formatted
    does not cost the whole dump.
    """
    exported: dict[str, object] = {}
    for path, component in message_root.runtime.components.items():
        try:
            exported[path] = sl.runtime.export_state(component)
        except Exception as error:
            exported[path] = f"<unreadable: {type(error).__name__}: {error}>"
    return exported


def _reactivity(message_root: squid_ui_discord.MessageRoot) -> list[str]:
    """Every cell's version and every computed's current source set, resolved to names.

    Names are resolved across the whole tree rather than per component, because a computed
    may read another component's state and printing an identity would say nothing.
    """
    components = message_root.runtime.components
    # Deduplicated by identity: several panels usually hold the very same namespace, and
    # that is the point of one.
    namespaces = {
        id(owner): owner for owner, _ in (_pair(topic) for topic in message_root.observed) if owner is not None
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
        lines.append(f"{namespace!r} (shared)")
        for name, cell in sl.runtime.inspect_cells(namespace).items():
            marks = " ".join(mark for mark, on in (("opaque", cell.opaque), ("default", not cell.assigned)) if on)
            lines.append(f"  {name} v{cell.version}{f'  [{marks}]' if marks else ''}")
    return lines


def _pair(topic: object) -> tuple[sl.runtime.SharedState[Any] | None, object]:
    """An observed address split into its namespace and cell, or `(None, topic)` otherwise.

    Read from what the render observed rather than from what it subscribed to, so a message root
    with no scheduler still reports the shared state it is showing.
    """
    match topic:
        case (sl.runtime.SharedState() as owner, descriptor):
            return owner, descriptor
        case _:
            return None, topic


def _dump_lines(lines: list[str]) -> str:
    return "\n".join(lines) or "(no components)"


def _presentation(session: sl.runtime.PresentationState) -> dict[str, object]:
    return {
        "cursors": dict(session.cursors),
        "selections": dict(session.selections),
        "disclosures": dict(session.disclosures),
        "strategies": dict(session.strategies),
    }


def _members(session: squid_ui_discord.devtools_runtime.SessionInspection) -> str:
    """Membership as `used/limit`, or a bare count when the session is unbounded."""
    count = len(session.members)
    return f"{count}" if session.capacity is None else f"{count}/{session.capacity}"


def _dump(value: object) -> str:
    """Whatever this is, as something readable. `repr` is the floor, not the ideal."""
    return pprint.pformat(value, width=88, sort_dicts=True) or "{}"
