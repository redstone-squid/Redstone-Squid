"""Inspect unresolved record results and request category lookups or rebuilds.

Diagnostics identify a TitleDefinition by its integer definition ID. That rules_revision-specific
definition is distinct from the stable RecordTitle record_title UUID used by record
subscriptions. Lookup accepts a definition ID or an exact category; the record
service persists the requested category and recomputes its standings.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from functools import partial
from typing import Any, Literal, Protocol, override

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.lookup import pick
from squid.bot.ui import tr, truncate_display_text
from squid.bot.utils.permissions import Grants, requires_grant
from squid.core.permissions.domain.catalogue import RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD
from squid.core.records.application import RebuildSummary, RecordGap, RecordLookupRequest, TitleDiagnosticGap
from squid.core.records.domain import BuildKind
from squid.core.suggestions.domain import Suggestion
from squid_i18n import Text, md_tr
from squid_ui.ext.discord.target_types import ComponentsV2Target


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

    async def pin_current_version(self, version_id: int | None) -> None: ...

    async def rebuild(
        self,
        *,
        current_version_id: int | None,
        kinds: Sequence[BuildKind],
    ) -> RebuildSummary: ...


type BuildOpener = Callable[[int, sl.ActionEvent], Awaitable[None]]


class RecordsScreen(sd.Screen[None, Any, RedstoneSquid]):
    """Show record diagnostics and maintenance controls for one guild session.

    Three tabs cover the staff workflow. "Missing evidence" lists record categories
    that cannot be decided until their builds gain measurements, "Unrecognised titles"
    lists categories whose titles contain terms the title rules do not know, and
    "Recompute" recalculates standings once either has been fixed.

    The grants held at open choose which tabs and forms are visible. Each maintenance
    action rechecks its node before invoking the record service.
    """

    policy = sd.ResponseSpec(
        nav=sd.navigation_controls.page_jump_nav,
        timeout=300,
        session=sd.SessionSpec("records", scope=sd.ScopeKind.USER_GUILD),
    )

    _version: Suggestion | None = sl.state(None)
    _restrictions: tuple[Suggestion, ...] = sl.state(())
    _gaps: sp.Browser[RecordGap, ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | None = sl.state(
        None, opaque=True
    )
    _titles: sp.Browser[TitleDiagnosticGap, ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | None = sl.state(
        None, opaque=True
    )
    _tabs: sp.ComponentDriver[sp.TabsState, ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | None = sl.state(
        None, opaque=True
    )

    def __init__(
        self,
        records: RecordOperations,
        computation: RecordComputationOperations,
        *,
        grants: Grants,
    ) -> None:
        self._records = records
        self._computation = computation
        self.grants = grants
        self.open_build: BuildOpener | None = None
        """Opens a build from a gap's details; set by a host that can show builds."""

    @override
    async def on_load(self) -> None:
        if RECORD_ENTRY_INSPECT in self.grants:
            await self._refresh_diagnostics()
        self._build_tabs()

    async def _refresh_diagnostics(self) -> None:
        gaps = tuple(await self._records.gaps())
        titles = tuple(await self._records.title_gaps())
        self._gaps = sp.Browser(
            sl.sources.list_source(gaps),
            key="evidence-gaps",
            identity=lambda gap: str(gap.definition_id),
            label=lambda gap: truncate_display_text(_gap_title(gap), 80),
            summary=_gap_summary,
            detail=self._gap_detail,
            fetch_size=15,
            title=tr(t"bot-submission-records-record-evidence-gaps = Records missing evidence"),
            empty=tr(t"records-no-unresolved-categories = Every active record category has the evidence it needs."),
        )
        self._titles = sp.Browser(
            sl.sources.list_source(titles),
            key="title-issues",
            identity=lambda gap: str(gap.definition_id),
            label=lambda gap: truncate_display_text(gap.title, 80),
            summary=lambda gap: gap.title,
            detail=lambda gap: sl.section(
                sl.heading(gap.title),
                sl.paragraph(Text.join("\n", [_diagnostic_text(item) for item in gap.diagnostics])),
                sl.note(_category_note(gap.definition_id)),
                sl.action_controls(
                    sl.action_control(
                        tr(t"records-recompute-definition = Recompute definition"),
                        partial(self._recompute_diagnostic, gap.definition_id, gap.build_kind),
                        key=f"recompute-{gap.definition_id}",
                    ),
                    key="diagnostic-actions",
                ),
            ),
            fetch_size=15,
            title=tr(t"bot-submission-records-record-title-diagnostics = Unrecognised record titles"),
            empty=tr(t"records-no-titles-need-review = Every active record title uses known terms."),
        )

    def _gap_detail(self, gap: RecordGap) -> sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        needs = ", ".join(field.replace("_", " ") for field in gap.fields)
        return sl.section(
            sl.heading(_gap_title(gap)),
            sl.paragraph(tr(t"records-gap-needs = Needs {needs} on these builds:")),
            sl.action_controls(
                *(
                    sl.action_control(
                        tr(t"records-gap-open-build = Open build #{build_id!s}"),
                        partial(self.open_build, build_id),
                        key=f"gap-build-{build_id}",
                    )
                    for build_id in gap.build_ids[:5]
                ),
                key="gap-builds",
            )
            if self.open_build is not None and gap.build_ids
            else sl.paragraph(", ".join(f"#{build_id}" for build_id in gap.build_ids)),
            sl.note(_category_note(gap.definition_id)),
            sl.action_controls(
                sl.action_control(
                    tr(t"records-recompute-definition = Recompute definition"),
                    partial(self._recompute_diagnostic, gap.definition_id, gap.build_kind),
                    key=f"recompute-{gap.definition_id}",
                ),
                key="diagnostic-actions",
            ),
        )

    @requires_grant(RECORD_ENTRY_INSPECT)
    async def _recompute_diagnostic(self, definition_id: int, kind: BuildKind, event: sl.PressEvent) -> None:
        summary = await self._records.materialize_definition(
            definition_id, kind=kind, version_id=int(self._version.value) if self._version else None
        )
        await self._refresh_diagnostics()
        self._build_tabs()
        definitions, resolved = summary.definitions, summary.resolved
        await event.notice(
            md_tr(t"records-recompute-summary = Recomputed {definitions} definitions; {resolved} resolved.")
        )

    def _build_tabs(self) -> None:
        tabs: list[sp.Tab[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = []
        if self._gaps is not None and self._titles is not None:
            tabs.extend(
                (
                    sp.Tab(
                        "gaps",
                        tr(t"bot-submission-records-evidence-gaps = Missing evidence"),
                        (
                            sl.note(
                                tr(
                                    t"records-gaps-purpose = These record categories cannot be decided yet. Add "
                                    t"the missing values to their builds, then recompute."
                                )
                            ),
                            self._gaps,
                        ),
                    ),
                    sp.Tab(
                        "titles",
                        tr(t"bot-submission-records-title-issues = Unrecognised titles"),
                        (
                            sl.note(
                                tr(
                                    t"records-titles-purpose = These record titles contain terms the title rules "
                                    t"do not know. Fix the taxonomy, then rebuild records."
                                )
                            ),
                            self._titles,
                        ),
                    ),
                )
            )
        tabs.append(
            sp.Tab(
                "maintenance",
                tr(t"records-maintenance-tools = Recompute"),
                self._maintenance_nodes(),
            )
        )
        self._tabs = sp.Tabs(
            tabs,
            key="records-tabs",
            title=tr(t"bot-submission-records-records = Record tools"),
            initial_state=None if self._tabs is None else self._tabs.machine_state,
        )

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        if self._tabs is None:
            return (sl.status(tr(t"records-diagnostics-loading = Loading record diagnostics.")),)
        return (self._tabs,)

    def _maintenance_nodes(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        if not (RECORD_ENTRY_INSPECT in self.grants or RECORD_ENTRY_REBUILD in self.grants):
            return (sl.note(tr(t"records-no-maintenance-actions = No maintenance actions are available.")),)
        version = self._version.label if self._version else tr(t"records-active-version = the active version")
        return (
            sl.note(
                tr(
                    t"records-maintenance-purpose = Recalculate standings after fixing builds or title rules. "
                    t"These actions use the rules of {version}."
                )
            ),
            sl.action_controls(
                RECORD_ENTRY_INSPECT in self.grants
                and sl.action_control(
                    tr(t"records-choose-definition = Recompute a category"),
                    self._choose_definition,
                    key="definition",
                ),
                RECORD_ENTRY_REBUILD in self.grants
                and sl.form(
                    tr(t"bot-submission-records-rebuild-records = Rebuild all records"),
                    self._rebuild_form(),
                    key="rebuild",
                    on_submit=self._rebuild,
                ),
                sl.action_control(
                    tr(t"records-choose-version = Use another version"), self._choose_version, key="version"
                ),
                self._version is not None
                and sl.action_control(
                    tr(t"records-reset-version = Use active version"),
                    self._reset_version,
                    key="active-version",
                ),
                key="maintenance-forms",
                display=sl.semantic.ControlDisplay.INDIVIDUAL,
            ),
            *(
                (
                    sl.heading(tr(t"records-exact-heading = Category without a record")),
                    sl.note(
                        tr(
                            t"records-exact-purpose = Create and compute a record category by its exact key, "
                            t"optionally narrowed by restrictions."
                        )
                    ),
                    sl.action_controls(
                        sl.action_control(
                            tr(t"records-choose-restriction = Add restriction"),
                            self._choose_restriction,
                            key="restriction",
                        ),
                        sl.form(
                            tr(t"records-exact-category = Look up exact category"),
                            self._lookup_form(),
                            key="lookup",
                            on_submit=self._lookup,
                        ),
                        *(
                            sl.action_control(
                                _remove_label(item.label),
                                partial(self._remove_restriction, item.value),
                                key=f"restriction-{item.value}",
                            )
                            for item in self._restrictions
                        ),
                        key="exact-category",
                        display=sl.semantic.ControlDisplay.INDIVIDUAL,
                    ),
                )
                if RECORD_ENTRY_INSPECT in self.grants
                else ()
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
    def _lookup_form(cls) -> sl.forms.FormSpec[sd.Request[Any, Any]]:
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
    def _rebuild_form(cls) -> sl.forms.FormSpec[sd.Request[Any, Any]]:
        form = _RebuildForm().spec()
        return replace(
            form,
            items=tuple(
                item for item in form.items if not isinstance(item, sl.forms.FormField) or item.key != "version_id"
            ),
        )

    async def _choose_version(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await pick(event, "version_ids", tr(t"records-choose-version = Use another version"), self._version_picked)

    async def _version_picked(self, _event: sd.ActionEvent, item: Suggestion) -> None:
        self._version = item
        self._build_tabs()

    async def _reset_version(self, _event: sd.PressEvent) -> None:
        self._version = None
        self._build_tabs()

    async def _choose_restriction(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await pick(
            event,
            "restriction_ids",
            tr(t"records-choose-restriction = Add restriction"),
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
            if not await self.grants.confirm(source, RECORD_ENTRY_INSPECT):
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
            tr(t"records-choose-definition = Recompute a category"),
            selected,
            confirm=tr(t"records-recompute-definition = Recompute definition"),
        )

    @requires_grant(RECORD_ENTRY_INSPECT)
    async def _lookup(self, event: sl.SubmitEvent) -> None:
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

    @requires_grant(RECORD_ENTRY_REBUILD)
    async def _rebuild(self, event: sl.SubmitEvent) -> None:
        form = _RebuildForm.from_submission(event)
        kind = form.kind
        version_id = (
            form.version_id if form.version_id is not None else (int(self._version.value) if self._version else None)
        )
        kinds = (kind,) if kind is not None else (BuildKind.DOOR, BuildKind.EXTENDER)
        if version_id is not None:
            await self._computation.pin_current_version(version_id)
        summary = await self._computation.rebuild(current_version_id=version_id, kinds=kinds)
        if RECORD_ENTRY_INSPECT in self.grants:
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


def _gap_title(gap: RecordGap) -> str:
    return f"{gap.title} · {gap.subtitle}" if gap.subtitle else gap.title


def _gap_summary(gap: RecordGap) -> sl.TextLike:
    title = _gap_title(gap)
    needs = ", ".join(field.replace("_", " ") for field in gap.fields)
    return tr(t"records-gap-summary = {title} · needs {needs}")


def _diagnostic_text(diagnostic: Mapping[str, str | list[str]]) -> str:
    """Prefer the stored explanation; older rows may carry only a code."""
    message = diagnostic.get("message")
    return f"- {message}" if isinstance(message, str) and message else f"- {diagnostic.get('code', 'unknown')}"


def _category_note(definition_id: int) -> sl.TextLike:
    return tr(t"records-category-id = Category definition #{definition_id!s}")


def _remove_label(label: sl.TextLike) -> sl.TextLike:
    return tr(t"records-remove-restriction = Remove {label}")


class _LookupForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"bot-submission-records-lookup-record-category = Lookup record category")

    kind = RecordsScreen._kind_field()
    base_key = sl.forms.TextField(label=tr(t"records-exact-category-key = Exact category key"), maximum=300)
    restrictions = sl.forms.TextField(
        label=tr(t"records-restriction-ids-help = Restriction IDs, comma separated"),
        required=False,
        maximum=300,
    )
    version_id = sl.forms.IntField(
        label=tr(t"bot-submission-records-pinned-version-id = Pinned version ID"), required=False, minimum=1
    )


class _RebuildForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"bot-submission-records-rebuild-records = Rebuild all records")

    kind = RecordsScreen._kind_field(required=False)
    version_id = sl.forms.IntField(
        label=tr(t"bot-submission-records-current-version-id = Current version ID"), required=False, minimum=1
    )
