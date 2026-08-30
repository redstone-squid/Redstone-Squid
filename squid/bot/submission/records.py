"""Discord workspace for querying and maintaining computed records."""

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Protocol, cast

import discord
from discord import app_commands

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import tr
from squid.bot.utils.permissions import allows, enforce, hide_unless
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD
from squid.records.application import RebuildSummary, RecordGap, RecordLookupRequest, TitleDiagnosticGap
from squid.records.domain import BuildKind
from squid_ui_discord.ext import Cog

if TYPE_CHECKING:
    import squid.bot.app


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
    """A records workspace that ends when closed, replaced, or timed out."""

    session = sd.SessionSpec("records", scope=sd.ScopeKind.USER_GUILD)
    timeout = 300
    audience = "personal"

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
        self._gaps: sp.Browser[RecordGap, sl.ComponentsV2Target] | None = None
        self._titles: sp.Browser[TitleDiagnosticGap, sl.ComponentsV2Target] | None = None
        self._tabs: sp.ComponentDriver[sp.TabsState, sl.ComponentsV2Target] | None = None

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
            label=lambda gap: gap.title,
            summary=lambda gap: f"{gap.title} · missing {', '.join(gap.fields)}",
            detail=lambda gap: sl.fields(
                sl.field(tr(t"Definition"), str(gap.definition_id)),
                sl.field(tr(t"Builds"), ", ".join(map(str, gap.build_ids))),
                sl.field(tr(t"Missing evidence"), ", ".join(gap.fields)),
            ),
            page_size=15,
            title=tr(t"Record evidence gaps"),
            empty=tr(t"No unresolved active record categories."),
        )
        self._titles = sp.Browser(
            sl.sources.list_source(titles),
            key="title-issues",
            identity=lambda gap: str(gap.definition_id),
            label=lambda gap: gap.title,
            summary=lambda gap: gap.title,
            detail=lambda gap: sl.fields(
                sl.field(tr(t"Definition"), str(gap.definition_id)),
                sl.field(
                    tr(t"Diagnostics"),
                    ", ".join(str(item.get("code", "unknown")) for item in gap.diagnostics),
                ),
            ),
            page_size=15,
            title=tr(t"Record title diagnostics"),
            empty=tr(t"No active record titles require taxonomy review."),
        )

    def _build_tabs(self) -> None:
        tabs: list[sp.Tab[sl.ComponentsV2Target]] = []
        if self._gaps is not None and self._titles is not None:
            tabs.extend(
                (
                    sp.Tab("gaps", tr(t"Evidence gaps"), self._gaps),
                    sp.Tab("titles", tr(t"Title issues"), self._titles),
                )
            )
        tabs.append(sp.Tab("maintenance", tr(t"Lookup and rebuild"), self._maintenance_nodes()))
        self._tabs = sp.Tabs(tabs, key="records-tabs", title=tr(t"Records")).build_component()

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self._tabs is None:
            return (sl.status(tr(t"Loading record diagnostics.")),)
        return (
            self.boundary(self._tabs, key="tabs"),
            sl.action_controls(sl.action_control(tr(t"Close"), self._close, key="close"), key="record-actions"),
        )

    def _maintenance_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = []
        if self._can_inspect:
            nodes.append(sl.form(tr(t"Lookup category"), self._lookup_form(), key="lookup", on_submit=self._lookup))
        if self._can_rebuild:
            nodes.append(sl.form(tr(t"Rebuild records"), self._rebuild_form(), key="rebuild", on_submit=self._rebuild))
        return tuple(nodes) or (sl.note(tr(t"No maintenance actions are available.")),)

    @staticmethod
    def _kind_field(*, required: bool = True) -> sl.forms.ChoiceField[BuildKind]:
        return sl.forms.ChoiceField(
            key="kind",
            label=tr(t"Build kind"),
            required=required,
            options=tuple(sl.forms.ChoiceOption(kind.value, kind.value.title(), kind) for kind in BuildKind),
        )

    @classmethod
    def _lookup_form(cls) -> sl.forms.FormSpec:
        return sl.forms.FormSpec(
            tr(t"Lookup record category"),
            (
                cls._kind_field(),
                sl.forms.TextField(key="base_key", label=tr(t"Definition ID or base key"), maximum=300),
                sl.forms.TextField(
                    key="restrictions",
                    label=tr(t"Restriction IDs, comma separated"),
                    required=False,
                    maximum=300,
                ),
                sl.forms.IntField(key="version_id", label=tr(t"Pinned version ID"), required=False, minimum=1),
            ),
        )

    @classmethod
    def _rebuild_form(cls) -> sl.forms.FormSpec:
        return sl.forms.FormSpec(
            tr(t"Rebuild records"),
            (
                cls._kind_field(required=False),
                sl.forms.IntField(key="version_id", label=tr(t"Current version ID"), required=False, minimum=1),
            ),
        )

    async def _lookup(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, RECORD_ENTRY_INSPECT):
            return
        kind = cast(BuildKind, event.values["kind"])
        selected = cast(str, event.values["base_key"]).strip()
        version_id = cast(int | None, event.values.get("version_id"))
        raw_restrictions = cast(str | None, event.values.get("restrictions")) or ""
        try:
            restrictions = frozenset(int(value.strip()) for value in raw_restrictions.split(",") if value.strip())
        except ValueError:
            await event.notice(tr(t"Restriction IDs must be whole numbers separated by commas."))
            return
        if selected.isdigit():
            if restrictions:
                await event.notice(tr(t"Restrictions can only be combined with a hand-typed base key."))
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
        await event.notice(tr(t"Recomputed {definitions} definitions; {resolved} resolved."))

    async def _rebuild(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, RECORD_ENTRY_REBUILD):
            return
        kind = cast(BuildKind | None, event.values.get("kind"))
        version_id = cast(int | None, event.values.get("version_id"))
        kinds = (kind,) if kind is not None else (BuildKind.DOOR, BuildKind.EXTENDER)
        summary = await self._computation.rebuild(current_version_id=version_id, kinds=kinds)
        if self._can_inspect:
            await self._refresh_diagnostics()
            self._build_tabs()
        definitions = summary.definitions
        resolved = summary.resolved
        unresolved = summary.unresolved
        await event.notice(
            tr(t"Rebuilt {definitions} definitions; {resolved} resolved; {unresolved} awaiting evidence.")
        )

    async def _may(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(tr(t"You are no longer allowed to perform this records operation."))
        return False

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()


class RecordCog[BotT: "squid.bot.app.RedstoneSquid"](Cog[BotT]):
    """Open the record diagnostics and maintenance workspace."""

    def __init__(self, bot: BotT) -> None:
        super().__init__(bot)
        self.records = bot.services.records
        self.computation = bot.services.record_computation

    @app_commands.command(name="records", description="Inspect and maintain computed records")
    @app_commands.guild_only()
    @hide_unless(manage_guild=True)
    async def records_workspace(self, interaction: discord.Interaction[BotT]) -> None:
        """Open capability-aware diagnostics, lookup, and rebuild tools."""
        await enforce(interaction, RECORD_ENTRY_INSPECT, RECORD_ENTRY_REBUILD, mode="any")

        async def authorize(node: PermissionNode) -> bool:
            return await allows(interaction, node)

        await self.ui.respond(
            interaction,
            RecordsScreen(
                self.records,
                self.computation,
                can_inspect=await authorize(RECORD_ENTRY_INSPECT),
                can_rebuild=await authorize(RECORD_ENTRY_REBUILD),
                authorize=authorize,
            ),
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the records workspace cog."""
    await bot.add_cog(RecordCog(bot))
