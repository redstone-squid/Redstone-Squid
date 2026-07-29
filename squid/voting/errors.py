"""Voting context errors."""

from squid.core.errors import ConfigurationError, ErrorCode


class InvalidVoteConfigurationError(ConfigurationError):
    """Vote options violate voting policy."""

    default_message = "Vote configuration is invalid."
    default_code = ErrorCode.INVALID_VOTE_CONFIGURATION
    default_resource = "vote"
