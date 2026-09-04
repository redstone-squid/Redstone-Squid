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
    """Location the target addresses directly; the Slack renderer requires a public HTTPS URL here."""


type AssetSource = InlineAsset | StoredAsset


@dataclass(frozen=True, slots=True)
class Asset:
    """A portable file attached to, or offered by, a rendered document."""

    key: str
    """Identity the planner dedupes by; one key naming two unequal assets is a `LayoutInvariantError`."""
    name: str
    """Filename shown to the reader."""
    media_type: str
    source: AssetSource
