"""Permission pattern parsing, matching and specificity.

A *node* is a concrete capability (`build.submission.approve`). A *pattern* is what
a grant or a role definition actually stores, and selects zero or more nodes:

| Pattern                 | Selects                                                    |
|-------------------------|------------------------------------------------------------|
| `build.submission.edit` | that node exactly                                           |
| `build.*`               | exactly one further segment, so not `build.schematic.info`   |
| `build.**`              | one or more further segments, at any depth                  |
| `build.*.view`          | the `view` verb across every build resource                 |
| `**`                    | every node                                                  |
| `@destructive`          | every node carrying that tag                                |

Matching is structural and always evaluated against the live catalogue, so a
pattern never stores an expansion of itself. That is what lets a node added
tomorrow fall under a wildcard granted today without a re-grant or a migration.

Mid-string globs (`build.re*`) are deliberately unsupported: they match on how a
name happens to be spelled rather than on the tree, and silently capture any
future node sharing the prefix.
"""

import re
from dataclasses import dataclass
from typing import Self

from squid.permissions.domain.models import InvalidPatternError, PermissionNode, Tag

SEGMENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
"""A literal name segment. Lowercase, so patterns are unambiguous to type."""

MAX_SEGMENTS = 5
"""Depth ceiling. The convention is `<domain>.<resource>.<verb>`; the two spare
levels are for resources that genuinely nest, and the cap catches typos that
would otherwise register an unreachable node."""

ANY = "*"
"""Matches exactly one segment."""

SUBTREE = "**"
"""Matches one or more trailing segments. Only valid as the final segment."""

type Specificity = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class Pattern:
    """A parsed node selector.

    Either a tag selector (`tag` set, `segments` empty) or a segment pattern
    (`segments` non-empty, `tag` unset).
    """

    raw: str
    segments: tuple[str, ...] = ()
    tag: Tag | None = None

    def __str__(self) -> str:
        return self.raw

    @classmethod
    def parse(cls, raw: str) -> Self:
        """Parse a pattern, raising `InvalidPatternError` if it is malformed."""
        text = raw.strip()
        if not text:
            msg = "A permission pattern cannot be empty."
            raise InvalidPatternError(msg)

        if text.startswith("@"):
            try:
                tag = Tag(text[1:])
            except ValueError:
                known = ", ".join(f"@{member.value}" for member in Tag)
                msg = f"Unknown permission tag {text!r}. Known tags: {known}."
                raise InvalidPatternError(msg) from None
            return cls(raw=text, tag=tag)

        segments = tuple(text.split("."))
        if len(segments) > MAX_SEGMENTS:
            msg = f"Permission pattern {text!r} is deeper than {MAX_SEGMENTS} segments."
            raise InvalidPatternError(msg)
        for index, segment in enumerate(segments):
            if segment == SUBTREE:
                if index != len(segments) - 1:
                    msg = f"'{SUBTREE}' may only be the last segment, but {text!r} continues after it."
                    raise InvalidPatternError(msg)
                continue
            if segment == ANY:
                continue
            if not SEGMENT_PATTERN.match(segment):
                msg = f"Invalid segment {segment!r} in permission pattern {text!r}."
                raise InvalidPatternError(msg)
        return cls(raw=text, segments=segments)

    @property
    def is_tag(self) -> bool:
        """Whether this pattern selects by tag rather than by tree position."""
        return self.tag is not None

    def matches(self, node: PermissionNode) -> bool:
        """Whether this pattern selects `node`."""
        if self.tag is not None:
            return self.tag in node.tags
        leaf = node.segments
        for index, segment in enumerate(self.segments):
            if segment == SUBTREE:
                # Trailing by construction, and requires at least one segment to
                # consume, so `build.**` does not select a bare `build` node.
                return len(leaf) > index
            if index >= len(leaf):
                return False
            if segment != ANY and segment != leaf[index]:
                return False
        return len(leaf) == len(self.segments)

    @property
    def specificity(self) -> Specificity:
        """How narrow a claim this pattern makes, comparable and total.

        Ordered descending, so the maximum is the most specific pattern. The
        tiers are: patterns naming at least one real segment, then tag selectors,
        then patterns that name nothing at all (`**`, `*`). A tag sits below any
        concrete name because it cuts across the tree, but above a bare wildcard
        because it still excludes something.
        """
        if self.tag is not None:
            return (1, 0, 1, 0)
        literals = sum(1 for segment in self.segments if segment not in (ANY, SUBTREE))
        tier = 2 if literals else 0
        return (tier, literals, 0 if SUBTREE in self.segments else 1, len(self.segments))
