"""Read models returned by the schematic store."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from whenever import Instant

from squid.core.errors import DataIntegrityError, JSONValue
from squid.core.i18n import tr
from squid.schematics.domain.models import (
    SchematicAnalysis,
    SchematicFormat,
    SchematicLicense,
    SchematicVisibility,
    SimulationResult,
)
from squid_ui.text import Message

type DuplicateTier = Literal["identical", "structural-match", "near"]


@dataclass(frozen=True, slots=True)
class SchematicPublication:
    """Rights, sanitization, and withdrawal facts governing an attachment."""

    visibility: SchematicVisibility = SchematicVisibility.LEGACY_UNVERIFIED
    license: SchematicLicense | None = None
    rights_attested_at: Instant | None = None
    rights_attested_by_account_id: int | None = None
    sanitized_at: Instant | None = None
    sanitizer_version: str | None = None
    sanitization_report: dict[str, JSONValue] | None = None
    published_at: Instant | None = None
    withdrawn_at: Instant | None = None

    def __post_init__(self) -> None:
        rights_complete = (
            self.license is not None
            and self.rights_attested_at is not None
            and self.rights_attested_by_account_id is not None
        )
        if self.visibility is SchematicVisibility.PUBLIC_DOWNLOAD and not rights_complete:
            msg = tr(t"public schematic downloads require a license and rights attestation")
            raise DataIntegrityError(msg)
        sanitization_parts = (self.sanitized_at, self.sanitizer_version, self.sanitization_report)
        if any(part is not None for part in sanitization_parts) and any(part is None for part in sanitization_parts):
            msg = tr(t"sanitization requires a timestamp, sanitizer version, and audit report")
            raise DataIntegrityError(msg)

    @property
    def is_sanitized(self) -> bool:
        """Whether a format-aware sanitizer completed successfully."""
        return (
            self.sanitized_at is not None
            and self.sanitizer_version is not None
            and self.sanitization_report is not None
        )

    @property
    def is_public_downloadable(self) -> bool:
        """Whether public callers may receive the stored canonical bytes."""
        return (
            self.visibility is SchematicVisibility.PUBLIC_DOWNLOAD
            and self.license is not None
            and self.rights_attested_at is not None
            and self.rights_attested_by_account_id is not None
            and self.published_at is not None
            and self.withdrawn_at is None
            and self.is_sanitized
        )


@dataclass(frozen=True, slots=True)
class StoredSchematic:
    """A schematic file that has been analyzed and attached to a build."""

    id: int
    build_id: int
    file_sha256: str
    is_primary: bool
    original_filename: str | None
    analysis: SchematicAnalysis
    publication: SchematicPublication = SchematicPublication()
    simulation_evidence: SimulationResult | None = None


@dataclass(frozen=True, slots=True)
class PublicSchematicDownload:
    """Sanitized bytes plus everything a download response must state.

    The route used to receive `(content, stored)` and then `assert
    publication.license is not None` to satisfy the type checker -- restating an
    invariant `SchematicPublication` already enforces, in a layer where
    assertions are enabled only by convention. Carrying the license and the
    stored container format here means the response has no facts left to derive.
    """

    content: bytes
    schematic: StoredSchematic
    license: SchematicLicense
    source_format: SchematicFormat


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    """A previously submitted build whose schematic resembles the one under review."""

    build_id: int
    schematic_id: int
    tier: DuplicateTier
    footprint_distance: float
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class StoredRender:
    """A persisted, recipe-keyed rendered preview."""

    schematic_id: int
    recipe_hash: str
    url: str
    width: int
    height: int
    byte_size: int


class RenderSkipReason(StrEnum):
    """Why a build will never get a preview under the current recipe.

    Every member is a *permanent* outcome for this recipe: the durable render queue
    acknowledges it rather than retrying, and a moderator can be told what happened.
    Operational failures — a dead worker, an unreachable resource pack, a renderer that
    returns something that is not a PNG — stay exceptions so the queue retries them.
    """

    RENDERING_DISABLED = "rendering_disabled"
    NO_PRIMARY_SCHEMATIC = "no_primary_schematic"
    NOT_SANITIZED = "not_sanitized"
    POISONED_FILE = "poisoned_file"
    OVER_BLOCK_BUDGET = "over_block_budget"
    OVER_VOLUME_BUDGET = "over_volume_budget"
    MISSING_FILE = "missing_file"

    @property
    def description(self) -> Message:
        """A translatable sentence a moderator can be shown verbatim.

        Deliberately says nothing about the engine, the adapter, or the cap's value: the
        numbers are deployment configuration, and the engine's own vocabulary is not
        something a moderator can act on.
        """
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
    """A newly rendered preview awaiting upload by the transport layer."""

    schematic_id: int
    recipe_hash: str
    width: int
    height: int
    png: bytes


@dataclass(frozen=True, slots=True)
class CachedRender:
    """A recipe-matched preview already in object storage, awaiting projection."""

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
    """A PNG answered to a caller who asked for it and is waiting for it.

    Separate from `FreshRender` because nothing here is on its way to being published: the
    bytes travel to one Discord message or one HTTP response, and `from_cache` only says
    whether a GPU was involved, which is what a log line or a header wants to know.
    """

    build_id: int
    schematic_id: int
    recipe_hash: str
    width: int
    height: int
    png: bytes
    from_cache: bool


type RenderPreparation = FreshRender | CachedRender | SkippedRender
"""What `SchematicService.prepare_render` decided.

Three explicit states rather than `PreparedRender | None`: the old shape collapsed "disabled",
"no attachment", "unsanitized", "poisoned", "over budget", "file gone", "already rendered", and
"just rendered" into one value, so neither the durable worker nor a moderator-facing surface
could tell a permanent skip from an absent attachment.
"""
