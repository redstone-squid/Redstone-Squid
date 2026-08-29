"""Build context errors."""

from squid.core.errors import ConflictError, DomainError, ErrorCode, NotFoundError, ValidationError
from squid.core.i18n import tr


class InvalidBuildError(ValidationError):
    """Build data is invalid."""

    default_message = tr(t"The build data is invalid.")
    default_code = ErrorCode.INVALID_BUILD
    default_resource = "build"


class BuildNotFoundError(NotFoundError):
    """A build could not be found."""

    default_message = tr(t"Build not found.")
    default_code = ErrorCode.BUILD_NOT_FOUND
    default_resource = "build"
    default_end_user_action = tr(t"Check the build ID and try again.")

    def __init__(self, build_id: int, message: str | None = None) -> None:
        super().__init__(
            message,
            context={"build_id": build_id},
            public_context={"build_id": build_id},
        )
        self.build_id = build_id


class BuildBusyError(ConflictError):
    """A build is already being edited."""

    default_message = tr(t"This build is currently being edited by someone else.")
    default_title = tr(t"Build busy")
    default_code = ErrorCode.BUILD_BUSY
    default_resource = "build"
    default_end_user_action = tr(t"Wait for the other edit to finish and try again.")

    def __init__(self, build_id: int) -> None:
        super().__init__(
            context={"build_id": build_id},
            public_context={"build_id": build_id},
        )
        self.build_id = build_id


class BuildRevisionRequiredError(DomainError):
    """A caller attempted an optimistic write without a revision precondition."""

    default_message = tr(t"An If-Match build revision is required for this operation.")
    default_title = tr(t"Build revision required")
    default_code = ErrorCode.BUILD_REVISION_REQUIRED
    default_resource = "build"
    default_end_user_action = tr(t"Fetch the build again and retry with its ETag.")

    def __init__(self, build_id: int) -> None:
        super().__init__(
            context={"build_id": build_id},
            public_context={"build_id": build_id},
        )


class BuildRevisionMismatchError(DomainError):
    """A caller's expected build revision is no longer current."""

    default_message = tr(t"The build changed after it was read.")
    default_title = tr(t"Build revision mismatch")
    default_code = ErrorCode.BUILD_REVISION_MISMATCH
    default_resource = "build"
    default_end_user_action = tr(t"Fetch the latest build, merge the changes, and try again.")

    def __init__(self, build_id: int, *, expected_revision: int | None, current_revision: int | None = None) -> None:
        context = {"build_id": build_id}
        if expected_revision is not None:
            context["expected_revision"] = expected_revision
        if current_revision is not None:
            context["current_revision"] = current_revision
        super().__init__(context=context, public_context=context)


class RestrictionNotFoundError(NotFoundError):
    """A restriction name or alias could not be found."""

    default_message = tr(t"Restriction not found.")
    default_code = ErrorCode.RESTRICTION_NOT_FOUND
    default_resource = "restriction"

    def __init__(self, name: str) -> None:
        super().__init__(
            tr(t"Restriction '{name}' does not exist."),
            context={"name": name},
            public_context={"name": name},
        )
        self.name = name


class AliasAlreadyAddedError(ConflictError):
    """An alias is already attached to the requested restriction."""

    default_message = tr(t"That alias is already on this restriction.")
    default_title = tr(t"Alias already added")
    default_code = ErrorCode.ALIAS_ALREADY_ADDED
    default_resource = "restriction_alias"

    def __init__(self, alias: str, restriction_id: int) -> None:
        super().__init__(
            context={"alias": alias, "restriction_id": restriction_id},
            public_context={"alias": alias},
        )
        self.alias = alias
        self.restriction_id = restriction_id


class AliasInUseError(ConflictError):
    """An alias belongs to a different restriction."""

    default_message = tr(t"That alias is already used by another restriction.")
    default_title = tr(t"Alias in use")
    default_code = ErrorCode.ALIAS_IN_USE
    default_resource = "restriction_alias"

    def __init__(self, alias: str, other_id: int) -> None:
        super().__init__(
            context={"alias": alias, "other_restriction_id": other_id},
            public_context={"alias": alias},
        )
        self.alias = alias
        self.other_id = other_id
