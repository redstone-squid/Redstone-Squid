"""Canonical build catalogue, review, and schematic workspace."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.submission.schematics import (
    WRITABLE_EXTENSIONS,
    _describe,
    _describe_input_refusal,
    _describe_lattice,
    _describe_timing,
    _parse_position,
)
from squid.bot.submission.ui.controls import build_edit
from squid.bot.ui import L
from squid.builds.application import BuildQueryService, BuildService
from squid.builds.domain import Build, Status
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    BUILD_SCHEMATIC_DETECT_LATTICE,
    BUILD_SCHEMATIC_MEASURE_TIMING,
    BUILD_SUBMISSION_APPROVE,
    BUILD_SUBMISSION_DEBUG,
    BUILD_SUBMISSION_REJECT,
)
from squid.schematics.application import ConvertRequest, SchematicService, summarise_losses
from squid.schematics.domain.models import SchematicFormat
from squid.schematics.errors import AmbiguousSimulationInputError, SchematicRenderRefusedError
from squid.topics import resource_topic
from squid_ui.sources import window_fingerprint

type BuildRenderer = Callable[[Build], Awaitable[sl.LayoutNode[sl.ComponentsV2Target]]]
type BuildAuthorizer = Callable[[PermissionNode], Awaitable[bool]]
type RefreshPosts = Callable[[int], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class BuildCapabilities:
    """The build operations visible when the workspace opens."""

    view_pending: bool
    approve: bool
    reject: bool
    debug: bool
    edit: bool
    measure_timing: bool
    detect_lattice: bool


class _BuildSource:
    capabilities = sl.sources.SourceCapabilities(
        backward=True,
        offsets=True,
        jumpable=True,
        count=sl.sources.CountPrecision.EXACT,
    )

    def __init__(self, queries: BuildQueryService, statuses: frozenset[Status]) -> None:
        self._queries = queries
        self._statuses = statuses

    async def fetch(self, position: sl.sources.Position, extent: int) -> sl.sources.Window[Build]:
        from squid.core.pagination import PageSelector

        page = await self._queries.list_page(
            statuses=self._statuses,
            selector=PageSelector(offset=position.offset),
            page_size=extent,
        )
        return sl.sources.Window(
            sl.sources.Position(str(page.items[0].id) if page.items else None, position.offset),
            page.items,
            has_previous=page.prev is not None,
            has_next=page.next is not None,
            total=page.total,
        )

    def fingerprint(self, window: sl.sources.Window[Build]) -> str:
        return window_fingerprint(window.items, lambda build: str(build.id))


class _BuildDetail(sl.Component[sl.ComponentsV2Target]):
    """Live detail and capability-aware actions for one persisted build."""

    def __init__(
        self,
        build: Build,
        *,
        queries: BuildQueryService,
        builds: BuildService,
        schematics: SchematicService,
        render_build: BuildRenderer,
        capabilities: BuildCapabilities,
        actor_account_id: int | None,
        authorize: BuildAuthorizer,
        refresh_posts: RefreshPosts,
    ) -> None:
        assert build.id is not None
        self._build_id = build.id
        self._seed: Build | None = build
        self._queries = queries
        self._builds = builds
        self._schematics = schematics
        self._render_build = render_build
        self._capabilities = capabilities
        self._actor_account_id = actor_account_id
        self._authorize = authorize
        self._refresh_posts = refresh_posts
        self._decision: sp.ComponentDriver[sp.DecisionState, sl.ComponentsV2Target] | None = None
        self._pending_review: str | None = None
        self._schematic_result: sl.TextLike | None = None
        self._asset: sl.document.Asset | None = None

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def projection(self) -> tuple[Build, sl.LayoutNode[sl.ComponentsV2Target]]:
        sl.runtime.watch(resource_topic("build", str(self._build_id)))
        build, self._seed = self._seed, None
        if build is None:
            build = await self._queries.get(self._build_id)
        if build is None:
            message = f"build {self._build_id} no longer exists"
            raise LookupError(message)
        return build, await self._render_build(build)

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        state = self.projection.status
        if not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status(L(t"Loading build.")),)
        if isinstance(state, sl.resources.Ready):
            build, node = state.value
        else:
            assert state.previous is not None
            build, node = state.previous.value
        if self._decision is not None:
            return (node, self._review_prompt(), self.boundary(self._decision, key="review-decision"))
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [node]
        actions = self._detail_actions(build)
        if actions is not None:
            nodes.append(actions)
        nodes.extend(self._schematic_nodes())
        if self._schematic_result is not None:
            nodes.append(sl.note(self._schematic_result))
        if self._asset is not None:
            nodes.append(sl.download(L(t"Download result"), self._asset, key="build-download"))
        return tuple(nodes)

    def _detail_actions(self, build: Build) -> sl.LayoutNode[sl.ComponentsV2Target] | None:
        actions: list[sl.semantic.ActionControl | sl.semantic.RoutedActionControl] = []
        if self._capabilities.edit or (
            build.submission_status is Status.PENDING
            and self._actor_account_id is not None
            and build.submitter_account_id == self._actor_account_id
        ):
            actions.append(sl.routed_action_control(L(t"Edit"), build_edit.id(build_id=self._build_id), key="edit"))
        if build.submission_status is Status.PENDING and self._capabilities.approve:
            actions.append(sl.action_control(L(t"Approve"), self._request_approve, key="approve", tone=sl.Tone.SUCCESS))
        if build.submission_status is Status.PENDING and self._capabilities.reject:
            actions.append(sl.action_control(L(t"Reject"), self._request_reject, key="reject", tone=sl.Tone.DANGER))
        if self._capabilities.debug:
            actions.append(sl.action_control(L(t"Debug download"), self._debug, key="debug"))
        return sl.action_controls(*actions, key="build-actions") if actions else None

    def _schematic_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if not self._schematics.available:
            return ()
        actions = [
            sl.action_control(L(t"Schematic info"), self._schematic_info, key="schematic-info"),
            sl.action_control(L(t"Download schematic"), self._schematic_download, key="schematic-download"),
            sl.action_control(L(t"Render schematic"), self._schematic_render, key="schematic-render"),
        ]
        if self._capabilities.detect_lattice:
            actions.append(sl.action_control(L(t"Detect lattice"), self._detect_lattice, key="detect-lattice"))
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [sl.action_controls(*actions, key="schematic-actions")]
        nodes.append(sl.form(L(t"Convert schematic"), self._convert_form(), key="convert", on_submit=self._convert))
        if self._capabilities.measure_timing:
            nodes.append(sl.form(L(t"Measure timing"), self._timing_form(), key="timing", on_submit=self._timing))
        return tuple(nodes)

    @staticmethod
    def _convert_form() -> sl.forms.FormSpec:
        return sl.forms.FormSpec(
            L(t"Convert schematic"),
            (
                sl.forms.ChoiceField(
                    key="format",
                    label=L(t"Output format"),
                    default=SchematicFormat.LITEMATIC,
                    options=tuple(
                        sl.forms.ChoiceOption(file_format.value, file_format.value, file_format)
                        for file_format in WRITABLE_EXTENSIONS
                    ),
                ),
                sl.forms.IntField(key="data_version", label=L(t"Minecraft data version"), required=False),
                sl.forms.TextField(key="version", label=L(t"Minecraft version name"), required=False, maximum=100),
            ),
        )

    @staticmethod
    def _timing_form() -> sl.forms.FormSpec:
        return sl.forms.FormSpec(
            L(t"Measure schematic timing"),
            (sl.forms.TextField(key="input", label=L(t"Input position (x y z)"), required=False, maximum=100),),
        )

    def _review_prompt(self) -> sl.LayoutNode[sl.ComponentsV2Target]:
        action = self._pending_review or "review"
        return sl.section(
            sl.heading(L(t"Confirm review decision")),
            sl.paragraph(L(t"{action} build #{self._build_id}?")),
        )

    async def _request_approve(self, _event: sl.PressEvent) -> None:
        self._request_review("approve")

    async def _request_reject(self, _event: sl.PressEvent) -> None:
        self._request_review("reject")

    def _request_review(self, action: str) -> None:
        self._pending_review = action
        self._decision = sp.Decision[sl.ComponentsV2Target](
            L(t"This changes the build's review state."),
            (
                sp.DecisionOption("confirm", L(t"Confirm"), sl.Tone.DANGER),
                sp.DecisionOption("cancel", L(t"Cancel")),
            ),
            key="build-review",
        ).build_component(on_decide=self._decide_review)

    async def _decide_review(self, event: sp.TransitionEvent[sp.DecisionState], choice: str) -> None:
        action = self._pending_review
        self._pending_review = None
        self._decision = None
        if choice == "cancel" or action is None:
            return
        node = BUILD_SUBMISSION_APPROVE if action == "approve" else BUILD_SUBMISSION_REJECT
        if not await self._may(event.source, node):
            return
        if action == "approve":
            await self._builds.confirm(self._build_id)
        else:
            await self._builds.deny(self._build_id)
        await self._refresh_posts(self._build_id)
        await event.source.notice(L(t"Build review state updated."))

    async def _debug(self, event: sl.PressEvent) -> None:
        if not await self._may(event, BUILD_SUBMISSION_DEBUG):
            return
        from squid.bot.submission.search import _debug_dump

        build = await self._queries.get(self._build_id)
        if build is None:
            await event.notice(L(t"That build is no longer available."))
            return
        data = _debug_dump(build).encode()
        self._asset = sl.document.Asset(
            "build-debug",
            f"build-{self._build_id}-debug.json",
            "application/json",
            sl.document.InlineAsset(data),
        )

    async def _primary(self, event: sl.ActionEvent):
        stored = await self._schematics.primary_for_build(self._build_id)
        if stored is None:
            await event.notice(L(t"This build has no schematic attached."))
        return stored

    async def _schematic_info(self, event: sl.PressEvent) -> None:
        stored = await self._primary(event)
        if stored is None:
            return
        self._schematic_result = _describe(
            stored,
            locale=event.locale,
            render_skip=self._schematics.explain_render_skip(stored),
        )

    async def _schematic_download(self, event: sl.PressEvent) -> None:
        if await self._primary(event) is None:
            return
        data, _ = await self._schematics.convert(
            self._build_id, ConvertRequest(target_format=SchematicFormat.LITEMATIC)
        )
        self._asset = self._schematic_asset(data, "litematic")

    async def _schematic_render(self, event: sl.PressEvent) -> None:
        if await self._primary(event) is None:
            return
        try:
            rendered = await self._schematics.render_now(self._build_id, request=self._schematics.render_recipe())
        except SchematicRenderRefusedError as error:
            await event.notice(error.localized_public_detail(event.locale))
            return
        self._asset = sl.document.Asset(
            "schematic-render",
            f"build-{self._build_id}-render.png",
            "image/png",
            sl.document.InlineAsset(rendered.png),
        )

    async def _convert(self, event: sl.SubmitEvent) -> None:
        if await self._primary(event) is None:
            return
        file_format = cast(SchematicFormat, event.values["format"])
        data_version = cast(int | None, event.values.get("data_version"))
        version = cast(str | None, event.values.get("version")) or None
        data, losses = await self._schematics.convert(
            self._build_id,
            ConvertRequest(target_format=file_format, target_data_version=data_version),
            version_label=version,
        )
        self._asset = self._schematic_asset(data, WRITABLE_EXTENSIONS[file_format])
        self._schematic_result = L(t"Conversion report: {summarise_losses(losses)}")

    async def _timing(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, BUILD_SCHEMATIC_MEASURE_TIMING) or await self._primary(event) is None:
            return
        raw = cast(str | None, event.values.get("input")) or None
        position = _parse_position(raw)
        if raw is not None and position is None:
            await event.notice(L(t"Input position must contain three integers, for example `12 5 -3`."))
            return
        try:
            result = await self._schematics.measure_timing(self._build_id, input_position=position)
        except AmbiguousSimulationInputError as error:
            self._schematic_result = _describe_input_refusal(error, locale=event.locale)
            return
        self._schematic_result = _describe_timing(result, locale=event.locale)

    async def _detect_lattice(self, event: sl.PressEvent) -> None:
        if not await self._may(event, BUILD_SCHEMATIC_DETECT_LATTICE) or await self._primary(event) is None:
            return
        lattice = await self._schematics.detect_lattice(self._build_id)
        self._schematic_result = (
            L(t"No repeating lattice was detected in this schematic.")
            if lattice is None
            else _describe_lattice(lattice, locale=event.locale)
        )

    def _schematic_asset(self, data: bytes, extension: str) -> sl.document.Asset:
        return sl.document.Asset(
            "schematic",
            f"build-{self._build_id}.{extension}",
            "application/octet-stream",
            sl.document.InlineAsset(data),
        )

    async def _may(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(L(t"You are no longer allowed to perform this build operation."))
        return False


class BuildBrowseScreen(sd.Screen):
    """A live build workspace that ends when closed, replaced, or timed out."""

    session_name = "build-browse"
    timeout = 300
    visibility = "personal"
    follow_topics = True

    def __init__(
        self,
        queries: BuildQueryService,
        builds: BuildService,
        schematics: SchematicService,
        *,
        initial_id: int | None,
        render_build: BuildRenderer,
        capabilities: BuildCapabilities,
        actor_account_id: int | None,
        authorize: BuildAuthorizer,
        refresh_posts: RefreshPosts,
    ) -> None:
        self._queries = queries
        self._builds = builds
        self._schematics = schematics
        self._initial_id = initial_id
        self._render_build = render_build
        self._capabilities = capabilities
        self._actor_account_id = actor_account_id
        self._authorize = authorize
        self._refresh_posts = refresh_posts
        statuses = {Status.CONFIRMED}
        if capabilities.view_pending:
            statuses.add(Status.PENDING)
        self._browser = sp.Browser(
            _BuildSource(queries, frozenset(statuses)),
            key="builds",
            identity=lambda build: str(build.id),
            label=lambda build: f"#{build.id} {build.title}",
            summary=lambda build: f"{build.category} · {build.submission_status.name.lower()}",
            detail=self._detail,
            page_size=15,
            title=L(t"Build catalogue"),
            empty=L(t"No builds are available."),
        )
        self._selected: _BuildDetail | None = None
        self._tabs: sp.ComponentDriver[sp.TabsState, sl.ComponentsV2Target] | None = None

    async def on_load(self) -> None:
        if self._initial_id is not None:
            await self._select(self._initial_id)
        self._tabs = sp.Tabs(
            (
                sp.Tab("browse", L(t"Browse"), self._browser),
                sp.Tab("find", L(t"Find build"), self._find_nodes()),
            ),
            key="build-tabs",
            title=L(t"Builds"),
        ).build_component()

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self._selected is not None:
            return (
                self.boundary(self._selected, key="selected-build"),
                sl.action_controls(
                    sl.action_control(L(t"Back to builds"), self._back, key="back"),
                    sl.action_control(L(t"Close"), self._close, key="close"),
                    key="selected-actions",
                ),
            )
        if self._tabs is None:
            return (sl.status(L(t"Loading builds.")),)
        return (
            self.boundary(self._tabs, key="tabs"),
            sl.action_controls(sl.action_control(L(t"Close"), self._close, key="close"), key="build-screen-actions"),
        )

    def _find_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        return (
            sl.form(
                L(t"Open by ID"),
                sl.forms.FormSpec(
                    L(t"Open build"),
                    (sl.forms.IntField(key="id", label=L(t"Build ID"), minimum=1),),
                ),
                key="open-id",
                on_submit=self._open_id,
            ),
            sl.form(
                L(t"Search by meaning"),
                sl.forms.FormSpec(
                    L(t"Find build"),
                    (sl.forms.TextField(key="query", label=L(t"Description or title"), maximum=500),),
                ),
                key="semantic-search",
                on_submit=self._search,
            ),
        )

    def _detail(self, build: Build) -> _BuildDetail:
        return _BuildDetail(
            build,
            queries=self._queries,
            builds=self._builds,
            schematics=self._schematics,
            render_build=self._render_build,
            capabilities=self._capabilities,
            actor_account_id=self._actor_account_id,
            authorize=self._authorize,
            refresh_posts=self._refresh_posts,
        )

    async def _select(self, build_id: int) -> bool:
        build = await self._queries.get(build_id)
        if build is None or (build.submission_status is not Status.CONFIRMED and not self._capabilities.view_pending):
            return False
        self._selected = self._detail(build)
        return True

    async def _open_id(self, event: sl.SubmitEvent) -> None:
        build_id = cast(int, event.values["id"])
        if not await self._select(build_id):
            await event.notice(L(t"No visible build has that ID."))

    async def _search(self, event: sl.SubmitEvent) -> None:
        build = await self._queries.semantic(cast(str, event.values["query"]))
        if build is None or build.id is None or not await self._select(build.id):
            await event.notice(L(t"No visible build matches that search."))

    async def _back(self, _event: sl.PressEvent) -> None:
        self._selected = None

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()
