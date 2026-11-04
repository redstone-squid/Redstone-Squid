"""Group layout nodes and their assets before planning for a target."""

from collections.abc import Sequence
from dataclasses import dataclass

from squid_ui.assets import Asset, AssetSource, InlineAsset, StoredAsset
from squid_ui.errors import LayoutInvariantError
from squid_ui.semantic import LayoutNode
from squid_ui.target_types import RenderTarget

__all__ = ["Asset", "AssetSource", "Document", "DocumentLike", "InlineAsset", "StoredAsset", "as_document"]


@dataclass(frozen=True, slots=True)
class Document[RenderTargetT = RenderTarget, ContextT = object]:
    """The layout to display, together with the files that layout needs.

    A component can return a Document when it needs to declare assets as well as
    content. Returning a node or a sequence of nodes is shorthand for a document
    without separate asset declarations; as_document normalizes those forms before planning.
    """

    children: tuple[LayoutNode[RenderTargetT, ContextT], ...]
    assets: tuple[Asset, ...] = ()
    key: str | None = None


type DocumentLike[RenderTargetT = RenderTarget, ContextT = object] = (
    Document[RenderTargetT, ContextT]
    | LayoutNode[RenderTargetT, ContextT]
    | Sequence[LayoutNode[RenderTargetT, ContextT]]
)
type PortableDocumentLike = DocumentLike[RenderTarget]


def as_document[RenderTargetT, ContextT](
    rendered: DocumentLike[RenderTargetT, ContextT],
) -> Document[RenderTargetT, ContextT]:
    """Normalize one node or node sequence into a complete document."""
    if isinstance(rendered, Document):
        return rendered
    if isinstance(rendered, str | bytes):
        # A string is a Sequence, so the branch below would take one apart into a node per
        # character and plan it. Text is not a render result; the factories promote it.
        message = "render() returned text; wrap it in a node, e.g. `return sl.paragraph(...)`"
        raise LayoutInvariantError(message)
    if isinstance(rendered, Sequence):
        return Document(tuple(rendered))
    return Document((rendered,))
