"""Complete declarative output produced by a component render."""

from collections.abc import Sequence
from dataclasses import dataclass

from squid_ui.assets import Asset, AssetSource, InlineAsset, StoredAsset
from squid_ui.errors import LayoutInvariantError
from squid_ui.semantic import LayoutNode
from squid_ui.target_types import RenderTarget

__all__ = ["Asset", "AssetSource", "Document", "DocumentLike", "InlineAsset", "StoredAsset", "as_document"]


@dataclass(frozen=True, slots=True)
class Document[RenderTargetT = RenderTarget]:
    """Visual nodes and delivery assets derived from one component state snapshot."""

    children: tuple[LayoutNode[RenderTargetT], ...]
    assets: tuple[Asset, ...] = ()
    key: str | None = None


type DocumentLike[RenderTargetT = RenderTarget] = (
    Document[RenderTargetT] | LayoutNode[RenderTargetT] | Sequence[LayoutNode[RenderTargetT]]
)
type PortableDocumentLike = DocumentLike[RenderTarget]


def as_document[RenderTargetT](rendered: DocumentLike[RenderTargetT]) -> Document[RenderTargetT]:
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
