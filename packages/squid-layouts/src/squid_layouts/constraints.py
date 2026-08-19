"""Overflow policies: what a node gives up when the message budget runs out.

Nodes declare intent, not sizes — the solver measures chrome, allocates Discord's shared
budgets by priority, and applies the node's policy only when its content does not fit.
"""

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
    """Split overflowing content into pages; the mount adds nav controls and a page footer.

    Splits at ``boundary`` where possible, hard-splitting single oversized segments. One
    Paginate node per document; extras degrade to Truncate with a note.
    """

    boundary: str = "\n"


@dataclass(frozen=True, slots=True)
class Alts:
    """A degradation ladder: alternates tried in order when the node's content does not fit.

    Semantically-aware shrinking beats mid-string trimming: `[all links] → [count + first
    link] → [count]` degrades meaningfully where an ellipsis would leave `https://exampl…`.
    The node's own content is the preferred form; the last alternate that still does not fit
    is ellipsis-trimmed as the final fallback.
    """

    ladder: tuple[str, ...]


def alts(*ladder: str) -> Alts:
    return Alts(ladder=ladder)


@dataclass(frozen=True, slots=True)
class Drop:
    """Omit the node entirely rather than show it shortened."""


@dataclass(frozen=True, slots=True)
class Never:
    """Shrinking this node is a bug: overflow raises in strict mode."""


type Overflow = Truncate | Spill | Paginate | Alts | Drop | Never
