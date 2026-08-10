"""Submission draft application errors."""

from uuid import UUID

from squid.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError


class DraftNotFoundError(NotFoundError):
    """No visible submission draft matches the requested identifier."""

    default_message = "Submission draft not found."
    default_title = "Draft not found"
    default_resource = "submission_draft"

    def __init__(self, draft_id: UUID) -> None:
        super().__init__(public_context={"draft_id": str(draft_id)})


class DraftAccessDeniedError(AuthorizationError):
    """The caller is not allowed to mutate a submission draft."""

    default_message = "You cannot edit this submission draft."
    default_title = "Draft access denied"
    default_resource = "submission_draft"


class DraftCapacityExceededError(ConflictError):
    """An account has no free synchronized draft capacity."""

    default_message = "Your synchronized draft capacity is full."
    default_title = "Draft capacity full"
    default_resource = "submission_draft"
    default_end_user_action = "Submit, delete, or wait for an existing draft to expire before creating another."

    def __init__(self, limit: int) -> None:
        super().__init__(public_context={"limit": limit})


class DraftStateConflictError(ConflictError):
    """A draft mutation lost a race with a lifecycle transition."""

    default_message = "This submission draft cannot be modified in its current state."
    default_title = "Draft state changed"
    default_resource = "submission_draft"
    default_end_user_action = "Reload the draft before trying again."

    def __init__(self, status: str, *, operation: str) -> None:
        super().__init__(public_context={"status": status, "operation": operation})


class DraftArtifactsChangedError(ConflictError):
    """Artifact readiness changed before finalization acquired its durable fence."""

    default_message = "The submission draft's media changed while finalization was starting."
    default_title = "Draft media changed"
    default_resource = "submission_draft"
    default_end_user_action = "Review the latest media status and submit the draft again."

    def __init__(self) -> None:
        super().__init__(public_context={"reason": "media_changed"})


class DraftSchemaUnsupportedError(ValidationError):
    """A renderer cannot safely present required fields in the pinned schema."""

    default_message = "This client cannot render every required field in the submission form."
    default_title = "Submission form unsupported"
    default_resource = "submission_form"
    default_end_user_action = "Update the client or continue this draft on the web."

    def __init__(self, capabilities: tuple[str, ...]) -> None:
        super().__init__(public_context={"missing_capabilities": capabilities})


class DraftIncompleteError(ValidationError):
    """A draft cannot enter processing until every required value is valid."""

    default_message = "The submission draft is incomplete."
    default_title = "Draft incomplete"
    default_resource = "submission_draft"

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__(public_context={"field_errors": errors})
