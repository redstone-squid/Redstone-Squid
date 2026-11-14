"""Inspect unresolved record results and request category lookups or rebuilds.

Diagnostics identify a TitleDefinition by its integer definition ID. That rules_revision-specific
definition is distinct from the stable RecordTitle record_title UUID used by record
subscriptions. Lookup accepts a definition ID or an exact category; the record
service persists the requested category and recomputes its standings.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Literal, Protocol, override

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.lookup import pick
from squid.bot.ui import tr, truncate_display_text
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD
from squid.records.application import RebuildSummary, RecordGap, RecordLookupRequest, TitleDiagnosticGap
from squid.records.domain import BuildKind
from squid.suggestions.domain import Suggestion
from squid_i18n import md_tr

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


class RecordOperations(Protocol):
    """Record diagnostics and exact lookup operations."""

    async def gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]: ...

    async def title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[TitleDiagnosticGap]: ...

    async def materialize_definition(
        self,
        definition_id: int,
        *,
        kind: BuildKind,
        version_id: int | None = None,
    ) -> RebuildSummary: ...

    async def lookup_or_materialize(self, request: RecordLookupRequest) -> RebuildSummary: ...


class RecordComputationOperations(Protocol):
    """Record rebuild operation exposed by the maintenance tab."""

    async def rebuild(
        self,
        *,
        current_version_id: int | None,
        kinds: Sequence[BuildKind],
    ) -> RebuildSummary: ...


type RecordAuthorizer = Callable[[PermissionNode], Awaitable[bool]]


class RecordsScreen(sd.Screen):
    """Show record diagnostics and maintenance controls for one guild session.

    Initial capabilities choose which tabs and forms are visible. Each maintenance
    action calls authorize again before invoking the record service.
    """

    policy = sd.ResponseSpec(
        nav=sd.navigation_controls.page_jump_nav,
        timeout=300,
        session=sd.SessionSpec("records", scope=sd.ScopeKind.USER_GUILD),
    )

    _version: Suggestion | None = sl.state(None, persist=False)
    _restrictions: tuple[Suggestion, ...] = sl.state(())
    _gaps: sp.Browser[RecordGap, sl.ComponentsV2Target, sd.Request] | None = sl.state(None, opaque=True)
    _titles: sp.Browser[TitleDiagnosticGap, sl.ComponentsV2Target, sd.Request] | None = sl.state(None, opaque=True)
    _tabs: sp.ComponentDriver[sp.TabsState, sl.ComponentsV2Target, sd.Request] | None = sl.state(None, opaque=True)

    def __init__(
        self,
        records: RecordOperations,
        computation: RecordComputationOperations,
        *,
        can_inspect: bool,
        can_rebuild: bool,
        authorize: RecordAuthorizer,
    ) -> None:
        self._records = records
        self._computation = computation
        self._can_inspect = can_inspect
        self._can_rebuild = can_rebuild
        self._authorize = authorize

    @override
    async def on_load(self) -> None:
        if self._can_inspect:
            await self._refresh_diagnostics()
        self._build_tabs()

    async def _refresh_diagnostics(self) -> None:
        gaps = tuple(await self._records.gaps())
        titles = tuple(await self._records.title_gaps())
        self._gaps = sp.Browser(
            sl.sources.list_source(gaps),
            key="evidence-gaps",
            identity=lambda gap: str(gap.definition_id),
            label=lambda gap: truncate_display_text(gap.title, 80),
            summary=lambda gap: f"{gap.title} · missing {', '.join(gap.fields)}",
            detail=lambda gap: sl.fields(
                sl.field(tr(t"bot-submission-records-definition = Definition"), str(gap.definition_id)),
                sl.field(tr(t"bot-submission-records-builds = Builds"), ", ".join(map(str, gap.build_ids))),
                sl.field(tr(t"bot-submission-records-missing-evidence = Missing evidence"), ", ".join(gap.fields)),
            ),
            page_size=15,
            title=tr(t"bot-submission-records-record-evidence-gaps = Record evidence gaps"),
            empty=tr(t"records-no-unresolved-categories = No unresolved active record categories."),
        )
        self._titles = sp.Browser(
            sl.sources.list_source(titles),
            key="title-issues",
            identity=lambda gap: str(gap.definition_id),
            label=lambda gap: truncate_display_text(gap.title, 80),
            summary=lambda gap: gap.title,
            detail=lambda gap: sl.fields(
                sl.field(tr(t"bot-submission-records-definition = Definition"), str(gap.definition_id)),
                sl.field(
                    tr(t"bot-submission-records-diagnostics = Diagnostics"),
                    ", ".join(str(item.get("code", "unknown")) for item in gap.diagnostics),
                ),
            ),
            page_size=15,
            title=tr(t"bot-submission-records-record-title-diagnostics = Record title diagnostics"),
            empty=tr(t"records-no-titles-need-review = No active record titles require taxonomy review."),
        )

    def _build_tabs(self) -> None:
        tabs: list[sp.Tab[sl.ComponentsV2Target, sd.Request]] = []
        if self._gaps is not None and self._titles is not None:
            tabs.extend(
                (
                    sp.Tab("gaps", tr(t"bot-submission-records-evidence-gaps = Evidence gaps"), self._gaps),
                    sp.Tab("titles", tr(t"bot-submission-records-title-issues = Title issues"), self._titles),
                )
            )
        tabs.append(
            sp.Tab(
                "maintenance",
                tr(t"bot-submission-records-lookup-and-rebuild = Lookup and rebuild"),
                self._maintenance_nodes(),
            )
        )
        self._tabs = sp.Tabs(
            tabs,
            key="records-tabs",
            title=tr(t"bot-submission-records-records = Records"),
            initial_state=None if self._tabs is None else self._tabs.machine_state,
        )

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self._tabs is None:
            return (sl.status(tr(t"records-diagnostics-loading = Loading record diagnostics.")),)
        return (
            sl.note(self._version.label)
            if self._version
            else sl.note(tr(t"records-active-version = Using the active version")),
            sl.action_controls(
                sl.action_control(
                    tr(t"records-choose-version = Choose version"), sd.on_press(self._choose_version), key="version"
                ),
                sl.action_control(
                    tr(t"records-reset-version = Use active version"),
                    sd.on_press(self._reset_version),
                    key="active-version",
                ),
                key="version-actions",
            ),
            self._tabs,
        )

    def _maintenance_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if not (self._can_inspect or self._can_rebuild):
            return (sl.note(tr(t"records-no-maintenance-actions = No maintenance actions are available.")),)
        return (
            sl.action_controls(
                self._can_inspect
                and sl.action_control(
                    tr(t"records-choose-definition = Find definition by name"),
                    sd.on_press(self._choose_definition),
                    key="definition",
                ),
                self._can_inspect
                and sl.action_control(
                    tr(t"records-choose-restriction = Add category restriction"),
                    sd.on_press(self._choose_restriction),
                    key="restriction",
                ),
                self._can_inspect
                and sl.form(
                    tr(t"records-advanced-category = Advanced exact category"),
                    self._lookup_form(),
                    key="lookup",
                    on_submit=self._lookup,
                ),
                self._can_rebuild
                and sl.form(
                    tr(t"bot-submission-records-rebuild-records = Rebuild records"),
                    self._rebuild_form(),
                    key="rebuild",
                    on_submit=self._rebuild,
                ),
                key="maintenance-forms",
            ),
            *(
                sl.action_controls(
                    sl.action_control(
                        item.label, partial(self._remove_restriction, item.value), key=f"restriction-{item.value}"
                    ),
                    key=f"selected-{item.value}",
                )
                for item in self._restrictions
            ),
            sl.note(
                tr(
                    t"records-restriction-removal = Selected restrictions apply to exact category requests. Press one to remove it."
                )
            ),
        )

    @staticmethod
    def _kind_field[RequiredT: (Literal[True], Literal[False]) = Literal[True]](
        *, required: RequiredT = True
    ) -> sl.forms.ChoiceField[BuildKind, RequiredT]:
        return sl.forms.ChoiceField(
            key="kind",
            label=tr(t"bot-submission-records-build-kind = Build kind"),
            required=required,
            options=tuple(sl.forms.ChoiceOption(kind.value, kind.value.title(), kind) for kind in BuildKind),
        )

    @classmethod
    def _lookup_form(cls) -> sl.forms.FormSpec:
        form = _LookupForm().spec()
        return replace(
            form,
            items=tuple(
                item
                for item in form.items
                if not isinstance(item, sl.forms.FormField) or item.key not in {"restrictions", "version_id"}
            ),
        )

    @classmethod
    def _rebuild_form(cls) -> sl.forms.FormSpec:
        form = _RebuildForm().spec()
        return replace(
            form,
            items=tuple(
                item for item in form.items if not isinstance(item, sl.forms.FormField) or item.key != "version_id"
            ),
        )

    async def _choose_version(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await pick(event, "version_ids", tr(t"records-choose-version = Choose version"), self._version_picked)

    async def _version_picked(self, _event: sd.ActionEvent, item: Suggestion) -> None:
        self._version = item

    async def _reset_version(self, _event: sd.PressEvent) -> None:
        self._version = None

    async def _choose_restriction(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await pick(
            event,
            "restriction_ids",
            tr(t"records-choose-restriction = Add category restriction"),
            self._restriction_picked,
        )

    async def _restriction_picked(self, _event: sd.ActionEvent, item: Suggestion) -> None:
        self._restrictions = (*(current for current in self._restrictions if current.value != item.value), item)
        self._build_tabs()

    async def _remove_restriction(self, value: str, _event: sd.PressEvent) -> None:
        self._restrictions = tuple(item for item in self._restrictions if item.value != value)
        self._build_tabs()

    async def _choose_definition(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        version = int(self._version.value) if self._version else None

        async def selected(source: sd.ActionEvent, item: Suggestion) -> None:
            if not await self._may(source, RECORD_ENTRY_INSPECT):
                return
            assert item.description is not None
            summary = await self._records.materialize_definition(
                int(item.value), kind=BuildKind(item.description), version_id=version
            )
            await self._refresh_diagnostics()
            self._build_tabs()
            definitions, resolved = summary.definitions, summary.resolved
            await source.notice(
                md_tr(t"records-recompute-summary = Recomputed {definitions} definitions; {resolved} resolved.")
            )

        await pick(
            event,
            "title_definitions",
            tr(t"records-choose-definition = Find definition by name"),
            selected,
            confirm=tr(t"records-recompute-definition = Recompute definition"),
        )

    async def _lookup(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, RECORD_ENTRY_INSPECT):
            return
        form = _LookupForm.from_submission(event)
        kind = form.kind
        selected = form.base_key.strip()
        version_id = (
            form.version_id if form.version_id is not None else (int(self._version.value) if self._version else None)
        )
        raw_restrictions = form.restrictions or ""
        try:
            restrictions = frozenset(
                int(value.strip()) for value in raw_restrictions.split(",") if value.strip()
            ) | frozenset(int(item.value) for item in self._restrictions)
        except ValueError:
            await event.notice(
                tr(t"records-restriction-ids-invalid = Restriction IDs must be whole numbers separated by commas.")
            )
            return
        if selected.isdigit():
            if restrictions:
                await event.notice(
                    tr(
                        t"records-restriction-base-key-required = Restrictions can "
                        t"only be combined with a hand-typed base key."
                    )
                )
                return
            summary = await self._records.materialize_definition(int(selected), kind=kind, version_id=version_id)
        else:
            summary = await self._records.lookup_or_materialize(
                RecordLookupRequest(kind, selected, restrictions, version_id=version_id)
            )
        await self._refresh_diagnostics()
        self._build_tabs()
        definitions = summary.definitions
        resolved = summary.resolved
        await event.notice(
            md_tr(
                t"records-recompute-summary = Recomputed {definitions} definitions; {resolved} resolved.",
            )
        )

    async def _rebuild(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, RECORD_ENTRY_REBUILD):
            return
        form = _RebuildForm.from_submission(event)
        kind = form.kind
        version_id = (
            form.version_id if form.version_id is not None else (int(self._version.value) if self._version else None)
        )
        kinds = (kind,) if kind is not None else (BuildKind.DOOR, BuildKind.EXTENDER)
        summary = await self._computation.rebuild(current_version_id=version_id, kinds=kinds)
        if self._can_inspect:
            await self._refresh_diagnostics()
            self._build_tabs()
        definitions = summary.definitions
        resolved = summary.resolved
        unresolved = summary.unresolved
        await event.notice(
            md_tr(
                t"records-rebuild-summary = Rebuilt {definitions} "
                t"definitions; {resolved} resolved; {unresolved} awaiting evidence.",
            )
        )

    async def _may(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(
            tr(t"records-operation-permission-denied = You are no longer allowed to perform this records operation.")
        )
        return False


class _LookupForm(sl.forms.Form):
    title = tr(t"bot-submission-records-lookup-record-category = Lookup record category")

    kind = RecordsScreen._kind_field()
    base_key = sl.forms.TextField(label=tr(t"records-exact-category-key = Exact category key (advanced)"), maximum=300)
    restrictions = sl.forms.TextField(
        label=tr(t"records-restriction-ids-help = Restriction IDs, comma separated"),
        required=False,
        maximum=300,
    )
    version_id = sl.forms.IntField(
        label=tr(t"bot-submission-records-pinned-version-id = Pinned version ID"), required=False, minimum=1
    )


class _RebuildForm(sl.forms.Form):
    title = tr(t"bot-submission-records-rebuild-records = Rebuild records")

    kind = RecordsScreen._kind_field(required=False)
    version_id = sl.forms.IntField(
        label=tr(t"bot-submission-records-current-version-id = Current version ID"), required=False, minimum=1
    )
