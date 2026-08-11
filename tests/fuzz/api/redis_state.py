"""Attested reset operations for one dedicated API-fuzz Redis database."""

import hmac
from dataclasses import dataclass

from redis import Redis

from tests.fuzz.api.environment import RunIdentity, UnsafeEnvironmentError


@dataclass(frozen=True, slots=True)
class RedisLocation:
    """Coordinator and container-network locations for one Redis service."""

    coordinator_host: str
    coordinator_port: int
    container_host: str
    container_port: int = 6379
    database: int = 0


class RedisController:
    """Verify and clear only the Redis database dedicated to one fuzz run."""

    def __init__(self, identity: RunIdentity, location: RedisLocation) -> None:
        self.identity = identity
        self.location = location

    @property
    def container_url(self) -> str:
        """Return the Redis URL reachable only on the Docker network."""
        return f"redis://{self.location.container_host}:{self.location.container_port}/{self.location.database}"

    @property
    def coordinator_url(self) -> str:
        """Return the internal-bridge Redis URL used by the local coordinator."""
        return f"redis://{self.location.coordinator_host}:{self.location.coordinator_port}/{self.location.database}"

    @property
    def sentinel_key(self) -> str:
        """Return the exact sentinel key for this run's namespace."""
        return f"{self.identity.redis_namespace}:sentinel"

    def initialize(self) -> None:
        """Require a fresh logical database and plant its per-run sentinel."""
        with self._client() as client:
            if client.dbsize() != 0:
                msg = "Disposable Redis database was not empty at initialization."
                raise UnsafeEnvironmentError(msg)
            client.set(self.sentinel_key, self.identity.sentinel)

    def verify(self) -> None:
        """Require the exact run sentinel before any Redis mutation."""
        with self._client() as client:
            value = client.get(self.sentinel_key)
        if not isinstance(value, bytes) or not hmac.compare_digest(value, self.identity.sentinel.encode()):
            msg = "Disposable Redis sentinel attestation failed."
            raise UnsafeEnvironmentError(msg)

    def clear(self) -> None:
        """Flush only this attested logical database and immediately replant its sentinel."""
        self.verify()
        with self._client() as client:
            client.flushdb()
            client.set(self.sentinel_key, self.identity.sentinel)

    def keys(self) -> set[bytes]:
        """Return bounded keys for lifecycle integration assertions."""
        with self._client() as client:
            size = client.dbsize()
            if size > 2_048:
                msg = "Disposable Redis database exceeded its key limit."
                raise UnsafeEnvironmentError(msg)
            return set(client.scan_iter(count=256))

    def _client(self) -> Redis:
        return Redis.from_url(
            self.coordinator_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=False,
        )
