"""Generated-preview preparation and outcome read models."""

from dataclasses import dataclass
from enum import StrEnum

from squid.core.i18n import tr
from squid_ui.text import Message


@dataclass(frozen=True, slots=True)
class StoredRender:
    """A persisted, recipe-keyed generated preview."""

    schematic_id: int
    recipe_hash: str
    url: str
    width: int
    height: int
    byte_size: int


class RenderSkipReason(StrEnum):
    """Why a build will never get a generated preview under the current recipe."""

    RENDERING_DISABLED = "rendering_disabled"
    NO_PRIMARY_SCHEMATIC = "no_primary_schematic"
    NOT_SANITIZED = "not_sanitized"
    POISONED_FILE = "poisoned_file"
    OVER_BLOCK_BUDGET = "over_block_budget"
    OVER_VOLUME_BUDGET = "over_volume_budget"
    MISSING_FILE = "missing_file"

    @property
    def description(self) -> Message:
        """A translatable sentence a moderator can be shown verbatim."""
        return _RENDER_SKIP_DESCRIPTIONS[self]


_RENDER_SKIP_DESCRIPTIONS: dict[RenderSkipReason, Message] = {
    RenderSkipReason.RENDERING_DISABLED: tr(t"Schematic previews are not enabled on this instance."),
    RenderSkipReason.NO_PRIMARY_SCHEMATIC: tr(t"This build has no primary schematic to preview."),
    RenderSkipReason.NOT_SANITIZED: tr(t"This schematic has not been sanitized, so it is never rendered."),
    RenderSkipReason.POISONED_FILE: tr(t"This schematic file already crashed the engine on this instance."),
    RenderSkipReason.OVER_BLOCK_BUDGET: tr(t"This schematic has too many blocks to preview."),
    RenderSkipReason.OVER_VOLUME_BUDGET: tr(t"This schematic is too large to preview."),
    RenderSkipReason.MISSING_FILE: tr(t"The stored schematic file is missing, so it cannot be previewed."),
}


@dataclass(frozen=True, slots=True)
class FreshRender:
    """A newly rendered preview awaiting upload by the publication worker."""

    schematic_id: int
    recipe_hash: str
    width: int
    height: int
    png: bytes


@dataclass(frozen=True, slots=True)
class CachedRender:
    """A recipe-matched preview already stored, awaiting publication as a build link."""

    schematic_id: int
    recipe_hash: str
    width: int
    height: int
    url: str


@dataclass(frozen=True, slots=True)
class SkippedRender:
    """A build the renderer will not produce a preview for, and why."""

    reason: RenderSkipReason


@dataclass(frozen=True, slots=True)
class RenderedSchematic:
    """A PNG answered directly to a waiting caller without publication."""

    build_id: int
    schematic_id: int
    recipe_hash: str
    width: int
    height: int
    png: bytes
    from_cache: bool


type RenderPreparation = FreshRender | CachedRender | SkippedRender
"""The explicit fresh, cached, or permanent-skip result of preview preparation."""
