"""Compatibility imports for Discord vote sessions."""

from squid.bot.voting.base_session import AbstractVoteSession
from squid.bot.voting.build_session import BuildVoteSession
from squid.bot.voting.delete_log_session import DeleteLogVoteSession

__all__ = ["AbstractVoteSession", "BuildVoteSession", "DeleteLogVoteSession"]
