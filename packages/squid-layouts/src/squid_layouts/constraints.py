"""Overflow policies: what a node gives up when the message budget runs out.

Nodes declare intent, not sizes — the solver measures chrome, allocates Discord's shared
budgets by priority, and applies the node's policy only when its content does not fit.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


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

    Splits at ``boundary`` where possible, hard-splitting single oversized segments. One
    Paginate node per document; extras degrade to Truncate with a note. ``initial`` picks the
    page first shown — "end" suits content whose interesting part is its tail, like a
    traceback whose failing frame is the last one.

    ``per`` switches to count-based pages: a `Lines` node paginates every ``per`` entries
    whether or not the budget is tight, which is the "10 results per page" pin a list command
    wants. A count-page too large for the budget is split further, so a page always fits.
    ``footer`` overrides `Chrome.page_footer` for this node — how a list keeps "Page 1 of 3 ·
    40 in total" while the rest of the framework says "Page 1 of 3".
    """

    boundary: str = "\n"
    initial: Literal["start", "end"] = "start"
    per: int | None = None
    footer: Callable[[int, int], str] | None = None

    def __post_init__(self) -> None:
        if self.per is not None and self.per < 1:
            message = "Paginate(per=...) must be at least 1"
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
    """A degradation ladder: fallbacks tried in order when the node's content does not fit.

    Semantically-aware shrinking beats mid-string trimming: `[all links] → [count + first
    link] → [count]` degrades meaningfully where an ellipsis would leave `https://exampl…`.
    The node's own content is the preferred form; the last fallback that still does not fit
    is ellipsis-trimmed as the final resort. Validated at construction: at least one step,
    no empty steps, non-increasing lengths.
    """

    ladder: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_ladder(self.ladder, of="Alts ladder")


def alts(*ladder: str) -> Alts:
    return Alts(ladder=ladder)


@dataclass(frozen=True, slots=True)
class Alt:
    """One list entry with its degradation ladder: a primary form plus validated fallbacks.

    Used as a `Lines` entry. Under budget pressure the solver steps the largest entries down
    their fallbacks before it spills any entry whole.
    """

    primary: str
    fallbacks: tuple[str, ...] = ()

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
class Drop:
    """Omit the node entirely rather than show it shortened."""


@dataclass(frozen=True, slots=True)
class Never:
    """Shrinking this node is a bug: overflow raises in strict mode."""


type Overflow = Truncate | Spill | Paginate | Alts | Drop | Never
