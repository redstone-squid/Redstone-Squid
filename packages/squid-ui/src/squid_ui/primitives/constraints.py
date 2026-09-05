"""Overflow policies: what a node gives up when the message budget runs out.

Nodes declare intent, not sizes — the solver measures chrome, allocates Discord's shared
budgets by priority, and applies the node's policy only when its content does not fit.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from squid_ui.text import TextLike


@dataclass(frozen=True, slots=True)
class Truncate:
    """Cut content to its grant, marking the cut with an ellipsis."""

    keep: Literal["head", "tail"] = "head"


@dataclass(frozen=True, slots=True)
class Spill:
    """Keep the lines that fit, then one "…and N more" chrome line. Lines nodes only."""


@dataclass(frozen=True, slots=True)
class Paginate:
    """Split content into pages; the solver adds nav controls and a page footer.

    Splits at `boundary` where possible, hard-splitting single oversized segments. Raises
    `ValueError` at construction for `per < 1`, `min_fill < 0` or `widows < 1`; the planner raises
    `ValueError` when two paginators in one document share a key.
    """

    key: str | None = None
    """Names this paginator's page state. `None` defaults to `page{N}` by position in the document, which
    shifts when a node is added before it; give a key to any paginator whose page must survive re-renders."""
    boundary: str = "\n"
    initial: Literal["start", "end"] = "start"
    """`"end"` opens on the last page, for content whose interesting part is its tail (a traceback)."""
    per: int | None = None
    """Count-based pages: a `Lines` node breaks every `per` entries whether or not the budget is tight, the
    "10 results per page" pin. A count page too large for the budget is split further. Ignored, with a
    `PAGINATE_PER_FALLBACK` note, on any node but `Lines`."""
    footer: Callable[[int, int], TextLike] | None = None
    """`(page, pages)` to footer text, overriding `Chrome.page_footer` for this node only."""
    min_fill: int = 0
    """Characters a page should reach before breaking; a preference the breaker minimizes violations of."""
    widows: int = 1
    """Fewest entries the last page should hold; a preference, like `min_fill`, weighed before page count."""

    def __post_init__(self) -> None:
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
    """Whole-node fallbacks tried in order when the content does not fit; the last is ellipsis-trimmed if it must.

    `[all links] → [count + first link] → [count]` degrades meaningfully where an ellipsis would
    leave `https://exampl…`. Raises `ValueError` for an empty ladder, an empty step, or a step
    longer than the one before it.
    """

    ladder: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_ladder(self.ladder, of="Alts ladder")


def alts(*ladder: str) -> Alts:
    """`Alts` from positional steps; raises `ValueError` under the same rules."""
    return Alts(ladder=ladder)


@dataclass(frozen=True, slots=True)
class Alt:
    """One `Lines` entry with its own fallback ladder.

    Under pressure the solver steps the largest entries down their fallbacks before it spills any
    entry whole. Raises `ValueError` for an empty `primary`, a fallback ladder that fails the
    `Alts` rules, or a first fallback longer than `primary`.
    """

    primary: str
    fallbacks: tuple[str, ...] = ()
    priority: int = 0
    """Which entries spill when stepping is not enough: lowest first, ties from the tail. Plain strings are 0."""

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
        """The full ladder, `primary` first."""
        return (self.primary, *self.fallbacks)


@dataclass(frozen=True, slots=True)
class Condense:
    """Step every `Lines` entry down its own `Alt` ladder, never dropping one.

    The policy for a block that may get shorter but may not get *smaller*: a labelled
    field list keeps every field, at whatever rung of its ladder still fits. Entries with
    no ladder cannot shrink, so a `Lines` of plain strings degrades to `Never`'s
    behaviour — the joined result is ellipsis-trimmed once every ladder is exhausted.

    Like `Never`, a condensing node is charged as a fixed cost before flexible nodes see
    the budget, so a long neighbouring paragraph cannot starve it. The corollary is that
    the ladders only engage when the fixed share itself overdraws the message.
    """


@dataclass(frozen=True, slots=True)
class Drop:
    """Omit the node entirely rather than show it shortened."""


@dataclass(frozen=True, slots=True)
class Never:
    """Shrinking this node is a bug: overflow raises in strict mode."""


type Overflow = Truncate | Spill | Paginate | Alts | Condense | Drop | Never
