"""Complete declarative output produced by a component render."""

from collections.abc import Sequence
from dataclasses import dataclass

from squid_layouts.ir import Node


@dataclass(frozen=True, slots=True)
class InlineAsset:
    """Transient bytes suitable only for a session mount."""

    data: bytes


@dataclass(frozen=True, slots=True)
class StoredAsset:
    """Host-resolved asset reference suitable for durable mounts."""

    reference: str


type AssetSource = InlineAsset | StoredAsset


@dataclass(frozen=True, slots=True)
class Asset:
    """A portable file attached to, or offered by, a rendered document."""

    key: str
    name: str
    media_type: str
    source: AssetSource


@dataclass(frozen=True, slots=True)
class Document:
    """Visual nodes and delivery assets derived from one component state snapshot."""

    children: tuple[Node, ...]
    assets: tuple[Asset, ...] = ()


type DocumentLike = Document | Node | Sequence[Node]


def as_document(rendered: DocumentLike) -> Document:
    if isinstance(rendered, Document):
        return rendered
    if isinstance(rendered, Sequence):
        return Document(tuple(rendered))
    return Document((rendered,))
