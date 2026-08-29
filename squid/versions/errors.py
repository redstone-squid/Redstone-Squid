"""Version context errors."""

from squid.core.errors import ErrorCode, InfrastructureError, ValidationError
from squid.core.i18n import tr


class InvalidVersionError(ValidationError):
    """A Minecraft version or version specification is invalid."""

    default_message = tr(t"The Minecraft version is invalid.")
    default_code = ErrorCode.INVALID_VERSION
    default_resource = "version"
    default_end_user_action = tr(t"Use a supported Minecraft version format and try again.")


class VersionCatalogUnavailableError(InfrastructureError, RuntimeError):
    """The persisted version catalog is unexpectedly empty."""

    default_message = tr(t"The Minecraft version catalog is empty.")
    default_code = ErrorCode.VERSION_CATALOG_UNAVAILABLE
    default_resource = "version"
    default_developer_action = "Populate the version catalog before serving version-dependent operations."

    def __init__(self, edition: str) -> None:
        super().__init__(context={"edition": edition})
        self.edition = edition
