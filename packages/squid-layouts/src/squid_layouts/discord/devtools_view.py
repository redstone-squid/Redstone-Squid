"""The `/dev ui` inspector: every live mount, and why one of them is odd.

squid-layouts has excellent *planning* diagnostics — reports, fingerprints, plan metrics —
and every one of them describes a render that already happened. Nothing answered "show me
the UI sessions this process is holding right now". `sl.discord.mounts()` is that list, and
this component is the reading of it.

Deliberately untranslated, unlike the rest of `squid.bot`: it is owner-only, and most of what
it prints is Python identifiers, state field names and planner event codes, which no
catalogue would improve.
"""

import pprint
from collections.abc import Hashable, Iterable, Sequence

import squid_layouts as sl

SESSION_SECONDS = 300
"""Short-lived on purpose: an inspector left open is one more mount in its own list."""

_SELECT_LIMIT = 25
"""Discord's option cap. The list itself pages; the picker offers the newest of them."""


class MountInspector(sl.Component):
    """The live mounts, and one of them opened.

    Reads `sl.discord.mounts()` on every render rather than holding a list, so a panel left
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

    def __init__(self, *, focus: str | None = None, registry: sl.discord.MountRegistry | None = None) -> None:
        self.focus = focus
        self._registry = registry

    def render(self) -> Sequence[sl.LayoutNode]:
        mounts = sl.discord.mounts()
        if self.focus is not None:
            target = sl.discord.live.find(self.focus)
            if target is not None:
                return self._detail(target.snapshot(), target)
        return self._list(mounts)

    # --- List ---------------------------------------------------------------------------

    def _list(self, mounts: Sequence[sl.discord.Mount]) -> Sequence[sl.LayoutNode]:
        # Newest first: the session someone is asking about is almost always the one they
        # just opened.
        snapshots = sorted((mount.snapshot() for mount in mounts), key=lambda snapshot: snapshot.age)
        missing = self.focus is not None
        body: sl.LayoutNode
        if snapshots:
            body = sl.bullets(*(self._row(snapshot) for snapshot in snapshots), key="mounts", page_size=8)
        else:
            body = sl.paragraph("Nothing is mounted. Open a panel and run this again.")

        nodes: list[sl.LayoutNode] = [sl.section(body, heading=f"Live mounts — {len(snapshots)}")]
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

    def _row(self, snapshot: sl.discord.MountSnapshot) -> str:
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
        return next((key for key, mount in self._registry.active() if mount.id == mount_id), None)

    # --- Detail -------------------------------------------------------------------------

    def _detail(self, snapshot: sl.discord.MountSnapshot, mount: sl.discord.Mount) -> Sequence[sl.LayoutNode]:
        children: list[sl.LayoutNode] = [sl.bullets(*_summary(snapshot), key="summary")]
        if snapshot.handler_keys:
            children.append(
                sl.section(
                    sl.note(f"generation {snapshot.generation}"),
                    sl.code("\n".join(snapshot.handler_keys)),
                    heading="Handlers",
                )
            )
        children.extend(self._plan_section(snapshot))
        children.append(
            sl.section(
                sl.note("persisted fields only"),
                sl.code(_dump(_exported_state(mount))),
                heading="Component state",
            )
        )
        children.append(
            sl.section(
                sl.note("cursors, selections, disclosures, strategies"),
                sl.code(_dump(_presentation(mount.presentation))),
                heading="Presentation",
            )
        )
        return [
            sl.section(*children, heading=f"Mount {snapshot.id}"),
            self._controls(back=True),
        ]

    def _plan_section(self, snapshot: sl.discord.MountSnapshot) -> Iterable[sl.LayoutNode]:
        if snapshot.report is None or snapshot.metrics is None:
            yield sl.section(sl.note("nothing has been committed yet"), heading="Plan")
            return
        metrics = snapshot.metrics
        yield sl.section(
            sl.note(
                f"{metrics.states_explored} states explored · "
                f"cache {'hit' if metrics.cache_hit else 'miss'}"
                f"{' · search fell back' if metrics.search_fallback else ''}"
            ),
            sl.code(plan_text(snapshot)),
            heading="Plan",
        )

    # --- Controls -----------------------------------------------------------------------

    def _controls(self, *, back: bool) -> sl.Actions:
        controls: list[sl.Action] = []
        if back:
            controls.append(sl.action("Back", self._back, key="back"))
        controls.extend(
            (
                sl.action("Refresh", self._refresh, key="refresh", emphasis=sl.Emphasis.SUBTLE),
                sl.action("Close", self._close, key="close", emphasis=sl.Emphasis.SUBTLE),
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


def scene_attachment(snapshot: sl.discord.MountSnapshot) -> sl.Asset | None:
    """The mount's committed scene as the protocol JSON, for reading outside Discord."""
    if snapshot.scene is None:
        return None
    return sl.Asset(
        key="scene",
        name=f"scene-{snapshot.id}-gen{snapshot.generation}.json",
        media_type="application/json",
        source=sl.InlineAsset(sl.scene.Codec.dumps(snapshot.scene).encode()),
    )


def plan_text(snapshot: sl.discord.MountSnapshot) -> str:
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


def metrics_text(snapshot: sl.discord.MountSnapshot) -> str:
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


def _summary(snapshot: sl.discord.MountSnapshot) -> list[str]:
    lock = "anyone" if snapshot.lock_to is None else ", ".join(f"<@{user}>" for user in sorted(snapshot.lock_to))
    entries = [
        f"**Component**\n`{snapshot.component}`",
        f"**Generation**\n{snapshot.generation} · {_flags(snapshot)}",
        f"**Timing**\nage {_duration(snapshot.age)} · idle {_duration(snapshot.idle)} · {_expiry(snapshot)}",
        f"**Locked to**\n{lock}",
    ]
    address = snapshot.address
    if address is None:
        # A mount sent through an unwaited interaction response has never seen its message.
        entries.append("**Message**\nnot located yet — nobody has clicked it")
    else:
        where = "ephemeral" if address.ephemeral else f"<#{address.channel_id}>"
        entries.append(f"**Message**\n[{address.message_id}]({address.jump_url}) in {where}")
    return entries


def _option_description(snapshot: sl.discord.MountSnapshot) -> str:
    return f"gen {snapshot.generation} · idle {_duration(snapshot.idle)} · {_expiry(snapshot)}"


def _flags(snapshot: sl.discord.MountSnapshot) -> str:
    flags = []
    if snapshot.pending:
        flags.append("dirty")
    if snapshot.finished:
        flags.append("finished")
    if snapshot.address is not None and snapshot.address.ephemeral:
        flags.append("ephemeral")
    return " ".join(flags) if flags else "clean"


def _expiry(snapshot: sl.discord.MountSnapshot) -> str:
    if snapshot.expires_in is None:
        return "no timeout"
    return f"{_duration(snapshot.expires_in)} left"


def _duration(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{total % 3600 // 60:02d}m"


def _exported_state(mount: sl.discord.Mount) -> dict[str, object]:
    """Declared persistent state for every component in the committed tree, by path.

    Each component is exported on its own: `export_state` deep-copies, and one field holding
    something that refuses to be copied must not cost the whole dump.
    """
    exported: dict[str, object] = {}
    for path, component in mount.runtime.components.items():
        try:
            exported[path] = sl.runtime.export_state(component)
        except Exception as error:
            exported[path] = f"<unreadable: {type(error).__name__}: {error}>"
    return exported


def _presentation(session: sl.runtime.PresentationSession) -> dict[str, object]:
    return {
        "cursors": dict(session.cursors),
        "selections": dict(session.selections),
        "disclosures": dict(session.disclosures),
        "strategies": dict(session.strategies),
    }


def _dump(value: object) -> str:
    """Whatever this is, as something readable. `repr` is the floor, not the ideal."""
    return pprint.pformat(value, width=88, sort_dicts=True) or "{}"
