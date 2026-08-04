"""Public bot authorization API."""

from squid.permissions.application import AuthorizationService
from squid.permissions.domain import GlobalAdministrator

__all__ = ["AuthorizationService", "GlobalAdministrator"]
