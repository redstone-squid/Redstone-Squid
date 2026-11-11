"""Canonical build catalogue, review, and schematic workspace."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast, override

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.consent import with_consented_account
from squid.bot.submission.schematic_tools import SchematicTools
from squid.bot.submission.ui import controls as build_controls
from squid.bot.ui import tr
from squid.builds.application import BuildQueryService, BuildService
from squid.builds.domain import Status
from squid.builds.models import Build
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    BUILD_SUBMISSION_APPROVE,
    BUILD_SUBMISSION_DEBUG,
    BUILD_SUBMISSION_REJECT,
)
from squid.schematics.application import SchematicService
from squid_reactivity import TopicBus, TopicHandle
from squid_ui.sources import window_fingerprint

type BuildRenderer = Callable[[Build], Awaitable[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]]
type BuildAuthorizer = Callable[[PermissionNode], Awaitable[bool]]
type RefreshPosts = Callable[[int], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class BuildCapabilities:
    """Initial visibility choices for build details and actions, not authorization to execute them."""

    view_pending: bool
    approve: bool
    reject: bool
    debug: bool
    edit: bool
    measure_timing: bool


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


class _BuildDetail(sl.Component[sl.ComponentsV2Target, sd.Request]):
    """Show one build and refresh its card when the build topic changes.

    Capabilities control which actions appear. Review, debug, and timing
    actions recheck permission through authorize; the routed editor checks its own access.
    """

    _decision: sp.ComponentDriver[sp.DecisionState, sl.ComponentsV2Target, sd.Request] | None = sl.state(
        None, opaque=True
    )
    _pending_review: str | None = sl.state(None, persist=False)
    _asset: sl.document.Asset | None = sl.state(None, opaque=True)

    def __init__(
        self,
        build: Build,
        *,
        changes: TopicHandle,
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
        self._changes = changes
        self._build_id = build.id
        self._seed: Build | None = build
        self._queries = queries
        self._builds = builds
        self.schematics = SchematicTools(
            build.id,
            schematics=schematics,
            measure_timing=capabilities.measure_timing,
            actor_account_id=actor_account_id,
            authorize=authorize,
        )
        self._render_build = render_build
        self._capabilities = capabilities
        self._actor_account_id = actor_account_id
        self._authorize = authorize
        self._refresh_posts = refresh_posts

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def projection(self) -> tuple[Build, sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]:
        self._changes.watch()
        build, self._seed = self._seed, None
        if build is None:
            build = await self._queries.get(self._build_id)
        if build is None:
            message = f"build {self._build_id} no longer exists"
            raise LookupError(message)
        return build, await self._render_build(build)

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        state = self.projection.status
        if not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status(tr(t"bot-submission-build-browse-loading-build = Loading build.")),)
        if isinstance(state, sl.resources.Ready):
            build, node = state.value
        else:
            assert state.previous is not None
            build, node = state.previous.value
        if self._decision is not None:
            return (node, self._review_prompt(), self._decision)
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [node]
        actions = self._detail_actions(build)
        if actions is not None:
            nodes.append(actions)
        nodes.append(self.schematics)
        if self._asset is not None:
            nodes.append(
                sl.download(
                    tr(t"bot-submission-build-browse-download-result = Download result"),
                    self._asset,
                    key="build-download",
                )
            )
        return tuple(nodes)

    def _detail_actions(self, build: Build) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None:
        actions: list[sl.semantic.ActionControl | sl.semantic.RoutedActionControl] = []
        if self._capabilities.edit or (
            build.submission_status is Status.PENDING
            and self._actor_account_id is not None
            and build.submitter_account_id == self._actor_account_id
        ):
            actions.append(
                sl.routed_action_control(
                    tr(t"bot-submission-build-browse-edit = Edit"),
                    build_controls.edit_build.id(build_id=self._build_id),
                    key="edit",
                )
            )
        if build.submission_status is Status.PENDING and self._capabilities.approve:
            actions.append(
                sl.action_control(
                    tr(t"bot-submission-build-browse-approve = Approve"),
                    self._request_approve,
                    key="approve",
                    tone=sl.Tone.SUCCESS,
                )
            )
        if build.submission_status is Status.PENDING and self._capabilities.reject:
            actions.append(
                sl.action_control(
                    tr(t"bot-submission-build-browse-reject = Reject"),
                    self._request_reject,
                    key="reject",
                    tone=sl.Tone.DANGER,
                )
            )
        if self._capabilities.debug:
            actions.append(
                sl.action_control(
                    tr(t"bot-submission-build-browse-debug-download = Debug download"), self._debug, key="debug"
                )
            )
        return sl.action_controls(*actions, key="build-actions") if actions else None

    def _review_prompt(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        action = self._pending_review or "review"
        build_id = self._build_id
        return sl.section(
            sl.heading(tr(t"build-review-confirmation-title = Confirm review decision")),
            sl.paragraph(
                tr(t"bot-submission-build-browse-build = {action} build #{(build_id_text := str(build_id))}?")  # noqa: F841
            ),
        )

    async def _request_approve(self, _event: sl.PressEvent) -> None:
        self._request_review("approve")

    async def _request_reject(self, _event: sl.PressEvent) -> None:
        self._request_review("reject")

    def _request_review(self, action: str) -> None:
        self._pending_review = action
        self._decision = sp.Decision[sl.ComponentsV2Target, sd.Request](
            tr(t"build-review-state-warning = This changes the build's review state."),
            (
                sp.DecisionOption("confirm", tr(t"bot-submission-build-browse-confirm = Confirm"), sl.Tone.DANGER),
                sp.DecisionOption("cancel", tr(t"bot-submission-build-browse-cancel = Cancel")),
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

        async def apply_review(source: sd.ActionEvent, account_id: int) -> None:
            if not await self._may(source, node):
                return
            if action == "approve":
                await self._builds.confirm(self._build_id, actor=account_id, source="discord_review")
            else:
                await self._builds.deny(self._build_id, actor=account_id, source="discord_review")
            await self._refresh_posts(self._build_id)
            await source.notice(tr(t"build-review-state-updated = Build review state updated."))

        source = cast(sd.ActionEvent, event.source)
        if self._actor_account_id is not None:
            await apply_review(source, self._actor_account_id)
        else:
            await with_consented_account(
                source, cast(RedstoneSquid, source.context.client).services.accounts, apply_review
            )

    async def _debug(self, event: sl.PressEvent) -> None:
        if not await self._may(event, BUILD_SUBMISSION_DEBUG):
            return
        from squid.bot.submission.search import _debug_dump

        build = await self._queries.get(self._build_id)
        if build is None:
            await event.notice(tr(t"build-no-longer-available = That build is no longer available."))
            return
        data = _debug_dump(build).encode()
        self._asset = sl.document.Asset(
            "build-debug",
            f"build-{self._build_id}-debug.json",
            "application/json",
            sl.document.InlineAsset(data),
        )

    async def _may(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(
            tr(t"build-operation-permission-denied = You are no longer allowed to perform this build operation.")
        )
        return False


class BuildBrowseScreen(sd.Screen):
    """A live build workspace that ends when replaced or timed out."""

    policy = sd.ResponseSpec(timeout=300, live_updates=True, session=sd.SessionSpec("build-browse"))

    _selected: _BuildDetail | None = sl.state(None, opaque=True)
    _tabs: sp.ComponentDriver[sp.TabsState, sl.ComponentsV2Target, sd.Request] | None = sl.state(None, opaque=True)

    def __init__(
        self,
        queries: BuildQueryService,
        builds: BuildService,
        schematics: SchematicService,
        *,
        topics: TopicBus,
        initial_id: int | None,
        render_build: BuildRenderer,
        capabilities: BuildCapabilities,
        actor_account_id: int | None,
        authorize: BuildAuthorizer,
        refresh_posts: RefreshPosts,
    ) -> None:
        self._topics = topics
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
            # `_BuildSource` filters on `submission_status`, so every listed build carries one; the
            # domain type stays optional only for a build that has not been submitted yet.
            summary=lambda build: f"{build.category} · {cast(Status, build.submission_status).name.lower()}",
            detail=self._detail,
            page_size=15,
            title=tr(t"bot-submission-build-browse-build-catalogue = Build catalogue"),
            empty=tr(t"build-list-empty = No builds are available."),
        )

    @override
    async def on_load(self) -> None:
        if self._initial_id is not None:
            await self._select(self._initial_id)
        self._tabs = sp.Tabs(
            (
                sp.Tab("browse", tr(t"bot-submission-build-browse-browse = Browse"), self._browser),
                sp.Tab("find", tr(t"bot-submission-build-browse-find-build = Find build"), self._find_nodes()),
            ),
            key="build-tabs",
            title=tr(t"bot-submission-build-browse-builds = Builds"),
        )

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self._selected is not None:
            return (
                self._selected,
                sl.action_controls(
                    sl.action_control(
                        tr(t"bot-submission-build-browse-back-to-builds = Back to builds"), self._back, key="back"
                    ),
                    key="selected-actions",
                ),
            )
        if self._tabs is None:
            return (sl.status(tr(t"bot-submission-build-browse-loading-builds = Loading builds.")),)
        return (self._tabs,)

    def _find_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return (
            sl.form(
                tr(t"bot-submission-build-browse-open-by-id = Open by ID"),
                _OpenBuildForm().spec(),
                key="open-id",
                on_submit=self._open_id,
            ),
            sl.form(
                tr(t"bot-submission-build-browse-search-by-meaning = Search by meaning"),
                _SearchForm().spec(),
                key="semantic-search",
                on_submit=self._search,
            ),
        )

    def _detail(self, build: Build) -> _BuildDetail:
        return _BuildDetail(
            build,
            changes=self._topics.topic("build", str(build.id)),
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
        form = _OpenBuildForm.from_submission(event)
        build_id = form.id
        if not await self._select(build_id):
            await event.notice(tr(t"build-id-not-visible = No visible build has that ID."))

    async def _search(self, event: sl.SubmitEvent) -> None:
        form = _SearchForm.from_submission(event)
        build = await self._queries.semantic(form.query)
        if build is None or build.id is None or not await self._select(build.id):
            await event.notice(tr(t"build-search-no-visible-match = No visible build matches that search."))

    async def _back(self, _event: sl.PressEvent) -> None:
        self._selected = None


class _OpenBuildForm(sl.forms.Form):
    title = tr(t"bot-submission-build-browse-open-build = Open build")

    id = sl.forms.IntField(label=tr(t"bot-submission-build-browse-build-id = Build ID"), minimum=1)


class _SearchForm(sl.forms.Form):
    title = tr(t"bot-submission-build-browse-find-build = Find build")

    query = sl.forms.TextField(
        label=tr(t"bot-submission-build-browse-description-or-title = Description or title"), maximum=500
    )
