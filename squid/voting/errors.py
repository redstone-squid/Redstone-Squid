"""Voting context errors."""

from squid.core.errors import ConfigurationError, ErrorCode
from squid.core.i18n import _


class InvalidVoteConfigurationError(ConfigurationError):
    """Vote options violate voting policy."""

    default_message = _("Vote configuration is invalid.")
    default_code = ErrorCode.INVALID_VOTE_CONFIGURATION
    default_resource = "vote"
