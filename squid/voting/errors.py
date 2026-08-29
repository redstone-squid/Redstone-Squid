"""Voting context errors."""

from squid.core.errors import ConfigurationError, ErrorCode, NotFoundError, ServiceUnavailableError
from squid.core.i18n import tr


class VoteSessionNotFoundError(NotFoundError):
    """No vote session exists for the requested identifier."""

    default_message = tr(t"Vote session not found.")
    default_title = tr(t"Vote session not found")
    default_code = ErrorCode.VOTE_SESSION_NOT_FOUND
    default_resource = "vote_session"

    def __init__(self, vote_session_id: int) -> None:
        super().__init__(
            context={"vote_session_id": vote_session_id},
            public_context={"vote_session_id": vote_session_id},
        )
        self.vote_session_id = vote_session_id


class InvalidVoteConfigurationError(ConfigurationError):
    """Vote options violate voting policy."""

    default_message = tr(t"Vote configuration is invalid.")
    default_code = ErrorCode.INVALID_VOTE_CONFIGURATION
    default_resource = "vote"


class DiscordMemberServiceUnavailableError(ServiceUnavailableError):
    """Discord could not provide current guild membership facts."""

    default_message = tr(t"Discord membership information is temporarily unavailable.")
    default_resource = "discord"
