"""Minecraft-version representations."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from squid.core.sentinel import UNSET
from squid.core.versions.domain import Edition, VersionCorrection, VersionEntry


class VersionDetail(BaseModel):
    """One recognized Minecraft release."""

    model_config = ConfigDict(extra="forbid")

    id: int
    edition: Edition
    major: int
    minor: int
    patch: int
    display_name: str
    data_version: int | None
    """The world-format number this release writes, or null when none is recorded."""

    @classmethod
    def from_domain(cls, entry: VersionEntry) -> Self:
        version = entry.version
        return cls(
            id=entry.id,
            edition=version.edition,
            major=version.major,
            minor=version.minor,
            patch=version.patch,
            display_name=str(version),
            data_version=entry.data_version,
        )


class VersionCreateRequest(BaseModel):
    """A release to add, written the way announcements write it, such as `1.21.4`."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=100)
    edition: Edition | None = None
    """Overrides the edition named in `version`, which defaults to Java."""


class VersionCorrectionRequest(BaseModel):
    """Omitted fields stay unchanged. A null `data_version` clears it."""

    model_config = ConfigDict(extra="forbid")

    edition: Edition | None = None
    major: int | None = Field(default=None, ge=0, le=32767)
    minor: int | None = Field(default=None, ge=0, le=32767)
    patch: int | None = Field(default=None, ge=0, le=32767)
    data_version: int | None = Field(default=None, ge=0, le=2147483647)

    def to_domain(self) -> VersionCorrection:
        present = self.model_fields_set
        return VersionCorrection(
            edition=self.edition if "edition" in present and self.edition is not None else UNSET,
            major=self.major if "major" in present and self.major is not None else UNSET,
            minor=self.minor if "minor" in present and self.minor is not None else UNSET,
            patch=self.patch if "patch" in present and self.patch is not None else UNSET,
            data_version=self.data_version if "data_version" in present else UNSET,
        )
