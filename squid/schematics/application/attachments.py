"""Attachment and publication read models."""

from dataclasses import dataclass

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
    """Sanitized bytes plus the typed facts required by a download response."""

    content: bytes
    schematic: StoredSchematic
    license: SchematicLicense
    source_format: SchematicFormat
