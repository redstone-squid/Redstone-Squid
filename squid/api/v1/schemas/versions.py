"""Public Minecraft-version representations."""

from pydantic import ConfigDict, Field

from squid.api.schema import ApiSchema
from squid.versions.domain import MinecraftVersion


class VersionDetail(ApiSchema):
    """One recognized Minecraft release."""

    model_config = ConfigDict(extra="forbid")

    edition: str = Field(description="`Java` or `Bedrock`.")
    major: int
    minor: int
    patch: int = Field(description="Zero for a release written without one, such as `1.20`.")
    display_name: str = Field(description="Formatted as `<edition> <major>.<minor>.<patch>`.")

    @classmethod
    def from_domain(cls, version: MinecraftVersion) -> VersionDetail:
        return cls(
            edition=version.edition,
            major=version.major,
            minor=version.minor,
            patch=version.patch,
            display_name=str(version),
        )
