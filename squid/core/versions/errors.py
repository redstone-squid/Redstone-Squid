"""Version context errors."""

from squid.core.errors import ConflictError, ErrorCode, InfrastructureError, NotFoundError, ValidationError
from squid.core.i18n import tr


class InvalidVersionError(ValidationError):
    """A Minecraft version or version specification is invalid."""

    default_message = tr(t"versions-minecraft-version-invalid = The Minecraft version is invalid.")
    default_code = ErrorCode.INVALID_VERSION
    default_resource = "version"
    default_end_user_action = tr(
        t"versions-use-supported-minecraft = Use a supported Minecraft version format and try again."
    )


class VersionCatalogUnavailableError(InfrastructureError, RuntimeError):
    """The persisted version catalog is unexpectedly empty."""

    default_message = tr(t"versions-minecraft-version-catalog = The Minecraft version catalog is empty.")
    default_code = ErrorCode.VERSION_CATALOG_UNAVAILABLE
    default_resource = "version"
    default_developer_action = "Populate the version catalog before serving version-dependent operations."

    def __init__(self, edition: str) -> None:
        super().__init__(context={"edition": edition})
        self.edition = edition


class VersionNotFoundError(NotFoundError):
    """No catalogue entry has the requested ID."""

    default_message = tr(t"versions-entry-not-found = That Minecraft version is not in the catalogue.")
    default_code = ErrorCode.VERSION_NOT_FOUND
    default_resource = "version"

    def __init__(self, version_id: int) -> None:
        super().__init__(context={"version_id": version_id}, public_context={"version_id": version_id})


class VersionExistsError(ConflictError):
    """Another catalogue entry already has this edition and release number."""

    default_message = tr(t"versions-entry-exists = That Minecraft version is already in the catalogue.")
    default_title = tr(t"versions-errors-entry-exists = Version already exists")
    default_code = ErrorCode.VERSION_EXISTS
    default_resource = "version"

    def __init__(self, version: str) -> None:
        super().__init__(context={"version": version}, public_context={"version": version})


class VersionInUseError(ConflictError):
    """Builds or records still refer to the entry, so it cannot be deleted."""

    default_message = tr(
        t"versions-entry-in-use = Builds or records still use that Minecraft version. Correct it instead of deleting it."
    )
    default_title = tr(t"versions-errors-entry-in-use = Version in use")
    default_code = ErrorCode.VERSION_IN_USE
    default_resource = "version"

    def __init__(self, version_id: int) -> None:
        super().__init__(context={"version_id": version_id}, public_context={"version_id": version_id})
