"""Group semantic children and define disclosure and pagination boundaries."""

from collections.abc import Sequence

from squid_i18n import Text
from squid_ui.authoring.semantic import (
    CLOSED,
    Article,
    Aside,
    Block,
    Cluster,
    Details,
    DisclosureOwnership,
    Group,
    Heading,
    LayoutNode,
    Region,
    Section,
    Stack,
    Summary,
    Themed,
    Tone,
)
from squid_ui.factories._inputs import ChildLike, TextValue, _children
from squid_ui.overflow import FIRST_PAGE, PageOwnership
from squid_ui.palette import INHERIT, Accent, Palette, Role
from squid_ui.target_types import RenderTarget


def group[RenderTargetT = RenderTarget, ContextT = object](
    *children: ChildLike[RenderTargetT, ContextT],
    key: str | None = None,
) -> Group[RenderTargetT, ContextT]:
    """Related content with no layout opinion; lowers to its children in place."""
    return Group(_children(children, "sl.group()"), key=key)


def stack[RenderTargetT = RenderTarget, ContextT = object](
    *children: ChildLike[RenderTargetT, ContextT],
    key: str | None = None,
) -> Stack[RenderTargetT, ContextT]:
    """Content read top to bottom."""
    return Stack(_children(children, "sl.stack()"), key=key)


def cluster[RenderTargetT = RenderTarget, ContextT = object](
    *children: ChildLike[RenderTargetT, ContextT],
) -> Cluster[RenderTargetT, ContextT]:
    """Content read as a set rather than a sequence."""
    return Cluster(_children(children, "sl.cluster()"))


def themed[RenderTargetT = RenderTarget, ContextT = object](
    palette: Palette, *children: ChildLike[RenderTargetT, ContextT]
) -> Themed[RenderTargetT, ContextT]:
    """Apply a presentation palette to one semantic subtree."""
    return Themed(_children(children, "sl.themed()"), palette)


def block[RenderTargetT = RenderTarget, ContextT = object](
    *children: ChildLike[RenderTargetT, ContextT], accent: Accent = INHERIT, key: str | None = None
) -> Block[RenderTargetT, ContextT]:
    """An untitled region; ``accent`` is a house-colour override."""
    return Block(_children(children, "sl.block()"), accent, key)


def section[RenderTargetT = RenderTarget, ContextT = object](
    heading: Heading,
    *children: ChildLike[RenderTargetT, ContextT],
    accent: Accent = INHERIT,
    thumbnail: str | None = None,
    key: str | None = None,
) -> Section[RenderTargetT, ContextT]:
    """A titled block of related content; see `Section` for accent and thumbnail options."""
    return Section(heading, _children(children, "sl.section()"), accent, thumbnail, key)


def article[RenderTargetT = RenderTarget, ContextT = object](
    heading: Heading,
    *children: ChildLike[RenderTargetT, ContextT],
    accent: Accent = INHERIT,
    thumbnail: str | None = None,
) -> Article[RenderTargetT, ContextT]:
    """A self-contained block that stands on its own."""
    return Article(heading, _children(children, "sl.article()"), accent, thumbnail)


def aside[RenderTargetT = RenderTarget, ContextT = object](
    *children: ChildLike[RenderTargetT, ContextT], tone: Tone | Role = Tone.NEUTRAL
) -> Aside[RenderTargetT, ContextT]:
    """Tangential or advisory content, coloured by tone."""
    return Aside(_children(children, "sl.aside()"), tone)


def details[RenderTargetT = RenderTarget, ContextT = object](
    summary: Summary,
    *children: ChildLike[RenderTargetT, ContextT],
    key: str,
    open: DisclosureOwnership[ContextT] = CLOSED,
) -> Details[RenderTargetT, ContextT]:
    """Content the reader expands; ``key`` carries its disclosure state."""
    return Details(key, summary, _children(children, "sl.details()"), open)


def summary(content: TextValue) -> Summary:
    """The control text that identifies a `details` region."""
    content = Text(content)
    return Summary(content)


def region[RenderTargetT = RenderTarget, ContextT = object](
    *children: ChildLike[RenderTargetT, ContextT],
    key: str,
    title: TextValue,
    header: Sequence[LayoutNode[RenderTargetT, ContextT]] = (),
    footer: Sequence[LayoutNode[RenderTargetT, ContextT]] = (),
    page: PageOwnership = FIRST_PAGE,
    page_size: int | None = None,
) -> Region[RenderTargetT, ContextT]:
    """Let the planner allocate space jointly with sibling and nested regions."""
    title = Text(title)
    return Region(_children(children, "sl.region()"), key, title, tuple(header), tuple(footer), page, page_size)
