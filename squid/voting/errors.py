"""Voting context errors."""

from squid.core.errors import ConfigurationError, ErrorCode, ServiceUnavailableError
from squid.core.i18n import _


class InvalidVoteConfigurationError(ConfigurationError):
    """Vote options violate voting policy."""

    default_message = _("Vote configuration is invalid.")
    default_code = ErrorCode.INVALID_VOTE_CONFIGURATION
    default_resource = "vote"


class DiscordMemberServiceUnavailableError(ServiceUnavailableError):
    """Discord could not provide current guild membership facts."""

    default_message = _("Discord membership information is temporarily unavailable.")
    default_resource = "discord"
