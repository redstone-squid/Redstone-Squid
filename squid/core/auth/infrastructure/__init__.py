"""Authentication persistence adapters."""

from squid.core.auth.infrastructure.repository import PostgresApiKeyRepository
from squid.core.auth.infrastructure.sessions import PostgresWebSessionRepository

__all__ = ["PostgresApiKeyRepository", "PostgresWebSessionRepository"]
