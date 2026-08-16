"""Attested reset operations for one dedicated API-fuzz Redis database."""

import hmac
import secrets
from dataclasses import dataclass, field
from urllib.parse import quote

from redis import Redis
from redis.exceptions import NoPermissionError

from tests.fuzz.api.environment import RunIdentity, UnsafeEnvironmentError


@dataclass(frozen=True, slots=True)
class RedisLocation:
    """Coordinator and container-network locations for one Redis service."""

    coordinator_host: str
    coordinator_port: int
    container_host: str
    container_port: int = 6379
    database: int = 0


@dataclass(frozen=True, slots=True)
class RedisCredentials:
    """Separate coordinator and narrowly scoped application Redis credentials."""

    coordinator_password: str = field(repr=False)
    application_password: str = field(repr=False)

    @classmethod
    def generate(cls) -> RedisCredentials:
        """Generate coordinator-owned Redis credentials independent of run identity."""
        return cls(secrets.token_urlsafe(32), secrets.token_urlsafe(32))


class RedisController:
    """Verify and clear only the Redis database dedicated to one fuzz run."""

    def __init__(self, identity: RunIdentity, location: RedisLocation, credentials: RedisCredentials) -> None:
        self.identity = identity
        self.location = location
        self.credentials = credentials

    @property
    def container_url(self) -> str:
        """Return the key-scoped application URL reachable only on the Docker network."""
        return self._url("application", self.credentials.application_password, self.location.container_host)

    @property
    def coordinator_url(self) -> str:
        """Return the internal-bridge Redis URL used by the local coordinator."""
        return self._url("coordinator", self.credentials.coordinator_password, self.location.coordinator_host)

    @property
    def application_coordinator_url(self) -> str:
        """Return the application credential against the coordinator-reachable address."""
        return self._url("application", self.credentials.application_password, self.location.coordinator_host)

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

    def application_cannot_control(self) -> bool:
        """Prove the API credential cannot read the sentinel or clear its logical database."""
        with Redis.from_url(
            self.application_coordinator_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=False,
        ) as client:
            refused = 0
            for operation in (lambda: client.get(self.sentinel_key), client.flushdb):
                try:
                    operation()
                except NoPermissionError:
                    refused += 1
        return refused == 2

    def _client(self) -> Redis:
        return Redis.from_url(
            self.coordinator_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=False,
        )

    def _url(self, username: str, password: str, host: str) -> str:
        encoded_username = quote(username, safe="")
        encoded_password = quote(password, safe="")
        return (
            f"redis://{encoded_username}:{encoded_password}@{host}:{self.location.container_port}/"
            f"{self.location.database}"
        )
