"""Plan one document as a sequence of messages when it does not fit in one.

A message target budgets one message. A crowded screen can exceed that budget even
though every part of it is legal. plan_spread then cuts the document into consecutive
segments and plans each segment as its own message, with the target's full budget.
Regions inside a segment still paginate as usual.

Complete content wins over fewer messages: the document is first planned without
omitting or shortening anything, as one message and then spread. Only if that fails
do the usual lossy adaptations apply, again as one message first.

Cuts fall between top-level children first. When no such cuts fit, they may also fall
inside a stack, a component placement, or an unordered list that forms one fixed
group, shallower cuts before deeper ones. Each message then carries its own copy of
the containers around its part. No cut follows a child wrapped in `keep_with_next`.

The first cut that works is remembered by the caller as anchors, one per later message:
the key path that identifies the part starting that message. Passing those anchors
back keeps the same parts at the head of the same messages on later renders, so
controls do not move between messages while the reader is using them. Messages merge
again only when an anchor's part disappears.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise, product
from typing import Any

from squid_ui import scene
from squid_ui.authoring.document import Document, DocumentLike, as_document
from squid_ui.authoring.semantic import ComponentPlacement, KeepWithNext, LayoutNode, List, Stack
from squid_ui.errors import LayoutSearchExhaustedError, UnsolvableLayoutError
from squid_ui.planning.cache import PlanCache
from squid_ui.planning.planner import plan_request
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.result import PlanResult
from squid_ui.planning.snapshot import PlanningSnapshot
from squid_ui.presentation_state import ActivePagers, RegionLayoutState, RegionLayoutUpdate, RetainedCompositionUpdate
from squid_ui.target_types import RenderTarget

# Planning failures that a smaller document could avoid. An overflow can also come from
# one oversized child; cutting cannot help then, and the original error is raised.
_DOES_NOT_FIT = (UnsolvableLayoutError, LayoutSearchExhaustedError)

# Above this many candidate cut combinations, balancing aims for an even share of the
# children per message instead of planning every combination.
_BALANCE_COMBINATIONS = 6


@dataclass(frozen=True, slots=True)
class MessageSpread[BodyT: scene.Body = Any]:
    """The messages one document was planned into, in reading order.

    `anchors` has one entry per message after the first. Pass it to the next
    plan_spread call as `retained` to keep the same cuts. An entry is None when the
    part starting that message has no key anywhere inside it; such a cut cannot be
    retained and is searched for again.

    Each message's session_updates hold only writes that concern that message alone.
    `session_updates` on the spread holds the writes that describe all messages
    together; apply both sets once every message has been delivered.
    """

    messages: tuple[PlanResult[BodyT], ...]
    anchors: tuple[str | None, ...] = ()
    session_updates: tuple[Any, ...] = ()


def _anchor(node: object) -> str | None:
    """The first key found in a node, searching the node before its descendants."""
    key = getattr(node, "key", None)
    if isinstance(key, str) and key:
        return key
    for name in ("node", "primary", "content"):
        child = getattr(node, name, None)
        if child is not None and (found := _anchor(child)) is not None:
            return found
    for name in ("children", "header", "footer", "alternates", "choices"):
        children = getattr(node, name, ())
        if isinstance(children, tuple):
            for child in children:
                if (found := _anchor(child)) is not None:
                    return found
    return None


def _parts(node: object) -> tuple[Any, ...] | None:
    """The children a cut may fall between, or None when cuts fall only around the node.

    An ordered list would restart its numbering in the next message, and a list of
    several groups already pages within one message, so only an unordered single group
    is cut.
    """
    if isinstance(node, Stack | ComponentPlacement):
        return node.children or None
    if type(node) is List and not node.ordered and node.group_size is not None:
        return node.items if 0 < len(node.items) <= node.group_size else None
    return None


def _with_parts(node: Any, parts: tuple[Any, ...]) -> Any:
    return replace(node, items=parts) if isinstance(node, List) else replace(node, children=parts)


def _pieces(children: Sequence[Any], path: tuple[int, ...] = ()) -> Iterator[tuple[int, ...]]:
    """The paths, as child indices from the root, of the parts no cut falls inside."""
    for index, child in enumerate(children):
        if (parts := _parts(child)) is not None:
            yield from _pieces(parts, (*path, index))
        else:
            yield (*path, index)


class _Search[BodyT: scene.Body, RenderTargetT, AdapterT]:
    """Plan segments of one document on demand, remembering each outcome.

    A cut at index i starts a message with the i-th uncut part. `legal` holds the cuts the search
    may currently use; `allow` widens it to deeper cuts.
    """

    def __init__(
        self,
        document: Document[RenderTargetT, Any],
        request: PlanRequest[BodyT, RenderTargetT, AdapterT],
        cache: PlanCache | None,
    ) -> None:
        self.document = document
        self.request = request
        self.cache = cache
        self.pieces = tuple(_pieces(document.children))
        self.count = len(self.pieces)
        # A cut's depth is how many containers its two sides share.
        self.depths: dict[int, int] = {}
        for index, (left, right) in enumerate(pairwise(self.pieces), start=1):
            depth = next(level for level, (a, b) in enumerate(zip(left, right, strict=False)) if a != b)
            if not isinstance(self._node(left[: depth + 1]), KeepWithNext):
                self.depths[index] = depth
        self.legal: tuple[int, ...] = ()
        self._planned: dict[tuple[int, int], PlanResult[BodyT] | None] = {}

    def _node(self, path: tuple[int, ...]) -> Any:
        children: Sequence[Any] = self.document.children
        node: Any = None
        for index in path:
            node = children[index]
            children = _parts(node) or ()
        return node

    def anchor(self, cut: int) -> str | None:
        """Name the part a cut starts: the keys of its containers, then its own first key."""
        path = self.pieces[cut][: self.depths[cut] + 1]
        names = []
        for end in range(1, len(path)):
            key = getattr(self._node(path[:end]), "key", None)
            names.append(key if isinstance(key, str) and key else f"#{path[end - 1]}")
        anchor = _anchor(self._node(path))
        return None if anchor is None else "/".join((*names, anchor))

    def allow(self, depth: int) -> None:
        self.legal = tuple(cut for cut, cut_depth in self.depths.items() if cut_depth <= depth)

    def _slice(self, children: Sequence[Any], start: int, end: int, depth: int = 0) -> tuple[Any, ...]:
        """`children` keeping only what the uncut parts start to end cover, at this depth of their paths."""
        kept: list[Any] = []
        while start < end:
            index = self.pieces[start][depth]
            stop = start
            while stop < end and self.pieces[stop][depth] == index:
                stop += 1
            child = children[index]
            if len(self.pieces[start]) > depth + 1:
                parts = _parts(child)
                assert parts is not None
                child = _with_parts(child, self._slice(parts, start, stop, depth + 1))
            kept.append(child)
            start = stop
        return tuple(kept)

    def segment(self, start: int, end: int) -> PlanResult[BodyT] | None:
        """Plan the uncut parts start to end as one message, or return None if it does not fit."""
        span = (start, end)
        if span not in self._planned:
            # A document key asks for root pages under one cursor. Segments would each
            # claim that cursor, so they are planned without it.
            part: Document[RenderTargetT, Any] = Document(
                self._slice(self.document.children, start, end), self.document.assets
            )
            try:
                self._planned[span] = plan_request(part, self.request, cache=self.cache)
            except _DOES_NOT_FIT:
                self._planned[span] = None
        return self._planned[span]

    def fits(self, cuts: Sequence[int]) -> bool:
        bounds = (0, *cuts, self.count)
        return all(self.segment(start, end) is not None for start, end in pairwise(bounds))

    def _furthest_end(self, start: int) -> int | None:
        """The last legal end at which a message starting at `start` still fits.

        Assumes that a segment fits whenever a longer segment with the same start fits.
        """
        ends = [index for index in (*self.legal, self.count) if index > start]
        low, high, best = 0, len(ends) - 1, None
        while low <= high:
            middle = (low + high) // 2
            if self.segment(start, ends[middle]) is not None:
                best = ends[middle]
                low = middle + 1
            else:
                high = middle - 1
        return best

    def _earliest_start(self, end: int) -> int | None:
        starts = [index for index in (0, *self.legal) if index < end]
        low, high, best = 0, len(starts) - 1, None
        while low <= high:
            middle = (low + high) // 2
            if self.segment(starts[middle], end) is not None:
                best = starts[middle]
                high = middle - 1
            else:
                low = middle + 1
        return best

    def fewest(self, max_messages: int) -> tuple[int, ...] | None:
        """Cuts for the fewest messages, each message filled as far as it goes."""
        cuts: list[int] = []
        start = 0
        while True:
            end = self._furthest_end(start)
            if end is None:
                return None
            if end == self.count:
                return tuple(cuts)
            if len(cuts) + 2 > max_messages:
                return None
            cuts.append(end)
            start = end

    def balanced(self, latest: tuple[int, ...]) -> tuple[int, ...]:
        """Move cuts earlier where that leaves the fullest message with more room.

        `latest` fills each message as far as it goes, which leaves the earlier messages
        with no room to grow. Filling from the end instead gives the earliest position of
        each cut; any position between the two keeps the same number of messages.
        """
        earliest: list[int] = []
        end = self.count
        for _ in latest:
            start = self._earliest_start(end)
            if start is None or start == 0:
                return latest
            earliest.append(start)
            end = start
        earliest.reverse()
        ranges = [
            tuple(index for index in self.legal if low <= index <= high)
            for low, high in zip(earliest, latest, strict=True)
        ]
        if any(not candidates for candidates in ranges):
            return latest
        combinations = 1
        for candidates in ranges:
            combinations *= len(candidates)
        if combinations > _BALANCE_COMBINATIONS:
            # Too many to plan each one: aim every cut at an even share of the children.
            share = self.count / (len(ranges) + 1)
            even = tuple(
                min(candidates, key=lambda index, target=share * (number + 1): abs(index - target))
                for number, candidates in enumerate(ranges)
            )
            return even if self._ordered(even) and self.fits(even) else latest
        best, best_fill = latest, self._fill(latest)
        for cuts in product(*ranges):
            if cuts == latest or not self._ordered(cuts) or not self.fits(cuts):
                continue
            # Ties keep the later cut, which leaves more of the screen in the first message.
            if (fill := self._fill(cuts)) < best_fill or (fill == best_fill and cuts > best):
                best, best_fill = cuts, fill
        return best

    @staticmethod
    def _ordered(cuts: Sequence[int]) -> bool:
        return all(first < second for first, second in pairwise(cuts))

    def _fill(self, cuts: Sequence[int]) -> float:
        """How full the fullest message is, as a fraction of its tightest budget."""
        capacities = self.request.target.reserve(self.request.reservation).capacities
        bounds = (0, *cuts, self.count)
        fullest = 0.0
        for start, end in pairwise(bounds):
            result = self.segment(start, end)
            assert result is not None
            for axis, capacity in capacities.items():
                if capacity > 0:
                    fullest = max(fullest, result.cost.get(axis) / capacity)
        return fullest

    def retained(self, anchors: Sequence[str | None]) -> tuple[int, ...] | None:
        """The cuts these anchors name in this document, if they all still apply and fit."""
        if not anchors or any(anchor is None for anchor in anchors):
            return None
        positions = {self.anchor(index): index for index in reversed(tuple(self.depths))}
        cuts = tuple(positions.get(anchor) for anchor in anchors)
        if any(cut is None for cut in cuts):
            return None
        found = tuple(cut for cut in cuts if cut is not None)
        return found if self._ordered(found) and self.fits(found) else None

    def spread(self, cuts: Sequence[int]) -> MessageSpread[BodyT]:
        bounds = (0, *cuts, self.count)
        results = [self.segment(start, end) for start, end in pairwise(bounds)]
        messages = tuple(result for result in results if result is not None)
        assert len(messages) == len(results)
        # These three writes describe the whole display. Applied per message, each would
        # undo the previous message's: the active pagers of one message would forget the
        # cursors of the others.
        pagers: frozenset[str] = frozenset()
        fingerprints: list[str] = []
        allocations: list[Any] = []
        own: list[tuple[Any, ...]] = []
        for message in messages:
            kept: list[Any] = []
            for update in message.session_updates:
                match update:
                    case ActivePagers(keys=keys):
                        pagers |= keys
                    case RegionLayoutUpdate(state=state):
                        fingerprints.append(state.fingerprint)
                        allocations.extend(state.allocations)
                    case RetainedCompositionUpdate():
                        pass
                    case _:
                        kept.append(update)
            own.append(tuple(kept))
        shared: list[Any] = [ActivePagers(pagers)]
        if fingerprints:
            shared.append(RegionLayoutUpdate(RegionLayoutState("+".join(fingerprints), tuple(allocations))))
        # One session retains one allocation for page turns, and it would match only one
        # of these messages. Retain none, so each message is searched again on a turn.
        shared.append(RetainedCompositionUpdate(None))
        return MessageSpread(
            tuple(replace(message, session_updates=updates) for message, updates in zip(messages, own, strict=True)),
            tuple(self.anchor(cut) for cut in cuts),
            tuple(shared),
        )


def plan_spread[RenderTargetT: RenderTarget, AdapterT, BodyT: scene.Body](
    rendered: DocumentLike[RenderTargetT] | LayoutNode | Sequence[LayoutNode],
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    *,
    max_messages: int,
    retained: Sequence[str | None] = (),
    cache: PlanCache | None = None,
    snapshot: PlanningSnapshot[BodyT] | None = None,
) -> MessageSpread[BodyT]:
    """Plan a document as one message, or as up to `max_messages` when one cannot hold it.

    Each pass, first without lost content and then with the request as given, tries the
    retained cuts, then the whole document as one message, then searches for cuts. The
    search uses the fewest messages that fit, then moves cuts so the fullest message
    keeps some room.

    The search assumes that removing parts from a message that fits leaves a message
    that fits. Authored alternatives make costs non-additive, but they do not break this.

    Args:
        max_messages: The most messages the caller can deliver; 1 plans exactly as
            plan_request does.
        retained: The anchors of the previous spread, or empty for a first plan.
        snapshot: Used only for the single-message plans, as plan_request uses it.

    Raises:
        LayoutError: The error from the single-message plan of the request as given,
            when no spread within `max_messages` fits either.
    """
    if max_messages < 1:
        message = "max_messages must be at least 1"
        raise ValueError(message)
    if max_messages == 1:
        return MessageSpread((plan_request(rendered, request, cache=cache, snapshot=snapshot),))
    document = as_document(rendered)
    passes = (
        (request,) if request.strict or request.preserve_content else (replace(request, preserve_content=True), request)
    )
    error: Exception | None = None
    for planned in passes:
        search = _Search(document, planned, cache)
        if (cuts := search.retained(retained)) is not None:
            return search.spread(cuts)
        try:
            return MessageSpread((plan_request(rendered, planned, cache=cache, snapshot=snapshot),))
        except _DOES_NOT_FIT as failure:
            error = failure
        for depth in sorted(set(search.depths.values())):
            search.allow(depth)
            if found := search.fewest(max_messages):
                return search.spread(search.balanced(found))
    assert error is not None
    raise error


__all__ = ["MessageSpread", "plan_spread"]
