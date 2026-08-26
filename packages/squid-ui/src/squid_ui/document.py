"""Complete declarative output produced by a component render."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from squid_ui.assets import Asset, AssetSource, InlineAsset, StoredAsset
from squid_ui.semantic import LayoutNode

__all__ = ["Asset", "AssetSource", "Document", "DocumentLike", "InlineAsset", "StoredAsset", "as_document"]


@dataclass(frozen=True, slots=True)
class Document[ModeT = Any]:
    """Visual nodes and delivery assets derived from one component state snapshot."""

    children: tuple[LayoutNode[ModeT], ...]
    assets: tuple[Asset, ...] = ()
    key: str | None = None


type DocumentLike[ModeT = Any] = Document[ModeT] | LayoutNode[ModeT] | Sequence[LayoutNode[ModeT]]


def as_document[ModeT](rendered: DocumentLike[ModeT]) -> Document[ModeT]:
    if isinstance(rendered, Document):
        return rendered
    if isinstance(rendered, Sequence):
        return Document(tuple(rendered))
    return Document((rendered,))
