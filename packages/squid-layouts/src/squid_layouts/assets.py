"""Portable asset values shared by documents and semantic controls."""

from dataclasses import dataclass


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
