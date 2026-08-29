"""Complete declarative output produced by a component render."""

from collections.abc import Sequence
from dataclasses import dataclass

from squid_layouts.assets import Asset, AssetSource, InlineAsset, StoredAsset
from squid_layouts.semantic import LayoutNode

__all__ = ["Asset", "AssetSource", "Document", "DocumentLike", "InlineAsset", "StoredAsset", "as_document"]


@dataclass(frozen=True, slots=True)
class Document:
    """Visual nodes and delivery assets derived from one component state snapshot."""

    children: tuple[LayoutNode, ...]
    assets: tuple[Asset, ...] = ()
    key: str | None = None


type DocumentLike = Document | LayoutNode | Sequence[LayoutNode]


def as_document(rendered: DocumentLike) -> Document:
    if isinstance(rendered, Document):
        return rendered
    if isinstance(rendered, Sequence):
        return Document(tuple(rendered))
    return Document((rendered,))
