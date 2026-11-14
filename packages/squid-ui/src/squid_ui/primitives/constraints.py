"""Overflow policies: what a node gives up when the message budget runs out.

Nodes declare intent, not sizes — the solver measures chrome, allocates Discord's shared
budgets by priority, and applies the node's policy only when its content does not fit.

`Paginate` also needs to know who keeps its visible page, so the page values it shares with
semantic `Paged` and `List` live here: `PageOwnership`, its defaults, `PageEvent` and `RoutedPage`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from squid_ui.interactions import ActionEvent
from squid_ui.ownership import Controlled, Ownership, Uncontrolled
from squid_ui.text import TextLike

# --- Who keeps a pager's page ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PageEvent[ContextT = object](ActionEvent[ContextT]):
    """The reader asked a controlled pager to show another page."""

    page: int = 0
    """The requested zero-based page, already clamped to the pages that exist."""


@dataclass(frozen=True, slots=True)
class RoutedPage:
    """An application-owned page whose navigation uses persistent route IDs.

    `route` receives a clamped zero-based target page. `seek_route`, when supplied,
    returns one input route for a jump menu whose submitted value is the target page.
    The host validates input and owns authorization, state persistence, and redraws.

    Planning may call these factories repeatedly, including for discarded candidates.
    They must be safe to repeat; prefer deterministic encoding without registration.
    Only the returned IDs go into the scene, so no Python callback survives delivery.
    """

    value: int
    route: Callable[[int], str]
    seek_route: Callable[[], str] | None = None
    jump_route: Callable[[int, int], str] | None = None
    """Encode a form-opening button from the resolved zero-based page and page count.

    The host opens ``page_jump_form(page, pages)`` and validates its submission
    against current data before seeking. This is separate from the select input route.
    """


type PageOwnership[ContextT = object] = Ownership[int, PageEvent[ContextT]] | RoutedPage
"""Who keeps a pager's zero-based page.

A negative page counts from the end, as Python indexing does: `-1` is the last page. The
planner clamps any value to the pages the content actually has, because a pager's page
count is only known after layout. `RoutedPage` supplies an application-owned value
with persistent navigation routes instead of an `on_change` callback.
"""

FIRST_PAGE: PageOwnership = Uncontrolled(0)
LAST_PAGE: PageOwnership = Uncontrolled(-1)
"""Open on the final page, for content read from the bottom such as a traceback or log."""


def resolve_page(page: int, extent: int) -> int:
    """Clamp a requested page, counting negative pages from the end, to ``extent`` pages."""
    if page < 0:
        page += extent
    return max(0, min(page, extent - 1))


# --- Overflow policies -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Truncate:
    """Cut content to its grant, marking the cut with an ellipsis."""

    keep: Literal["head", "tail"] = "head"


@dataclass(frozen=True, slots=True)
class Spill:
    """Keep the lines that fit, then one "…and N more" chrome line. Lines nodes only."""


@dataclass(frozen=True, slots=True)
class Paginate:
    """Split content into pages with navigation controls and a page footer.

    `boundary` chooses preferred split points; oversized segments are split to fit.
    Supply a stable `key` when planning so this paginator has independent state.

    `page` says who keeps the visible page. By default the runtime remembers it and opens
    on the first page; `LAST_PAGE` opens on the final page instead, useful for a traceback
    or activity log. `Controlled(page, on_change)` lets the application keep the page, for
    example to return to the first page when a filter changes. A controlled pager needs an
    explicit `key`, and its page is clamped to the pages the content has.

    `RoutedPage(value, route, seek_route)` supplies the same page through persistent routes.

    For `Lines`, `per` groups that many entries per page even when space remains.
    Pages that exceed the text budget are split further. `footer` receives the
    displayed page number and total pages and replaces this node's default footer.

    `min_fill` is a preferred character count on non-final pages. `widows` is the
    preferred minimum number of fragments on the final page. Both are soft goals:
    fitting within the page budget takes precedence.
    """

    key: str | None = None
    boundary: str = "\n"
    page: PageOwnership = FIRST_PAGE
    per: int | None = None
    footer: Callable[[int, int], TextLike] | None = None
    min_fill: int = 0
    widows: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.page, Controlled | RoutedPage) and not self.key:
            message = "an application-owned Paginate needs a key"
            raise ValueError(message)
        if self.per is not None and self.per < 1:
            message = "Paginate(per=...) must be at least 1"
            raise ValueError(message)
        if self.min_fill < 0:
            message = "Paginate(min_fill=...) must not be negative"
            raise ValueError(message)
        if self.widows < 1:
            message = "Paginate(widows=...) must be at least 1"
            raise ValueError(message)


def _validate_ladder(steps: tuple[str, ...], *, of: str = "ladder") -> None:
    if not steps:
        message = f"{of} needs at least one step"
        raise ValueError(message)
    if any(not step for step in steps):
        message = f"{of} steps must be non-empty strings"
        raise ValueError(message)
    lengths = [len(step) for step in steps]
    if lengths != sorted(lengths, reverse=True):
        message = f"{of} steps must not grow: each fallback should be no longer than the one before it"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Alts:
    """Fallback text tried in order when the node's preferred content does not fit.

    For example, a link list can fall back to a count and one link, then just a
    count. If no fallback fits, the final one is ellipsis-trimmed. Construction
    requires one or more nonempty steps with non-increasing character lengths.
    """

    ladder: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_ladder(self.ladder, of="Alts ladder")


def alts(*ladder: str) -> Alts:
    """Build a validated text degradation ladder from preferred to shortest."""
    return Alts(ladder=ladder)


@dataclass(frozen=True, slots=True)
class Alt:
    """One list entry with its degradation ladder: a primary form plus validated fallbacks.

    Used as a `Lines` entry. Under budget pressure the solver steps the largest entries down
    their fallbacks before it spills any entry whole. ``priority`` decides what disappears
    when stepping is not enough: the lowest-priority entries spill first, ties from the tail.
    Plain string entries are priority 0.
    """

    primary: str
    fallbacks: tuple[str, ...] = ()
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.primary:
            message = "Alt primary must be a non-empty string"
            raise ValueError(message)
        if self.fallbacks:
            _validate_ladder(self.fallbacks, of="Alt fallbacks")
            if len(self.fallbacks[0]) > len(self.primary):
                message = "Alt fallbacks must be no longer than the primary form"
                raise ValueError(message)

    @property
    def steps(self) -> tuple[str, ...]:
        return (self.primary, *self.fallbacks)


@dataclass(frozen=True, slots=True)
class Condense:
    """Shorten `Lines` entries through their `Alt` fallbacks before trimming the block.

    Entries are retained while their ladders can make the block fit. Once every
    useful fallback is exhausted, the joined text is ellipsis-trimmed, which may
    hide trailing entries. Plain strings have no fallbacks and go straight to that
    final trim when space is insufficient.

    Like `Never`, this policy reserves its content's fixed cost before flexible
    nodes receive space. Its ladders engage when that fixed share itself exceeds
    the available budget.
    """


@dataclass(frozen=True, slots=True)
class Drop:
    """Omit the node entirely rather than show it shortened."""


@dataclass(frozen=True, slots=True)
class Never:
    """Reserve the full text cost and report any forced shortening.

    Strict planning rejects that degradation. Non-strict measurement may trim the
    content or drop it when even its formatting cannot fit.
    """


type Overflow = Truncate | Spill | Paginate | Alts | Condense | Drop | Never
