"""Mounted squid-ui component for interactive search results."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol, override

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.catalogue.record_display import record_row, record_standings
from squid.bot.catalogue.rows import build_row
from squid.bot.ui import tr, truncate_display_text
from squid.core.actors import ActingContext
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build
from squid.core.records.domain.models import RecordCriterion, VersionScope
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.core.search.application.results import BuildSearchHit, RecordSearchHit, SearchHit, SearchPage
from squid.core.search.domain import FieldMatch, SearchRequest, SearchResultAnchor, SearchScope, SearchText
from squid.core.vocabulary import RECORD_CRITERIA, VERSION_SCOPES
from squid_i18n import Text, md_tr, strong
from squid_reactivity import untracked
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.sources import BatchLoader, Position, batch_fingerprint

if TYPE_CHECKING:
    from squid.core.search.application import SearchService


class BuildLoader(Protocol):
    async def __call__(self, build_id: int, *, context: ActingContext) -> Build | None: ...


type BuildRenderer = Callable[[Build], Awaitable[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]]]
type BuildDetailFactory = Callable[[int], sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]]
"""Build the full, interactive view of one build, given its ID, for a host that has one."""


@dataclass(frozen=True, slots=True)
class SearchFocus:
    """The selected result and any record or holder open inside it."""

    anchor: SearchResultAnchor
    record_id: str | None = None
    holder_id: int | None = None
    show_records: bool = False


def _hit_identity(hit: SearchHit) -> str:
    return f"{hit.resource_kind}:{hit.source_id}"


class _SearchSource:
    """Adapt search pages to browser batches while retaining their warning metadata."""

    capabilities = sl.sources.SourceCapabilities(
        backward=True,
        offsets=True,
        fixed_groups=True,
        jumpable=True,
        count=sl.sources.CountPrecision.EXACT,
    )

    def __init__(self, service: SearchService, request: SearchRequest, initial: SearchPage) -> None:
        self._service = service
        self.base_request = request
        self._initial: SearchPage | None = initial
        self._metadata: dict[tuple[int, str], SearchPage] = {}

    def request_at(self, offset: int) -> SearchRequest:
        return replace(self.base_request, offset=offset)

    def _loaded(self, page: SearchPage, offset: int) -> sl.sources.LoadedBatch[SearchHit]:
        batch = sl.sources.Batch(
            sl.sources.Position(_hit_identity(page.hits[0]) if page.hits else None, offset),
            page.hits,
            previous=sl.sources.Position(offset=page.prev.offset)
            if page.prev is not None and page.prev.offset is not None
            else None,
            next=sl.sources.Position(offset=page.next.offset)
            if page.next is not None and page.next.offset is not None
            else None,
            item_count=page.total,
        )
        fingerprint = batch_fingerprint(page.hits, _hit_identity)
        # Offset alone cannot distinguish an older visible page from a refreshed result set.
        self._metadata[(offset, fingerprint)] = page
        return sl.sources.LoadedBatch(batch, fingerprint)

    def initial_loaded(self) -> sl.sources.LoadedBatch[SearchHit]:
        assert self._initial is not None
        page = self._initial
        self._initial = None
        return self._loaded(page, self.base_request.offset)

    async def fetch(self, position: sl.sources.Position, fetch_size: int) -> sl.sources.Batch[SearchHit]:
        request = replace(self.base_request, offset=position.offset, page_size=fetch_size)
        page = await self._service.search(request, context=ActingContext.current())
        return self._loaded(page, position.offset).batch

    def page_for(self, loaded: sl.sources.LoadedBatch[SearchHit]) -> SearchPage:
        page = self._metadata.get((loaded.position.offset, loaded.fingerprint))
        if page is None:
            message = "search source has no metadata for the loaded batch"
            raise sl.errors.LayoutInvariantError(message)
        return page


class _SearchDetail(sl.Component[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]):
    _build_node: sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | None = sl.state(None, opaque=True)

    def __init__(
        self,
        hit: SearchHit,
        *,
        load_build: BuildLoader | None,
        render_build: BuildRenderer | None,
        build_detail: BuildDetailFactory | None = None,
    ) -> None:
        self.hit = hit
        self._load_build = load_build
        self._render_build = render_build
        build_id = _build_id(hit)
        self._full_build = build_detail(build_id) if build_detail is not None and build_id is not None else None

    @override
    def render(self) -> sl.DocumentLike[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        if self._full_build is not None:
            # The host's build view replaces the preview, so the result opens in one step.
            if isinstance(self.hit, RecordSearchHit):
                return (_record_heading(self.hit), self._full_build)
            return self._full_build
        detail = self._build_node or sl.section(
            sl.heading(self.hit.title),
            sl.paragraph(_detail_text(self.hit)).truncate(),
            accent=sl.Tone.SUCCESS,
        )
        build_id = _build_id(self.hit)
        return (
            detail,
            *(
                (
                    sl.action_controls(
                        sl.action_control(
                            tr(t"bot-submission-search-view-view-build = Expand"),
                            self._open_build,
                            key="open-build",
                        ),
                        key="build-actions",
                    ),
                )
                if build_id is not None
                else ()
            ),
        )

    async def _open_build(self, event: sl.ActionEvent[Any]) -> None:
        build_id = _build_id(self.hit)
        if build_id is None or self._load_build is None or self._render_build is None:
            await event.notice(tr(t"build-search-build-unavailable = That build is no longer available."))
            return
        build = await self._load_build(build_id, context=ActingContext.current())
        if build is None:
            await event.notice(tr(t"build-search-build-unavailable = That build is no longer available."))
            return
        self._build_node = await self._render_build(build)


class _GroupedBuildDetail(sl.Component[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]):
    _show_records: bool = sl.state(False)
    _holder_id: int | None = sl.state(None)
    _holder_build: sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | None = sl.state(
        None, opaque=True
    )

    def __init__(
        self,
        hit: BuildSearchHit,
        scope: SearchScope,
        *,
        load_build: BuildLoader | None,
        render_build: BuildRenderer | None,
        build_detail: BuildDetailFactory | None = None,
    ) -> None:
        self.hit = hit
        self._load_build = load_build
        self._render_build = render_build
        self._build_detail = build_detail
        self._full_build = build_detail(int(hit.source_id)) if build_detail is not None else None
        self._records_label = (
            tr(t"search-grouped-matching-records = Matching records")
            if scope is SearchScope.RECORDS
            else tr(t"search-grouped-records = Records")
        )
        self._records = sp.Browser(
            sl.sources.list_source(hit.records),
            key="search-records",
            identity=lambda record: record.source_id,
            label=lambda record: truncate_display_text(record.one_line_title, 80),
            summary=_record_line,
            detail=self._record_detail,
            fetch_size=5,
            title=self._records_label,
        )

    @property
    def focus(self) -> SearchFocus:
        return SearchFocus(
            SearchResultAnchor("build", self.hit.source_id),
            self._records.session.focus,
            self._holder_id,
            self._show_records,
        )

    async def restore_focus(self, focus: SearchFocus) -> None:
        record_id = focus.record_id
        if record_id is not None:
            for index, record in enumerate(self.hit.records):
                if record.source_id == record_id:
                    session = self._records.session
                    offset = index // session.fetch_size * session.fetch_size
                    loaded = await BatchLoader(session.source, session.fetch_size, session.identity).load(
                        Position(offset=offset)
                    )
                    assert loaded is not None
                    session.replace(loaded)
                    session.open(record_id, revision=session.revision)
                    if (holder_id := focus.holder_id) is not None and holder_id in record.holder_build_ids:
                        self._select_holder(holder_id)
                    break
        self._show_records = focus.show_records

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def card(self) -> sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        build_id = self._holder_id or int(self.hit.source_id)
        if self._load_build is None or self._render_build is None:
            return sl.section(sl.heading(self.hit.title), sl.paragraph(_detail_text(self.hit)))
        build = await self._load_build(build_id, context=ActingContext.current())
        if build is None or build.submission_status is not BuildStatus.APPROVED:
            return sl.note(tr(t"build-search-build-unavailable = That build is no longer available."))
        return await self._render_build(build)

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        if self._show_records and self._holder_id is None:
            return (
                self._records,
                sl.action_controls(
                    sl.action_control(
                        tr(t"search-grouped-build-details = Build details"),
                        self._back_to_build,
                        key="search-build-details",
                    ),
                    key="search-build-detail-actions",
                ),
            )
        if self._holder_id is not None:
            node = self._holder_build if self._holder_build is not None else self._card_node()
        else:
            node = self._full_build if self._full_build is not None else self._card_node()
        if self._holder_id is not None:
            return (
                node,
                sl.action_controls(
                    sl.action_control(
                        self._records_label,
                        self._back_to_records,
                        key="search-back-records",
                    ),
                    key="search-holder-actions",
                ),
            )
        if self.hit.records:
            return (
                node,
                sl.action_controls(
                    sl.action_control(
                        self._records_label,
                        self._back_to_records,
                        key="search-show-records",
                    ),
                    key="search-record-actions",
                ),
            )
        return (node,)

    def _card_node(self) -> sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        state = self.card.status
        if isinstance(state, sl.resources.Ready):
            return state.value
        if isinstance(state, sl.resources.Failed):
            return sl.stack(
                sl.note(tr(t"build-search-build-unavailable = That build is no longer available.")),
                sl.action_controls(
                    sl.action_control(
                        tr(t"bot-submission-search-view-retry = Retry"), self._retry, key="search-build-retry"
                    ),
                    key="search-build-retry-actions",
                ),
            )
        return sl.note(tr(t"search-grouped-loading-build = Loading build…"))

    def _record_detail(
        self, record: RecordSearchHit
    ) -> sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        metrics = ", ".join(f"{key}: {value}" for key, value in record.metrics.items())
        return record_standings(
            _title(record),
            record.subtitle,
            RecordCriterion(record.criterion),
            VersionScope(record.version_scope),
            record.holder_build_ids,
            self._open_holder,
            current_holder=int(self.hit.source_id),
            details=(sl.paragraph(metrics),) if metrics else (),
        )

    async def _open_holder(self, build_id: int, _event: sl.ActionEvent[Any]) -> None:
        self._select_holder(build_id)

    def _select_holder(self, build_id: int) -> None:
        self._holder_id = build_id
        if self._build_detail is not None:
            self._holder_build = self._build_detail(build_id)

    async def _back_to_records(self, _event: sl.ActionEvent[Any]) -> None:
        self._holder_id = None
        self._show_records = True

    async def _back_to_build(self, _event: sl.ActionEvent[Any]) -> None:
        self._show_records = False

    async def _retry(self, _event: sl.ActionEvent[Any]) -> None:
        self.card.invalidate()


class SearchScreen(sd.Screen[None, Any, RedstoneSquid]):
    """A resource-backed search workspace that ends when replaced or timed out."""

    policy = sd.ResponseSpec(
        nav=sd.navigation_controls.page_jump_nav, timeout=180, audience="public", session=sd.SessionSpec("search")
    )

    def __init__(
        self,
        service: SearchService,
        request: SearchRequest,
        page: SearchPage,
        *,
        load_build: BuildLoader | None = None,
        render_build: BuildRenderer | None = None,
        build_detail: BuildDetailFactory | None = None,
        open_holder: Callable[[int, sl.PressEvent], Awaitable[None]] | None = None,
    ) -> None:
        self._build_detail = build_detail
        self._open_holder = open_holder
        self._restored_detail: tuple[int, _GroupedBuildDetail] | None = None
        request = replace(request, offset=request.offset if page.offset is None else page.offset, anchor=None)
        self._source = _SearchSource(service, request, page)
        self._load_build = load_build
        self._render_build = render_build
        self._browser = sp.Browser(
            self._source,
            key="search",
            identity=_hit_identity,
            label=lambda hit: _result_label(hit, request),
            summary=lambda hit: _result_description(hit, request),
            detail=self._detail,
            overview=self._overview,
            fetch_size=request.page_size,
            show_fetch_size=True,
            title=tr(t"bot-submission-search-view-search-results = Search results"),
            empty=tr(t"build-search-no-results = No results match this query."),
            copy=sp.LoadingCopy(
                loading=tr(t"build-search-loading = Loading search results…"),
                failed=tr(t"build-search-load-failed = Could not load search results."),
                retry=tr(t"bot-submission-search-view-retry = Retry"),
            ),
        )
        self._browser.session.replace(self._source.initial_loaded())

    @property
    def focus(self) -> SearchFocus | None:
        hit = self._browser.opened
        if isinstance(hit, BuildSearchHit):
            detail = self._browser._detail.status
            if isinstance(detail, sl.resources.Ready) and isinstance(detail.value, _GroupedBuildDetail):
                return detail.value.focus
            return SearchFocus(SearchResultAnchor("build", hit.source_id))
        if isinstance(hit, RecordSearchHit):
            return SearchFocus(SearchResultAnchor("record", hit.source_id), record_id=hit.source_id, show_records=True)
        return None

    async def restore_focus(self, focus: SearchFocus) -> bool:
        anchor = focus.anchor
        for hit in self.hits:
            if hit.resource_kind == anchor.resource_kind and hit.source_id == anchor.source_id:
                break
            if (
                anchor.resource_kind == "record"
                and isinstance(hit, BuildSearchHit)
                and any(record.source_id == anchor.source_id for record in hit.records)
            ):
                break
        else:
            return False
        session = self._browser.session
        session.open(_hit_identity(hit), revision=session.revision)
        if isinstance(hit, BuildSearchHit) and (focus.show_records or focus.record_id is not None):
            detail = self._detail(hit)
            if isinstance(detail, _GroupedBuildDetail):
                await detail.restore_focus(focus)
                # The browser may build its detail again when the focus transaction publishes.
                self._restored_detail = (session.visit, detail)
        return True

    @property
    def request(self) -> SearchRequest:
        """Return the request currently displayed by the component."""
        return self._source.request_at(self._visible_window().position.offset)

    @property
    def page(self) -> SearchPage:
        """Return the current result page."""
        return self._source.page_for(self._visible_window())

    @property
    def hits(self) -> tuple[SearchHit, ...]:
        """Return the results currently displayed."""
        return self._visible_window().batch.items

    @property
    def can_go_back(self) -> bool:
        """Return whether an earlier page exists."""
        return self._visible_window().batch.has_previous

    @property
    def can_go_forward(self) -> bool:
        """Return whether a later page exists."""
        return self._visible_window().batch.has_next

    def _visible_window(self) -> sl.sources.LoadedBatch[SearchHit]:
        """Keep the last loaded page visible while a fetch is pending or has failed."""
        current = self._browser.session.snapshot
        if current is None:
            message = "search browser has no visible batch"
            raise sl.errors.LayoutInvariantError(message)
        return current.loaded

    def _detail(self, hit: SearchHit) -> sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        if (
            isinstance(hit, RecordSearchHit)
            and self._open_holder is not None
            and (not hit.build_id or len(hit.holder_build_ids) > 1)
        ):
            details: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = [
                sl.paragraph(line) for line in _holder_lines(hit)
            ]
            if hit.matched_holder_id is not None and hit.metrics:
                details.insert(0, sl.paragraph(_detail_text(hit)).truncate())
            return record_standings(
                _title(hit),
                hit.subtitle,
                RecordCriterion(hit.criterion),
                VersionScope(hit.version_scope),
                hit.holder_build_ids,
                self._open_holder,
                details=details,
            )
        if isinstance(hit, BuildSearchHit) and self._source.base_request.group_by_build:
            restored = self._restored_detail
            if restored is not None and restored[0] == self._browser.session.visit:
                return restored[1]
            # The nested browser owns its state; reading it while constructing the
            # detail must not make the outer browser recreate it on navigation.
            with untracked():
                return _GroupedBuildDetail(
                    hit,
                    self._source.base_request.scope,
                    load_build=self._load_build,
                    render_build=self._render_build,
                    build_detail=self._build_detail,
                )
        return _SearchDetail(
            hit,
            load_build=self._load_build,
            render_build=self._render_build,
            build_detail=self._build_detail,
        )

    def _overview(
        self, loaded: sl.sources.LoadedBatch[SearchHit]
    ) -> sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | tuple[()]:
        page = self._source.page_for(loaded)
        notes: list[sl.TextLike] = []
        if self.request.group_by_build:
            total = page.total
            notes.append(
                tr(t"search-grouped-build-count = {total} builds")
                if self.request.scope in {SearchScope.BUILDS, SearchScope.RECORDS}
                else tr(t"search-grouped-result-count = {total} results")
            )
        notes.extend(f"⚠ {item}" for item in page.warnings)
        return sl.note(Text.join("\n", notes)) if notes else ()

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        return (self._browser,)


def _build_id(hit: SearchHit) -> int | None:
    if isinstance(hit, RecordSearchHit):
        return hit.build_id or None
    if isinstance(hit, BuildSearchHit) and hit.source_id.isdigit():
        return int(hit.source_id)
    return None


def _record_heading(hit: RecordSearchHit) -> sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
    """Name the record a result matched above the holder build that it opens."""
    return sl.section(
        sl.heading(hit.title),
        sl.note(hit.subtitle) if hit.subtitle else None,
        accent=sl.Tone.SUCCESS,
    )


def _metadata_label(kind: str) -> sl.TextLike:
    match kind:
        case "restriction":
            return tr(t"bot-submission-search-view-restriction = Restriction")
        case "pattern":
            return tr(t"bot-submission-search-view-pattern = Pattern")
        case "showcase":
            return tr(t"bot-submission-search-view-showcase-tag = Showcase tag")
        case "creator":
            return tr(t"bot-submission-search-view-creator = Creator")
        case "version":
            return tr(t"bot-submission-search-view-version = Version")
        case "tag":
            return tr(t"bot-submission-search-view-tag = Tag")
        case _:
            return kind


def _result_title(hit: SearchHit, request: SearchRequest) -> SearchText:
    """Return the heading of a result: in a grouped record search, a build is headed by its best record."""
    if (
        isinstance(hit, BuildSearchHit)
        and request.group_by_build
        and request.scope is SearchScope.RECORDS
        and hit.records
    ):
        record = hit.records[0]
        return record.evidence.highlight(record.title, "title")
    return hit.evidence.highlight(hit.title, "title")


def _heading_subtitle(hit: SearchHit, request: SearchRequest) -> str | None:
    """Return the subtitle of the record heading a grouped record result, if any."""
    if isinstance(hit, BuildSearchHit) and request.group_by_build and request.scope is SearchScope.RECORDS:
        return hit.records[0].subtitle if hit.records else None
    return hit.subtitle if isinstance(hit, RecordSearchHit) else None


def _record_line(record: RecordSearchHit) -> Text:
    """Show a record with its subtitle; many records share a title and differ only by restrictions."""
    subtitle = record.subtitle
    return record_row(
        _title(record), _highlighted(record.evidence.highlight(subtitle, "subtitle")) if subtitle else None
    )


def _title(hit: SearchHit) -> Text:
    return _highlighted(hit.evidence.highlight(hit.title, "title"))


def _match_note(hit: SearchHit) -> Text | None:
    """Say where a hit matched when its heading does not show it, and mark close spellings.

    A holder match is always named, because a record found through its holder's build
    title must not read as a restriction of the record itself.
    """
    evidence = hit.evidence
    shown = [match for match in evidence.matches if match.spans]
    note: Text | None = None
    if (holder := next((match for match in shown if match.holder_build_id is not None), None)) is not None:
        assert holder.holder_build_id is not None
        label = tr(t"search-match-holder = Holder")
        note = Text(t"↳ {label} {build_row(holder.holder_build_id, _excerpt(holder), (None, None, None))}")
    elif not any(match.field in _HEADING_FIELDS for match in shown) and (
        other := next((match for match in shown if match.field not in _HEADING_FIELDS), None)
    ):
        label = DEFAULT_FIELD_REGISTRY.display_name(other.field)
        note = Text(t"↳ {label}: {_excerpt(other)}")
    if evidence.close:
        close = tr(t"search-close-match = close spelling")
        note = Text(t"{note} · {close}") if note is not None else Text(t"↳ {close}")
    return note


_HEADING_FIELDS = frozenset({"title", "subtitle"})
_EXCERPT_CONTEXT = 30
_EXCERPT_LIMIT = 80


def _excerpt(match: FieldMatch) -> Text:
    """Highlight a matched value, cut around its first match when it is long, like a description."""
    text, spans = match.text, match.spans
    if len(text) > _EXCERPT_LIMIT and spans:
        start = max(0, spans[0][0] - _EXCERPT_CONTEXT)
        end = min(len(text), start + _EXCERPT_LIMIT)
        spans = tuple((max(a, start) - start, min(b, end) - start) for a, b in spans if a < end and b > start)
        text = ("…" if start else "") + text[start:end] + ("…" if end < len(match.text) else "")
        if start:
            spans = tuple((a + 1, b + 1) for a, b in spans)
    return _highlighted(SearchText(text, spans))


def _holder_lines(hit: RecordSearchHit) -> tuple[Text, ...]:
    lines: list[Text] = []
    for holder in hit.holders:
        line = build_row(holder.build_id, holder.title, (None, None, None))
        if holder.creators:
            line += " · " + ", ".join(creator.name for creator in holder.creators)
        lines.append(line)
    if not lines and hit.build_id:
        lines.append(build_row(hit.build_id, hit.build_title, (None, None, None)))
    return tuple(lines)


def _result_label(hit: SearchHit, request: SearchRequest) -> str:
    title = _result_title(hit, request).text
    if subtitle := _heading_subtitle(hit, request):
        title = f"{title} · {subtitle}"
    if isinstance(hit, BuildSearchHit) and request.group_by_build:
        # Tied holders can share the same heading; keep their picker labels distinct.
        suffix = f" · #{hit.source_id}"
        return truncate_display_text(title, 80 - len(suffix)) + suffix
    return truncate_display_text(title, 80)


def _highlighted(value: SearchText) -> Text:
    parts: list[sl.TextLike] = []
    position = 0
    for start, end in value.spans:
        parts.extend((value.text[position:start], strong(value.text[start:end])))
        position = end
    parts.append(value.text[position:])
    return Text.join("", parts)


def _result_description(hit: SearchHit, request: SearchRequest) -> sl.TextLike:
    """Describe a result with the same rows as the catalogue, with matched words in bold."""
    if isinstance(hit, BuildSearchHit):
        if request.group_by_build and request.scope is SearchScope.RECORDS and hit.records:
            # The matching-records detail lists every title held by this build.
            record = _record_line(hit.records[0])
            extra = len(hit.records) - 1
            return record + f" (+{extra}) · #{hit.source_id}" if extra else record + f" · #{hit.source_id}"
        build = build_row(hit.source_id, _title(hit), hit.dimensions)
        note = _match_note(hit)
        if not request.group_by_build:
            return Text.join("\n", (build, note)) if note is not None else build
        others = hit.records
        # A build whose only record repeats its title adds nothing.
        if len(others) == 1 and others[0].title == hit.title:
            others = ()
        lines: list[sl.TextLike] = [build]
        if note is not None:
            lines.append(note)
        lines.extend(_record_line(record) for record in others[:2])
        if len(others) > 2:
            count = len(others) - 2
            lines.append(tr(t"search-grouped-more-records = +{count} more records"))
        return Text.join("\n", lines)
    note = _match_note(hit)
    if isinstance(hit, RecordSearchHit):
        return Text.join("\n", (_record_line(hit), *((note,) if note is not None else ()), *_holder_lines(hit)))
    return Text.join("\n", (_title(hit), *((note,) if note is not None else ()), _metadata_label(hit.metadata_kind)))


def _detail_text(hit: SearchHit) -> sl.TextLike:
    if isinstance(hit, RecordSearchHit):
        tags = ""
        build_title = hit.build_title
        build_id = hit.build_id
        criterion = RECORD_CRITERIA[RecordCriterion(hit.criterion)]
        version_scope = VERSION_SCOPES[VersionScope(hit.version_scope)]
        fields = md_tr(
            t"""
            bot-submission-search-view-build-base-title =
                {{ "*" }}*Build**
                {build_title} ({build_id!s})
                {{ "*" }}*Record criterion**
                {criterion} · {version_scope}
            """,
        )
        if hit.metrics:
            metrics = ", ".join(f"{key}: {metric.display}" for key, metric in hit.metrics.items())
            fields = md_tr(
                t"""
                bot-submission-search-view-metrics =
                    {fields}
                    {{ "*" }}*Metrics**
                    {metrics}
                """,
            )
        description = hit.subtitle or ""
    elif isinstance(hit, BuildSearchHit):
        tags = ", ".join(tag for tag in hit.tags)
        status = hit.status
        fields = md_tr(
            t"""
            bot-submission-search-view-status =
                {{ "*" }}*Status**
                {status}
            """,
        )
        description = hit.description or ""
    else:
        tags = ", ".join(alias for alias in hit.aliases)
        kind = _metadata_label(hit.metadata_kind)
        fields = md_tr(
            t"""
            bot-submission-search-view-kind =
                {{ "*" }}*Kind**
                {kind}
            """,
        )
        description = hit.description or ""
    if tags:
        fields = md_tr(
            t"""
            bot-submission-search-view-tags =
                {fields}
                {{ "*" }}*Tags**
                {tags}
            """,
        )
    return md_tr(
        t"""
        bot-submission-search-view-value =
            {description}
            {fields}
        """,
    )
