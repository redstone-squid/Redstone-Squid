"""Public Minecraft-version representations."""

from pydantic import BaseModel, ConfigDict

from squid.versions.domain import MinecraftVersion


class VersionDetail(BaseModel):
    """One recognized Minecraft release."""

    model_config = ConfigDict(extra="forbid")

    edition: str
    major: int
    minor: int
    patch: int
    display_name: str

    @classmethod
    def from_domain(cls, version: MinecraftVersion) -> VersionDetail:
        return cls(
            edition=version.edition,
            major=version.major,
            minor=version.minor,
            patch=version.patch,
            display_name=str(version),
        )
