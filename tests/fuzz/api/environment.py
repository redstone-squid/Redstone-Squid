"""Safety and lifecycle boundary for one disposable API fuzzing stack."""

import asyncio
import base64
import hashlib
import hmac
import secrets
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Self
from urllib.parse import urlsplit

RUN_LABEL = "dev.redstone-squid.api-fuzz.run"
RESOURCE_LABEL = "dev.redstone-squid.api-fuzz.resource"
DATABASE_PREFIX = "squid_fuzz_"
TEMPLATE_DATABASE_PREFIX = "squid_fuzz_template_"
APPLICATION_PREFIX = "squid-api-fuzz-"
RUN_ID_ENV = "REDSTONE_SQUID_FUZZ_RUN_ID"
SENTINEL_ENV = "REDSTONE_SQUID_FUZZ_SENTINEL"
CONTROL_NONCE_ENV = "REDSTONE_SQUID_FUZZ_CONTROL_NONCE"
FAKE_PORT_ENV = "REDSTONE_SQUID_FUZZ_FAKE_PORT"

type AsyncAction = Callable[[], Awaitable[None]]
type Checksum = Callable[[], Awaitable[str]]
type Seeder = Callable[[], Awaitable["SeededIds"]]
type StackStarter = Callable[["RunIdentity", AsyncExitStack], Awaitable["RunningApi"]]


class UnsafeEnvironmentError(RuntimeError):
    """A target or owned resource failed its mandatory disposable-stack attestation."""


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """Unguessable names and sentinels shared by resources in one fuzz run."""

    run_id: str
    sentinel: str
    database_name: str
    application_name: str
    network_name: str

    @classmethod
    def generate(cls) -> Self:
        """Create a collision-resistant identity without reading process configuration."""
        run_id = secrets.token_hex(16)
        return cls(
            run_id=run_id,
            sentinel=secrets.token_urlsafe(32),
            database_name=f"{DATABASE_PREFIX}{run_id}",
            application_name=f"{APPLICATION_PREFIX}{run_id}",
            network_name=f"redstone-squid-api-fuzz-{run_id}",
        )

    @property
    def labels(self) -> dict[str, str]:
        """Return the run-ownership label required on every Docker resource."""
        return {RUN_LABEL: self.run_id}

    @property
    def template_database_name(self) -> str:
        """Return the per-run migrated template database name."""
        return f"{TEMPLATE_DATABASE_PREFIX}{self.run_id}"

    @property
    def migrator_role(self) -> str:
        """Return the per-run migration and reset role name."""
        return f"squid_fuzz_migrator_{self.run_id}"

    @property
    def application_role(self) -> str:
        """Return the per-run least-privileged API role name."""
        return f"squid_fuzz_app_{self.run_id}"

    @property
    def observer_role(self) -> str:
        """Return the per-run read-only invariant role name."""
        return f"squid_fuzz_observer_{self.run_id}"

    @property
    def redis_namespace(self) -> str:
        """Return the namespace reserved in this run's dedicated Redis database."""
        return f"squid:fuzz:{self.run_id}"


@dataclass(frozen=True, slots=True)
class ResourceAttestation:
    """Facts read back from the live stack before a destructive action."""

    labels: Mapping[str, str]
    network_id: str
    network_internal: bool
    database_name: str
    sentinel: str
    application_name: str

    def verify(self, identity: RunIdentity, *, expected_network_id: str) -> None:
        """Refuse any resource set that is not exactly owned by this run."""
        failures: list[str] = []
        for key, expected in identity.labels.items():
            if self.labels.get(key) != expected:
                failures.append(f"label:{key}")
        if not expected_network_id or self.network_id != expected_network_id:
            failures.append("network_id")
        if not self.network_internal:
            failures.append("network_internal")
        if not self.database_name.startswith(DATABASE_PREFIX) or self.database_name != identity.database_name:
            failures.append("database_name")
        if not secrets.compare_digest(self.sentinel, identity.sentinel):
            failures.append("sentinel")
        if self.application_name != identity.application_name:
            failures.append("application_name")
        if failures:
            names = ", ".join(failures)
            msg = f"Disposable API environment attestation failed: {names}."
            raise UnsafeEnvironmentError(msg)


@dataclass(frozen=True, slots=True)
class ResetHooks:
    """Narrow operations needed to rebuild one deterministic example baseline."""

    quiesce: AsyncAction
    reset_database: AsyncAction
    clear_redis: AsyncAction
    seed: Seeder
    resume: AsyncAction
    reset_fakes: AsyncAction
    checksum: Checksum
    seeded_ids: "SeededIds"
    baseline_checksum: str


@dataclass(frozen=True, slots=True)
class SeededIds:
    """Stable identifiers and synthetic credentials recreated by every reset."""

    alice_account_id: int
    bob_account_id: int
    consent_pending_account_id: int
    administrator_account_id: int
    java_version_id: int
    alice_public_id: str
    bob_public_id: str
    alice_web_session: str = field(repr=False)
    bob_web_session: str = field(repr=False)
    consent_pending_web_session: str = field(repr=False)
    administrator_web_session: str = field(repr=False)
    service_api_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SyntheticSecrets:
    """Per-run synthetic credentials derived without consulting host configuration."""

    verification_code_pepper: str = field(repr=False)
    cursor_secret: str = field(repr=False)
    api_secret: str = field(repr=False)
    api_key_pepper: str = field(repr=False)
    session_pepper: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    alice_web_session: str = field(repr=False)
    bob_web_session: str = field(repr=False)
    consent_pending_web_session: str = field(repr=False)
    administrator_web_session: str = field(repr=False)
    service_api_key_id: str
    service_api_key_secret: str = field(repr=False)

    @classmethod
    def for_identity(cls, identity: RunIdentity) -> "SyntheticSecrets":
        """Derive domain-separated credentials from the run's random sentinel."""

        def derive(label: str, *, size: int = 32) -> str:
            digest = hmac.digest(identity.sentinel.encode(), label.encode(), hashlib.sha256)[:size]
            return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

        idempotency_key = base64.b64encode(
            hmac.digest(identity.sentinel.encode(), b"idempotency", hashlib.sha256)
        ).decode()
        return cls(
            verification_code_pepper=f"fuzz-verification-{derive('verification')}",
            cursor_secret=f"fuzz-cursor-{derive('cursor')}",
            api_secret=f"fuzz-bootstrap-{derive('bootstrap')}",
            api_key_pepper=f"fuzz-api-key-{derive('api-key-pepper')}",
            session_pepper=f"fuzz-session-{derive('session-pepper')}",
            idempotency_key=idempotency_key,
            alice_web_session=derive("web-session-alice"),
            bob_web_session=derive("web-session-bob"),
            consent_pending_web_session=derive("web-session-consent-pending"),
            administrator_web_session=derive("web-session-administrator"),
            service_api_key_id="fuzzservice",
            service_api_key_secret=derive("service-api-key"),
        )

    @property
    def service_api_token(self) -> str:
        """Return the complete indexed service credential."""
        return f"sq_{self.service_api_key_id}_{self.service_api_key_secret}"


@dataclass(slots=True)
class RunningApi:
    """One attested live API stack and its serialized reset boundary."""

    identity: RunIdentity
    base_url: str
    network_id: str
    read_attestation: Callable[[], Awaitable[ResourceAttestation]]
    reset_hooks: ResetHooks
    _reset_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def attest(self) -> ResourceAttestation:
        """Read and verify live ownership facts before stack mutation."""
        validate_target_url(self.base_url)
        attestation = await self.read_attestation()
        attestation.verify(self.identity, expected_network_id=self.network_id)
        return attestation

    async def reset(self) -> None:
        """Quiesce, reset, and reseed one worker after fresh live attestation."""
        async with self._reset_lock:
            await self.attest()
            hooks = self.reset_hooks
            await hooks.quiesce()
            await hooks.reset_database()
            await hooks.clear_redis()
            seeded_ids = await hooks.seed()
            if seeded_ids != hooks.seeded_ids:
                msg = "Disposable API environment reseeded different stable identifiers."
                raise UnsafeEnvironmentError(msg)
            await hooks.resume()
            await hooks.reset_fakes()
            actual = await hooks.checksum()
            if not secrets.compare_digest(actual, hooks.baseline_checksum):
                msg = "Disposable API environment baseline checksum does not match after reset."
                raise UnsafeEnvironmentError(msg)


class ApiEnvironment:
    """Own every disposable resource through one asynchronous exit stack."""

    def __init__(self, starter: StackStarter, *, identity: RunIdentity | None = None) -> None:
        self._starter = starter
        self.identity = identity or RunIdentity.generate()
        self._stack: AsyncExitStack | None = None
        self._running: RunningApi | None = None

    async def __aenter__(self) -> RunningApi:
        if self._stack is not None:
            msg = "An API fuzz environment cannot be entered more than once."
            raise RuntimeError(msg)
        stack = AsyncExitStack()
        await stack.__aenter__()
        self._stack = stack
        try:
            running = await self._starter(self.identity, stack)
            _require_identity(running, self.identity)
            await running.attest()
        except BaseException:
            await stack.aclose()
            self._stack = None
            raise
        self._running = running
        return running

    async def __aexit__(self, *_exc: object) -> None:
        stack, self._stack = self._stack, None
        self._running = None
        if stack is not None:
            await stack.aclose()


def _require_identity(running: RunningApi, expected: RunIdentity) -> None:
    if running.identity != expected:
        msg = "The API stack starter returned a different run identity."
        raise UnsafeEnvironmentError(msg)


@dataclass(frozen=True, slots=True)
class SyntheticEndpoints:
    """Loopback endpoints assigned to harness-owned fake services."""

    api_container_port: int
    postgres_url: str
    redis_url: str
    mojang_profile_url: str
    discord_api_url: str
    discord_authorize_url: str


def synthetic_api_environment(
    identity: RunIdentity,
    endpoints: SyntheticEndpoints,
    *,
    secrets_: SyntheticSecrets | None = None,
) -> dict[str, str]:
    """Build an allowlisted API environment containing only synthetic credentials."""
    if not 1 <= endpoints.api_container_port <= 65_535:
        msg = "The API container port must be between 1 and 65535."
        raise ValueError(msg)
    resolved_secrets = secrets_ or SyntheticSecrets.for_identity(identity)
    return {
        CONTROL_NONCE_ENV: identity.sentinel,
        FAKE_PORT_ENV: "8101",
        RUN_ID_ENV: identity.run_id,
        SENTINEL_ENV: identity.sentinel,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "TMPDIR": "/tmp",
        "SQUID_STRICT_UNKNOWN_KEYS": "true",
        "SQUID_DATABASE_URL": endpoints.postgres_url,
        "SQUID_VERIFICATION_CODE_PEPPER": resolved_secrets.verification_code_pepper,
        "SQUID_CURSOR_SECRET": resolved_secrets.cursor_secret,
        "SQUID_API_SECRET": resolved_secrets.api_secret,
        "SQUID_API_KEY_PEPPER": resolved_secrets.api_key_pepper,
        "SQUID_API_SESSION_PEPPER": resolved_secrets.session_pepper,
        "SQUID_API_IDEMPOTENCY_ACTIVE_KEY_ID": "fuzz-v1",
        "SQUID_API_IDEMPOTENCY_KEYS": f'{{"fuzz-v1":"{resolved_secrets.idempotency_key}"}}',
        "SQUID_API_PORT": str(endpoints.api_container_port),
        "SQUID_RATE_LIMIT_REDIS_URL": endpoints.redis_url,
        "SQUID_STORAGE_LOCAL_DIRECTORY": "/tmp/objects",
        "SQUID_UPSTREAM_HTTP_MOJANG_PROFILE_URL": endpoints.mojang_profile_url,
        "SQUID_UPSTREAM_HTTP_DISCORD_API_URL": endpoints.discord_api_url,
        "SQUID_UPSTREAM_HTTP_DISCORD_AUTHORIZE_URL": endpoints.discord_authorize_url,
        "SQUID_SCHEMATIC_ENABLED": "false",
        "SQUID_MEDIA_ENABLED": "false",
        "SQUID_OBSERVABILITY_ENABLED": "false",
    }


def validate_target_url(url: str) -> str:
    """Return a normalized IPv4-loopback HTTP origin or refuse it."""
    parsed = urlsplit(url)
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        host = None
        port = None
    if (
        parsed.scheme != "http"
        or host != "127.0.0.1"
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        msg = "API fuzz targets must be explicit 127.0.0.1 HTTP origins with a port."
        raise UnsafeEnvironmentError(msg)
    return f"http://{host}:{port}"
