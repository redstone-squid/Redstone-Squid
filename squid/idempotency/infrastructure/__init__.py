"""PostgreSQL idempotency persistence."""

from squid.idempotency.infrastructure.repository import PostgresIdempotencyRepository

__all__ = ["PostgresIdempotencyRepository"]
