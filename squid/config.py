"""Typed process configuration loaded and validated at application boundaries."""

import base64
import binascii
import json
import logging
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from functools import cached_property
from ipaddress import ip_network
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self, cast, override
from urllib.parse import urlsplit

from google.oauth2.service_account import Credentials
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError
from pydantic_settings.sources import PydanticBaseSettingsSource
from sqlalchemy import make_url

from squid.accounts.domain import IdentityProvider
from squid.core.errors import ConfigurationError

if TYPE_CHECKING:
    from squid.permissions.domain import Pattern

logger = logging.getLogger(__name__)
EMBEDDING_DIMENSION = 1536
"""Vector dimension fixed by the application-owned PostgreSQL schema."""

OPENAI_REQUEST_TIMEOUT_SECONDS = 60.0
"""Per-request bound on OpenAI-compatible calls.

The SDK defaults to ten minutes with retries on top. Embedding work runs inside
a periodic job that awaits it to completion, so an unbounded call stalls the
job's heartbeat and flips the worker readiness probe; inference runs behind
interactive Discord surfaces that will have given up long before.
"""

OPENAI_MAX_RETRIES = 2
"""Retries per OpenAI-compatible call, so the worst case stays a bounded multiple
of OPENAI_REQUEST_TIMEOUT_SECONDS rather than the SDK's larger default."""


def _empty_to_none(value: object) -> object:
    return None if value == "" else value


def _validate_postgres_url(value: SecretStr | None) -> SecretStr | None:
    if value is None:
        return None
    try:
        url = make_url(value.get_secret_value())
    except Exception as exc:
        msg = "Must be a valid PostgreSQL URL."
        raise ValueError(msg) from exc
    if url.get_backend_name() not in {"postgres", "postgresql"}:
        msg = "Must use the PostgreSQL backend."
        raise ValueError(msg)
    return value


def _validate_log_level(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.upper()
    if normalized not in logging.getLevelNamesMapping():
        msg = "Must be a standard Python logging level."
        raise ValueError(msg)
    return normalized


def _validate_relative_log_file(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    path = Path(value)
    if path.anchor or ".." in path.parts:
        msg = "Must be a relative path contained by the log directory."
        raise ValueError(msg)
    return value


def _parse_google_credentials(raw_credentials: str) -> dict[str, object]:
    credentials_info = json.loads(raw_credentials)
    if not isinstance(credentials_info, dict):
        msg = "Google credentials must contain a JSON object."
        raise TypeError(msg)
    return cast(dict[str, object], credentials_info)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DatabaseConfig(_FrozenModel):
    """Relational database connection configuration."""

    url: SecretStr
    listener_url: SecretStr | None = None

    _empty_listener_url = field_validator("listener_url", mode="before")(_empty_to_none)
    _validate_url = field_validator("url", "listener_url")(_validate_postgres_url)


class OpenAIConfig(_FrozenModel):
    """OpenAI-compatible text generation configuration."""

    api_key: SecretStr | None = None
    base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")
    chat_model: str = Field(default="gpt-5.6-luna", min_length=1)
    reasoning_effort: str = Field(default="low", min_length=1)

    _empty_api_key = field_validator("api_key", mode="before")(_empty_to_none)


class EmbeddingProviderConfig(_FrozenModel):
    """Environment-facing embedding provider configuration."""

    api_key: SecretStr | None = None
    base_url: AnyHttpUrl | None = None
    model: str = Field(default="text-embedding-3-small", min_length=1)

    _empty_api_key = field_validator("api_key", mode="before")(_empty_to_none)
    _empty_base_url = field_validator("base_url", mode="before")(_empty_to_none)


class ObjectStorageConfig(_FrozenModel):
    """Content-addressed binary artifact storage configuration."""

    backend: Literal["local", "s3"] = "local"
    local_directory: Path = Field(default_factory=lambda: Path.cwd() / ".data" / "objects")
    bucket: str | None = None
    endpoint: AnyHttpUrl | None = None
    access_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    region: str = "us-east-1"
    prefix: str = "redstone-squid"
    addressing_style: Literal["auto", "path", "virtual"] = "path"
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60.0)
    read_timeout_seconds: float = Field(default=30.0, gt=0, le=3600.0)
    max_attempts: int = Field(default=3, ge=1, le=10)

    _empty_bucket = field_validator("bucket", mode="before")(_empty_to_none)
    _empty_endpoint = field_validator("endpoint", mode="before")(_empty_to_none)
    _empty_access_key = field_validator("access_key", mode="before")(_empty_to_none)
    _empty_secret_key = field_validator("secret_key", mode="before")(_empty_to_none)

    @model_validator(mode="after")
    def _validate_backend(self) -> Self:
        if self.backend == "s3" and self.bucket is None:
            msg = "The S3 storage backend requires a bucket."
            raise ValueError(msg)
        if (self.access_key is None) != (self.secret_key is None):
            msg = "Object-storage access_key and secret_key must be configured together."
            raise ValueError(msg)
        normalized_prefix = self.prefix.strip("/")
        if ".." in normalized_prefix.split("/"):
            msg = "Object-storage prefix must not contain path traversal."
            raise ValueError(msg)
        object.__setattr__(self, "prefix", normalized_prefix)
        return self


class MediaConfig(_FrozenModel):
    """Private media normalization queue and subprocess configuration."""

    enabled: bool = False
    ffmpeg: str = Field(default="ffmpeg", min_length=1)
    ffprobe: str = Field(default="ffprobe", min_length=1)
    working_directory: Path | None = None
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    probe_timeout_seconds: float = Field(default=15.0, gt=0)
    image_timeout_seconds: float = Field(default=120.0, gt=0)
    video_timeout_seconds: float = Field(default=600.0, gt=0)
    poster_timeout_seconds: float = Field(default=120.0, gt=0)
    memory_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)
    cpu_seconds: int = Field(default=540, gt=0)
    max_open_files: int = Field(default=128, gt=0)
    threads: int = Field(default=2, ge=1, le=16)

    _empty_working_directory = field_validator("working_directory", mode="before")(_empty_to_none)


class MinecraftAuthConfig(_FrozenModel):
    """Independent keyed-hash material for Minecraft device credentials."""

    pepper: SecretStr | None = None
    verification_uri: AnyHttpUrl | None = None
    sponsor_attribution_enabled: bool = False

    _empty_pepper = field_validator("pepper", mode="before")(_empty_to_none)
    _empty_verification_uri = field_validator("verification_uri", mode="before")(_empty_to_none)

    @field_validator("pepper")
    @classmethod
    def _require_pepper_strength(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < 32:
            msg = "Must contain at least 32 bytes."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _require_complete_device_flow(self) -> Self:
        if (self.pepper is None) != (self.verification_uri is None):
            msg = "Minecraft authorization requires both pepper and verification_uri."
            raise ValueError(msg)
        if self.sponsor_attribution_enabled and self.pepper is None:
            msg = "Paper sponsor attribution requires the Minecraft authorization flow."
            raise ValueError(msg)
        if self.verification_uri is not None:
            parsed = urlsplit(str(self.verification_uri))
            if parsed.scheme != "https" or parsed.query or parsed.fragment:
                msg = "Minecraft verification_uri must be HTTPS without a query or fragment."
                raise ValueError(msg)
        return self


class CliAuthConfig(_FrozenModel):
    """Independent hash material and browser location for CLI device approval."""

    pepper: SecretStr | None = None
    verification_uri: AnyHttpUrl | None = None

    _empty_pepper = field_validator("pepper", mode="before")(_empty_to_none)
    _empty_verification_uri = field_validator("verification_uri", mode="before")(_empty_to_none)

    @field_validator("pepper")
    @classmethod
    def _require_pepper_strength(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < 32:
            msg = "Must contain at least 32 bytes."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _require_complete_device_flow(self) -> Self:
        if (self.pepper is None) != (self.verification_uri is None):
            msg = "CLI authorization requires both pepper and verification_uri."
            raise ValueError(msg)
        if self.verification_uri is not None:
            parsed = urlsplit(str(self.verification_uri))
            if parsed.scheme != "https" or parsed.query or parsed.fragment:
                msg = "CLI verification_uri must be HTTPS without a query or fragment."
                raise ValueError(msg)
        return self


class EmbeddingConfig(_FrozenModel):
    """Resolved embedding provider configuration."""

    api_key: SecretStr | None
    base_url: AnyHttpUrl
    model: str


class VerificationConfig(_FrozenModel):
    """Verification-code security configuration."""

    code_pepper: SecretStr

    @field_validator("code_pepper")
    @classmethod
    def _require_pepper(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            msg = "Must not be empty."
            raise ValueError(msg)
        return value


_IDEMPOTENCY_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _validate_idempotency_key_id(value: str) -> str:
    if _IDEMPOTENCY_KEY_ID.fullmatch(value) is None:
        msg = "Must be a 1-64 character identifier containing only letters, digits, dot, underscore, or hyphen."
        raise ValueError(msg)
    return value


def _validate_idempotency_keys(value: dict[str, SecretStr]) -> dict[str, SecretStr]:
    for key_id, encoded_key in value.items():
        if _IDEMPOTENCY_KEY_ID.fullmatch(key_id) is None:
            msg = "Every key ID must use the same format as the active key ID."
            raise ValueError(msg)
        try:
            key = base64.b64decode(encoded_key.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            msg = "Every idempotency key must be valid padded base64."
            raise ValueError(msg) from exc
        if len(key) != 32:
            msg = "Every idempotency key must decode to exactly 32 bytes."
            raise ValueError(msg)
    return value


def _require_active_idempotency_key(active_key_id: str, keys: Mapping[str, SecretStr]) -> None:
    if active_key_id not in keys:
        msg = "The active idempotency key ID must exist in the keyring."
        raise ValueError(msg)


class IdempotencyEncryptionConfig(_FrozenModel):
    """Active and retained AES-256 keys for encrypted API response replay."""

    active_key_id: str
    keys: dict[str, SecretStr] = Field(min_length=1, max_length=8)

    _validate_active_key_id = field_validator("active_key_id")(_validate_idempotency_key_id)
    _validate_keys = field_validator("keys")(_validate_idempotency_keys)

    @model_validator(mode="after")
    def _require_active_key(self) -> Self:
        _require_active_idempotency_key(self.active_key_id, self.keys)
        return self

    def decoded_keys(self) -> dict[str, bytes]:
        """Return validated binary keys for the encryption adapter."""
        return {
            key_id: base64.b64decode(encoded_key.get_secret_value(), validate=True)
            for key_id, encoded_key in self.keys.items()
        }


class DiscordConfig(_FrozenModel):
    """Discord transport credentials."""

    token: SecretStr

    @field_validator("token")
    @classmethod
    def _require_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            msg = "Must not be empty."
            raise ValueError(msg)
        return value


class ApiConfig(_FrozenModel):
    """HTTP API transport configuration."""

    secret: SecretStr
    key_pepper: SecretStr
    session_pepper: SecretStr
    idempotency_active_key_id: str
    idempotency_keys: dict[str, SecretStr] = Field(min_length=1, max_length=8)
    bot_token: SecretStr | None = None
    port: int = Field(default=8000, ge=1, le=65535)
    log_file: str | None = None
    access_log_file: str | None = None
    cors_origins: tuple[str, ...] = ()
    trusted_proxy_ips: tuple[str, ...] = ()
    secret_nodes: tuple[str, ...] = ("build.submission.read",)
    """Permission nodes the legacy bootstrap secret carries.

    It used to carry every capability the API defined, forever, with no way to
    narrow it. Deployments that still need it for writes list the nodes here
    explicitly; the default is what an anonymous caller already has, so leaving
    it unset makes the secret useless rather than dangerous.

    Validated as patterns at load, so a typo fails startup rather than quietly
    matching nothing. Read `secret_patterns` at request time.
    """

    _empty_log_file = field_validator("log_file", "access_log_file", mode="before")(_empty_to_none)
    _empty_bot_token = field_validator("bot_token", mode="before")(_empty_to_none)
    _validate_log_files = field_validator("log_file", "access_log_file")(_validate_relative_log_file)

    # Validating the keyring on this model's own fields, rather than only through the
    # IdempotencyEncryptionConfig built below, is what keeps a bad key reported against
    # SQUID_API_IDEMPOTENCY_KEYS instead of the inner model's "keys".
    _validate_active_key_id = field_validator("idempotency_active_key_id")(_validate_idempotency_key_id)
    _validate_keys = field_validator("idempotency_keys")(_validate_idempotency_keys)

    @field_validator("secret")
    @classmethod
    def _require_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            msg = "Must not be empty."
            raise ValueError(msg)
        return value

    @field_validator("secret_nodes")
    @classmethod
    def _require_parsable_secret_nodes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # Imported here rather than at module scope: `squid.permissions` reaches
        # `squid.observability`, which reads `ObservabilityConfig` from this module.
        from squid.permissions.domain import InvalidPatternError, Pattern

        for raw in value:
            try:
                Pattern.parse(raw)
            except InvalidPatternError as error:
                msg = f"{raw!r} is not a valid permission pattern."
                raise ValueError(msg) from error
        return value

    @cached_property
    def secret_patterns(self) -> frozenset[Pattern]:
        """`secret_nodes` parsed once, for matching without re-parsing per request."""
        from squid.permissions.domain import Pattern

        return frozenset(Pattern.parse(raw) for raw in self.secret_nodes)

    @field_validator("key_pepper", "session_pepper")
    @classmethod
    def _require_peppers(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < 16:
            msg = "Must contain at least 16 bytes."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_idempotency_encryption(self) -> Self:
        _require_active_idempotency_key(self.idempotency_active_key_id, self.idempotency_keys)
        return self

    @property
    def idempotency_encryption(self) -> IdempotencyEncryptionConfig:
        """Return the validated response-encryption keyring."""
        return IdempotencyEncryptionConfig(
            active_key_id=self.idempotency_active_key_id,
            keys=self.idempotency_keys,
        )

    @field_validator("trusted_proxy_ips")
    @classmethod
    def _validate_trusted_proxy_ips(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for network in value:
            try:
                ip_network(network, strict=False)
            except ValueError as exc:
                msg = "Must contain only IP addresses or CIDR networks."
                raise ValueError(msg) from exc
        return value


class RateLimitConfig(_FrozenModel):
    """Distributed HTTP abuse-control configuration."""

    redis_url: SecretStr | None = None
    window_seconds: int = Field(default=300, ge=1, le=86_400)
    ip_requests: int = Field(default=600, ge=1)
    principal_requests: int = Field(default=300, ge=1)
    write_requests: int = Field(default=60, ge=1)
    vote_requests: int = Field(default=30, ge=1)
    suggest_requests: int = Field(default=1_200, ge=1)
    """Typeahead runs per keystroke, so it needs headroom the generic read quota does not give."""
    render_requests: int = Field(default=20, ge=1)
    """On-demand renders occupy the native engine for seconds each, so they are quota'd well
    below the generic read ceiling that would otherwise govern them."""
    minecraft_challenge_start_requests: int = Field(default=10, ge=1)
    minecraft_challenge_exchange_requests: int = Field(default=120, ge=1)
    minecraft_challenge_approval_requests: int = Field(default=20, ge=1)
    redis_timeout_seconds: float = Field(default=0.2, gt=0, le=10)
    redis_retry_seconds: float = Field(default=5.0, gt=0, le=300)
    local_max_keys: int = Field(default=2_048, ge=16, le=100_000)

    _empty_redis_url = field_validator("redis_url", mode="before")(_empty_to_none)

    @field_validator("redis_url")
    @classmethod
    def _validate_redis_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw_url = value.get_secret_value()
        if not raw_url.startswith(("redis://", "rediss://", "unix://")):
            msg = "Must use a redis://, rediss://, or unix:// URL."
            raise ValueError(msg)
        return value


@dataclass(frozen=True, slots=True)
class OAuthClientCredentials:
    """One provider's complete authorization-code client registration."""

    client_id: str
    client_secret: SecretStr
    redirect_uri: AnyHttpUrl


class OAuthConfig(_FrozenModel):
    """Authorization-code client registrations, one flat group per provider.

    Flat rather than a `dict[str, OAuthClientCredentials]` because `_ProcessSettings` sets
    `env_nested_max_split=1`: `SQUID_OAUTH_DISCORD_CLIENT_ID` cannot split into
    `oauth -> discord -> client_id`, so the nested shape is unreachable from the
    environment. A second provider is three more fields and one `_GROUPS` entry.
    """

    discord_client_id: str | None = None
    discord_client_secret: SecretStr | None = None
    redirect_uri: AnyHttpUrl | None = None
    """Discord's callback. Named without a provider prefix for compatibility with
    SQUID_OAUTH_REDIRECT_URI, which is registered in the Discord developer portal."""
    session_ttl_hours: int = Field(default=336, ge=1)

    _empty_client_id = field_validator("discord_client_id", mode="before")(_empty_to_none)
    _empty_client_secret = field_validator("discord_client_secret", mode="before")(_empty_to_none)
    _empty_redirect_uri = field_validator("redirect_uri", mode="before")(_empty_to_none)

    _GROUPS: ClassVar[Mapping[IdentityProvider, tuple[str, str, str]]] = {
        IdentityProvider.DISCORD: ("discord_client_id", "discord_client_secret", "redirect_uri"),
    }

    def clients(self) -> Mapping[IdentityProvider, OAuthClientCredentials]:
        """Every provider whose credentials are completely configured."""
        resolved: dict[IdentityProvider, OAuthClientCredentials] = {}
        for provider, (id_field, secret_field, redirect_field) in self._GROUPS.items():
            client_id = getattr(self, id_field)
            client_secret = getattr(self, secret_field)
            redirect_uri = getattr(self, redirect_field)
            if client_id is not None and client_secret is not None and redirect_uri is not None:
                resolved[provider] = OAuthClientCredentials(client_id, client_secret, redirect_uri)
        return resolved

    @model_validator(mode="after")
    def _require_complete_credentials(self) -> Self:
        for provider, fields in self._GROUPS.items():
            configured = [getattr(self, field) for field in fields]
            if any(value is not None for value in configured) and not all(value is not None for value in configured):
                msg = f"{provider.value} OAuth client ID, secret, and redirect URI must be configured together."
                raise ValueError(msg)
        return self


class UpstreamHttpConfig(_FrozenModel):
    """Production upstream endpoints with tightly constrained test overrides."""

    mojang_profile_url: AnyHttpUrl = AnyHttpUrl("https://sessionserver.mojang.com/session/minecraft/profile")
    discord_api_url: AnyHttpUrl = AnyHttpUrl("https://discord.com/api/v10")
    discord_authorize_url: AnyHttpUrl = AnyHttpUrl("https://discord.com/oauth2/authorize")

    @model_validator(mode="after")
    def _official_or_loopback(self) -> Self:
        official = {
            "mojang_profile_url": "https://sessionserver.mojang.com/session/minecraft/profile",
            "discord_api_url": "https://discord.com/api/v10",
            "discord_authorize_url": "https://discord.com/oauth2/authorize",
        }
        for field, expected in official.items():
            value = getattr(self, field)
            if str(value).rstrip("/") == expected:
                continue
            if value.host not in {"127.0.0.1", "[::1]", "localhost"} or value.username or value.password:
                msg = f"{field} overrides must use an explicit loopback HTTP endpoint."
                raise ValueError(msg)
            if value.query is not None or value.fragment is not None:
                msg = f"{field} overrides cannot contain a query or fragment."
                raise ValueError(msg)
        return self


class BotConfig(_FrozenModel):
    """Discord-process-specific settings."""

    log_file: str | None = "discord.log"
    health_port: int = Field(default=8001, ge=1, le=65535)

    _empty_log_file = field_validator("log_file", mode="before")(_empty_to_none)
    _validate_log_file = field_validator("log_file")(_validate_relative_log_file)


class WorkerConfig(_FrozenModel):
    """Database-worker process settings."""

    log_file: str | None = "worker.log"
    health_port: int = Field(default=8002, ge=1, le=65535)
    event_interval_seconds: float = Field(default=15, gt=0)
    maintenance_interval_seconds: float = Field(default=30, gt=0)
    keepalive_interval_seconds: float = Field(default=86_400, gt=0)
    schematic_job_interval_seconds: float = Field(default=0.25, gt=0)
    media_job_interval_seconds: float = Field(default=0.25, gt=0)
    media_cleanup_interval_seconds: float = Field(default=60, ge=1)
    media_job_concurrency: int = Field(default=1, ge=1, le=8)
    submission_finalization_interval_seconds: float = Field(default=0.25, gt=0)

    _empty_log_file = field_validator("log_file", mode="before")(_empty_to_none)
    _validate_log_file = field_validator("log_file")(_validate_relative_log_file)


class CommunityConfig(_FrozenModel):
    """Discord channel and user IDs used by community automation."""

    redstoner_starboard_author_id: int = 700796664276844612
    redstoner_starboard_channel_id: int = 1332630008270684241
    redstoner_role_id: int = 433670432420397060
    redstoner_corner_channel_id: int = 534945678850523138
    redstoner_announcement_channel_id: int = 433643026204852224
    welcome_channel_id: int = 1356094722531393680
    welcome_relay_channel_id: int = 433618741528625155
    version_tracker_channel_id: int = 1334168723170263122
    build_log_channel_ids: tuple[int, ...] = (726156829629087814, 667401499554611210)


class CatboxConfig(_FrozenModel):
    """Optional Catbox account configuration."""

    user_hash: SecretStr | None = None

    _empty_user_hash = field_validator("user_hash", mode="before")(_empty_to_none)


class GoogleConfig(_FrozenModel):
    """Optional Google service-account credential configuration."""

    credentials_json: SecretStr | None = Field(default=None, repr=False)
    credentials_file: Path | None = None
    _credentials_info: dict[str, object] | None = PrivateAttr(default=None)

    _empty_credentials_json = field_validator("credentials_json", mode="before")(_empty_to_none)
    _empty_credentials_file = field_validator("credentials_file", mode="before")(_empty_to_none)

    @model_validator(mode="after")
    def _load_credentials(self) -> Self:
        if self.credentials_json is not None and self.credentials_file is not None:
            msg = "Configure either credentials_json or credentials_file, not both."
            raise ValueError(msg)

        if self.credentials_json is None and self.credentials_file is None:
            return self

        try:
            if self.credentials_json is not None:
                raw_credentials = self.credentials_json.get_secret_value()
            else:
                assert self.credentials_file is not None
                raw_credentials = self.credentials_file.read_text(encoding="utf-8")
            typed_info = _parse_google_credentials(raw_credentials)
            Credentials.from_service_account_info(typed_info)
        except (OSError, TypeError, ValueError) as exc:
            msg = "Must contain valid Google service-account credentials."
            raise ValueError(msg) from exc

        object.__setattr__(self, "_credentials_info", typed_info)
        return self

    @property
    def credentials_info(self) -> Mapping[str, object] | None:
        """Return validated service-account data without exposing it in model output."""
        return self._credentials_info


class SchematicConfig(_FrozenModel):
    """Schematic engine resource budgets and worker supervision settings.

    Every field is reachable in a single `env_nested_delimiter` split, e.g.
    `SQUID_SCHEMATIC_WORKERS`. Two-level names such as `schematic.worker.count` would not
    resolve, because `_ProcessSettings` sets `env_nested_max_split=1`.
    """

    enabled: bool = True
    """Whether to use the native engine at all, even when it is installed."""
    workers: int = Field(default=2, ge=1, le=8)
    """How many supervised worker subprocesses to run."""
    job_poll_interval_seconds: float = Field(default=0.2, gt=0, le=5)
    """How often API and bot clients poll a submitted durable job."""
    job_wait_timeout_seconds: float = Field(default=120.0, gt=0)
    """Maximum client wait; jobs remain durable if the caller stops waiting."""
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    job_retention_hours: int = Field(default=24, ge=1, le=168)
    max_job_artifact_bytes: int = Field(default=64 * 1024 * 1024, ge=1)

    max_upload_bytes: int = Field(default=16 * 1024 * 1024, ge=1)
    """Largest attachment accepted, checked before the file is downloaded from Discord."""
    max_inflated_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    """Largest inflated size accepted, enforced while streaming decompression."""
    max_allocated_volume: int = Field(default=20_000_000, ge=1)
    """Largest allocated bounding box accepted, checked in the worker right after loading."""
    max_axis_length: int = Field(default=512, ge=1)
    """Largest allocated extent accepted on any single axis."""
    lattice_max_block_count: int = Field(default=200_000, ge=0)
    """Block count above which repeating-structure detection is skipped as too expensive."""

    parse_timeout_seconds: float = Field(default=5.0, gt=0)
    compare_timeout_seconds: float = Field(default=15.0, gt=0)
    convert_timeout_seconds: float = Field(default=15.0, gt=0)
    render_timeout_seconds: float = Field(default=45.0, gt=0)
    simulate_timeout_seconds: float = Field(default=90.0, gt=0)

    render_enabled: bool = False
    """Whether analyzed primary schematics should receive generated preview images."""
    render_pack_path: Path | None = None
    """Operator-supplied resource-pack zip on the local filesystem."""
    render_pack_url: AnyHttpUrl | None = None
    """Operator-supplied resource-pack URL, fetched lazily and cached after verification."""
    render_pack_sha256: str | None = None
    """Expected lowercase SHA-256. Required for remote packs; local packs may derive it."""
    render_public_base_url: AnyHttpUrl | None = None
    """Public API origin used for stable worker-published PNG URLs."""
    render_cache_dir: Path = Field(
        default_factory=lambda: (
            Path(os.environ.get("XDG_CACHE_HOME", Path.cwd() / ".cache")) / "redstone-squid" / "schematics"
        )
    )
    render_width: int = Field(default=768, ge=64, le=4096)
    render_height: int = Field(default=768, ge=64, le=4096)
    render_max_block_count: int = Field(default=400_000, ge=1)
    render_max_bounding_volume: int = Field(default=2_000_000, ge=1)
    render_background: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    duplicate_metric_tolerance: float = Field(default=0.2, gt=0, le=1)
    """Relative block-count and dimension tolerance for the fuzzy SQL shortlist."""
    duplicate_near_distance: float = Field(default=1.0, gt=0)
    """Largest shape footprint distance surfaced as a near duplicate."""
    duplicate_max_comparisons: int = Field(default=5, ge=0, le=25)
    """Maximum pairwise engine comparisons for one submission."""
    duplicate_result_limit: int = Field(default=3, ge=1, le=10)
    """Maximum duplicate warnings retained on a build and shown to reviewers."""
    duplicate_total_timeout_seconds: float = Field(default=15.0, gt=0)
    """Wall-clock budget shared by all pairwise comparisons for one submission."""

    worker_memory_limit_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1)
    """`RLIMIT_AS` applied in the child before the engine is imported."""
    worker_cpu_seconds: int = Field(default=900, ge=1)
    """Cumulative `RLIMIT_CPU` backstop. Reaching it recycles the worker; the per-operation
    deadline, not this, is what bounds a single request."""
    worker_file_size_limit_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    """`RLIMIT_FSIZE` applied in the child. The worker exchanges bytes over pipes and has no
    legitimate reason to write a large file."""

    restart_backoff_seconds: float = Field(default=1.0, gt=0)
    """Base delay for the exponential backoff between worker restarts."""
    max_restarts_per_window: int = Field(default=5, ge=1)
    restart_window_seconds: float = Field(default=60.0, gt=0)
    """Crashing more than `max_restarts_per_window` times inside this window trips the circuit
    breaker, so a payload a user keeps retrying cannot fork-bomb the host."""

    @field_validator("render_pack_sha256")
    @classmethod
    def _validate_render_pack_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            msg = "Must be a 64-character hexadecimal SHA-256 digest."
            raise ValueError(msg)
        return normalized

    @field_validator("render_background")
    @classmethod
    def _validate_render_background(cls, value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        if any(channel < 0 or channel > 1 for channel in value):
            msg = "Every background channel must be between 0 and 1."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_render_source(self) -> Self:
        if self.render_pack_path is not None and self.render_pack_url is not None:
            msg = "Configure only one of render_pack_path and render_pack_url."
            raise ValueError(msg)
        if self.render_enabled and (
            (self.render_pack_path is None and self.render_pack_url is None) or self.render_public_base_url is None
        ):
            msg = "Rendering requires a resource pack and render_public_base_url."
            raise ValueError(msg)
        if self.render_pack_url is not None and self.render_pack_sha256 is None:
            msg = "Remote render packs require render_pack_sha256."
            raise ValueError(msg)
        return self


class LogConfig(_FrozenModel):
    """Environment-facing shared logging configuration."""

    level: str = "INFO"
    root_level: str | None = None
    directory: Path = Field(default_factory=lambda: Path.cwd() / "logs")

    _validate_level = field_validator("level")(_validate_log_level)
    _empty_root_level = field_validator("root_level", mode="before")(_empty_to_none)
    _validate_root_level = field_validator("root_level")(_validate_log_level)


class DiagnosticsConfig(_FrozenModel):
    """Retention and size limits for stored error reports."""

    retention_hours: int = Field(default=168, ge=1, le=8760)
    """How long a stored error report stays queryable. A week covers the gap between a user
    hitting an error and a moderator getting around to asking about it."""
    log_tail_records: int = Field(default=50, ge=0, le=1000)
    """Log records buffered per correlation ID and attached to a report. Zero disables the
    buffer entirely, and with it the memory it holds on the logging path."""
    max_traceback_chars: int = Field(default=20000, ge=1000, le=200000)
    """Cap on the stored traceback text, so a runaway recursion cannot write an unbounded row."""


class LoggingConfig(_FrozenModel):
    """Resolved logging settings for one process."""

    level: str
    root_level: str
    directory: Path
    log_file: str | None
    access_log_file: str | None
    tail_records: int = 0
    """Records the correlated log buffer keeps per correlation ID; zero leaves it uninstalled."""


class ObservabilityConfig(_FrozenModel):
    """Optional OTLP trace export settings shared by all application processes."""

    enabled: bool = False
    endpoint: AnyHttpUrl | None = None
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    service_name: str = Field(default="redstone-squid", min_length=1)
    environment: str = Field(default="development", min_length=1)
    release: str | None = None

    _empty_endpoint = field_validator("endpoint", mode="before")(_empty_to_none)
    _empty_release = field_validator("release", mode="before")(_empty_to_none)

    @field_validator("service_name", "environment")
    @classmethod
    def _normalize_resource_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "Must contain a non-whitespace resource name."
            raise ValueError(msg)
        return normalized

    @model_validator(mode="after")
    def _require_endpoint_when_enabled(self) -> Self:
        if self.enabled and self.endpoint is None:
            msg = "Enabled observability requires an OTLP endpoint."
            raise ValueError(msg)
        return self


class BuildConfig(_FrozenModel):
    """Optional source-build metadata displayed by the bot."""

    commit_hash: str | None = None
    commit_message: str | None = None

    _empty_values = field_validator("commit_hash", "commit_message", mode="before")(_empty_to_none)

    @model_validator(mode="after")
    def _require_metadata_pair(self) -> Self:
        if (self.commit_hash is None) != (self.commit_message is None):
            msg = "commit_hash and commit_message must be configured together."
            raise ValueError(msg)
        return self


class NotificationConfig(_FrozenModel):
    """User notification links and retention policy."""

    public_site_url: AnyHttpUrl | None = None
    retention_days: int = Field(default=90, ge=1, le=3650)

    _empty_public_site_url = field_validator("public_site_url", mode="before")(_empty_to_none)


class BotIdentityConfig(_FrozenModel):
    """Code-owned identity and policy values for the Discord bot."""

    prefix: str = "!"
    owner_id: int | None = 353089661175988224
    owner_server_id: int | None = 433618741528625152
    bot_name: str = "Redstone Squid"
    bot_version: str = "1.5.7"
    source_code_url: str = "https://github.com/redstone-squid/Redstone-Squid"


class RuntimeConfig(_FrozenModel):
    """Infrastructure configuration for the application service graph."""

    database: DatabaseConfig
    openai: OpenAIConfig
    embeddings: EmbeddingConfig
    schematics: SchematicConfig
    object_storage: ObjectStorageConfig
    media: MediaConfig = MediaConfig()
    minecraft_auth: MinecraftAuthConfig = MinecraftAuthConfig()
    cli_auth: CliAuthConfig = CliAuthConfig()
    community: CommunityConfig
    notifications: NotificationConfig
    verification_code_pepper: SecretStr
    api_key_pepper: SecretStr | None = None
    discord_bot_token: SecretStr | None = None
    session_pepper: SecretStr | None = None
    idempotency_encryption: IdempotencyEncryptionConfig | None = None
    oauth: OAuthConfig | None = None
    upstream_http: UpstreamHttpConfig = UpstreamHttpConfig()
    diagnostics: DiagnosticsConfig = DiagnosticsConfig()


class _FilteredEnvironmentSource(PydanticBaseSettingsSource):
    """Discard audited environment-only keys before nested model validation."""

    def __init__(self, settings_cls: type[BaseSettings], source: PydanticBaseSettingsSource) -> None:
        super().__init__(settings_cls)
        self._source = source

    @override
    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._source.get_field_value(field, field_name)

    @override
    def __call__(self) -> dict[str, Any]:
        filtered: dict[str, Any] = {}
        for name, value in self._source().items():
            field = self.settings_cls.model_fields.get(name)
            if field is None:
                continue
            annotation = field.annotation
            if isinstance(value, dict) and isinstance(annotation, type) and issubclass(annotation, BaseModel):
                allowed = {nested.casefold() for nested in annotation.model_fields}
                value = {nested: item for nested, item in value.items() if nested.casefold() in allowed}
            filtered[name] = value
        return filtered


class _ProcessSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SQUID_",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        validate_default=True,
    )

    database: DatabaseConfig
    verification: VerificationConfig
    openai: OpenAIConfig = OpenAIConfig()
    embedding: EmbeddingProviderConfig = EmbeddingProviderConfig()
    storage: ObjectStorageConfig = ObjectStorageConfig()
    media: MediaConfig = MediaConfig()
    minecraft_auth: MinecraftAuthConfig = MinecraftAuthConfig()
    cli_auth: CliAuthConfig = CliAuthConfig()
    schematic: SchematicConfig = SchematicConfig()
    community: CommunityConfig = CommunityConfig()
    notification: NotificationConfig = NotificationConfig()
    upstream_http: UpstreamHttpConfig = UpstreamHttpConfig()
    log: LogConfig = LogConfig()
    observability: ObservabilityConfig = ObservabilityConfig()
    diagnostics: DiagnosticsConfig = DiagnosticsConfig()
    strict_unknown_keys: bool = False
    """Reject unknown ``SQUID_*`` names instead of logging and ignoring them."""

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Filter only environment sources; explicit model input remains strict."""
        return (
            init_settings,
            _FilteredEnvironmentSource(settings_cls, env_settings),
            _FilteredEnvironmentSource(settings_cls, dotenv_settings),
            file_secret_settings,
        )

    @property
    def runtime(self) -> RuntimeConfig:
        """Return the runtime settings, already resolved and free of any web framework."""
        return RuntimeConfig(
            database=self.database,
            openai=self.openai,
            embeddings=EmbeddingConfig(
                api_key=self.embedding.api_key or self.openai.api_key,
                base_url=self.embedding.base_url or self.openai.base_url,
                model=self.embedding.model,
            ),
            schematics=self.schematic,
            object_storage=self.storage,
            media=self.media,
            minecraft_auth=self.minecraft_auth,
            cli_auth=self.cli_auth,
            community=self.community,
            notifications=self.notification,
            verification_code_pepper=self.verification.code_pepper,
            upstream_http=self.upstream_http,
            diagnostics=self.diagnostics,
        )


class BotProcessConfig(_ProcessSettings):
    """Validated configuration required by the Discord process."""

    development_mode: bool = False
    discord: DiscordConfig
    bot: BotConfig = BotConfig()
    catbox: CatboxConfig = CatboxConfig()
    google: GoogleConfig = GoogleConfig()
    build: BuildConfig = BuildConfig()

    @property
    def logging(self) -> LoggingConfig:
        return LoggingConfig(
            level=self.log.level,
            root_level=self.log.root_level or ("INFO" if self.development_mode else "WARNING"),
            directory=self.log.directory,
            log_file=self.bot.log_file,
            access_log_file=None,
            tail_records=self.diagnostics.log_tail_records,
        )


class ApiProcessConfig(_ProcessSettings):
    """Validated configuration required by the HTTP API process."""

    api: ApiConfig
    oauth: OAuthConfig = OAuthConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()

    @property
    @override
    def runtime(self) -> RuntimeConfig:
        """Return runtime settings including API-only credential adapters."""
        return super().runtime.model_copy(
            update={
                "api_key_pepper": self.api.key_pepper,
                "discord_bot_token": self.api.bot_token,
                "session_pepper": self.api.session_pepper,
                "idempotency_encryption": self.api.idempotency_encryption,
                "oauth": self.oauth,
            }
        )

    @property
    def logging(self) -> LoggingConfig:
        return LoggingConfig(
            level=self.log.level,
            root_level=self.log.root_level or "INFO",
            directory=self.log.directory,
            log_file=self.api.log_file,
            access_log_file=self.api.access_log_file,
            tail_records=self.diagnostics.log_tail_records,
        )


class WorkerProcessConfig(_ProcessSettings):
    """Validated configuration required by the database worker process."""

    worker: WorkerConfig = WorkerConfig()

    @property
    def logging(self) -> LoggingConfig:
        return LoggingConfig(
            level=self.log.level,
            root_level=self.log.root_level or "INFO",
            directory=self.log.directory,
            log_file=self.worker.log_file,
            access_log_file=None,
            tail_records=self.diagnostics.log_tail_records,
        )


class ApplicationConfig(BotProcessConfig):
    """Complete configuration required by the combined application launcher."""

    api: ApiConfig
    oauth: OAuthConfig = OAuthConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    worker: WorkerConfig = WorkerConfig()

    def bot_process(self) -> BotProcessConfig:
        """Project the combined settings into the Discord process."""
        return BotProcessConfig.model_validate(
            self.model_dump(
                include={
                    "database",
                    "verification",
                    "openai",
                    "embedding",
                    "storage",
                    "schematic",
                    "media",
                    "minecraft_auth",
                    "cli_auth",
                    "community",
                    "notification",
                    "log",
                    "observability",
                    "strict_unknown_keys",
                    "development_mode",
                    "discord",
                    "bot",
                    "catbox",
                    "google",
                    "build",
                    "upstream_http",
                }
            )
        )

    def api_process(self) -> ApiProcessConfig:
        """Project the combined settings into the HTTP API process."""
        return ApiProcessConfig.model_validate(
            self.model_dump(
                include={
                    "database",
                    "verification",
                    "openai",
                    "embedding",
                    "storage",
                    "schematic",
                    "media",
                    "minecraft_auth",
                    "cli_auth",
                    "community",
                    "notification",
                    "log",
                    "observability",
                    "strict_unknown_keys",
                    "oauth",
                    "api",
                    "rate_limit",
                    "upstream_http",
                }
            )
        )

    def worker_process(self) -> WorkerProcessConfig:
        """Project the combined settings into the database worker process."""
        return WorkerProcessConfig.model_validate(
            self.model_dump(
                include={
                    "database",
                    "verification",
                    "openai",
                    "embedding",
                    "storage",
                    "schematic",
                    "media",
                    "minecraft_auth",
                    "cli_auth",
                    "community",
                    "notification",
                    "log",
                    "observability",
                    "strict_unknown_keys",
                    "worker",
                    "upstream_http",
                }
            )
        )


_DOTENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


_RETIRED_ENVIRONMENT_KEYS = frozenset({"SQUID_CURSOR_SECRET"})
"""Keys a deployment may still be setting for a feature that has since been removed.

They are accepted silently rather than reported: under `SQUID_STRICT_UNKNOWN_KEYS` an unknown key
is a boot failure, so a leftover in an env file or a compose service would take the process down
during a deploy that was otherwise a no-op for it.
"""


def _known_environment_keys() -> frozenset[str]:
    """Return the global SQUID key contract shared by every process."""
    keys: set[str] = set()
    for name, field in ApplicationConfig.model_fields.items():
        prefix = f"SQUID_{name.upper()}"
        keys.add(prefix)
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            keys.update(f"{prefix}_{nested.upper()}" for nested in annotation.model_fields)
    return frozenset(keys)


def _environment_group(field: str) -> tuple[str, tuple[str, ...]] | None:
    """Return the ``SQUID_`` prefix and required member names of a nested settings group."""
    model_field = ApplicationConfig.model_fields.get(field)
    if model_field is None:
        return None
    annotation = model_field.annotation
    if not (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
        return None
    prefix = f"SQUID_{field.upper()}"
    required = tuple(
        f"{prefix}_{name.upper()}" for name, nested in annotation.model_fields.items() if nested.is_required()
    )
    return prefix, required


def _setting_name(field: str, issue_type: str) -> str:
    """Render a validation location as the ``SQUID_*`` name(s) an operator can act on.

    Every character of the result comes from a declared field name. A validation location is
    not a settings path: its tail can be a configured dictionary key, and a validator that
    rejects a whole model reports no leaf at all, so neither the raw location nor a name
    assembled from it can be shown. A variable that does not exist would be set and then
    silently ignored, which is worse than naming the group.
    """
    if field.upper().startswith("SQUID_"):
        return field  # The unknown-key audit already reports literal variable names.
    known = _known_environment_keys()
    top, _, remainder = field.partition(".")
    if (group := _environment_group(top)) is None:
        scalar = f"SQUID_{top.upper()}"
        return scalar if scalar in known else "SQUID_*"
    prefix, required = group
    if not remainder:
        # "SQUID_DATABASE_*: Field required" would not tell anyone that the missing variable
        # is SQUID_DATABASE_URL, so a missing group names its members instead.
        return ", ".join(required) if issue_type == "missing" and required else f"{prefix}_*"
    candidate = f"{prefix}_{remainder.partition('.')[0].upper()}"
    return candidate if candidate in known else f"{prefix}_*"


def _issues_message(summary: str, issues: list[dict[str, str]]) -> str:
    """Render issues as an operator-readable list beneath `summary`."""
    lines = [f"{summary}:"]
    for issue in issues:
        message = issue["message"].removeprefix("Value error, ")
        if not message.endswith((".", "?", "!")):
            message = f"{message}."
        lines.append(f"  - {_setting_name(issue['field'], issue['type'])}: {message}")
    return "\n".join(lines)


def _configured_environment_keys() -> set[str]:
    """Read configured key names without parsing or retaining their values."""
    names = {name for name in os.environ if name.upper().startswith("SQUID_")}
    dotenv = Path(".env")
    try:
        lines = dotenv.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return names
    except OSError:
        logger.warning("Could not audit SQUID key names in the dotenv file")
        return names
    for line in lines:
        if match := _DOTENV_KEY.match(line):
            name = match.group(1)
            if name.upper().startswith("SQUID_"):
                names.add(name)
    return names


def _audit_unknown_environment_keys(*, strict: bool) -> None:
    """Report likely deployment typos while accepting sibling-process keys."""
    known = _known_environment_keys() | _RETIRED_ENVIRONMENT_KEYS
    unknown = sorted(name for name in _configured_environment_keys() if name.upper() not in known)
    if not unknown:
        return
    suggestions = {
        name: match[0] for name in unknown if (match := get_close_matches(name.upper(), known, n=1, cutoff=0.8))
    }
    if strict:
        issues = [
            {
                "field": name,
                "message": (
                    f"Unknown configuration key; did you mean {suggestions[name]}?"
                    if name in suggestions
                    else "Unknown configuration key."
                ),
                "type": "unknown_key",
            }
            for name in unknown
        ]
        message = _issues_message(f"Application configuration has {len(issues)} unknown key(s)", issues)
        raise ConfigurationError(
            message,
            context={
                "issues": cast(list[Mapping[str, Any]], issues),
                "unknown_keys": tuple(unknown),
                "suggestions": suggestions,
            },
            developer_action="Correct or remove the listed SQUID_* settings and restart the process.",
        )
    logger.warning(
        "Unknown SQUID configuration key names will be ignored",
        extra={
            "squid.config.unknown_keys": tuple(unknown),
            "squid.config.suggestions": suggestions,
        },
    )


def _configuration_error(exc: ValidationError | SettingsError) -> ConfigurationError:
    if isinstance(exc, ValidationError):
        issues: list[dict[str, str]] = []
        for error in exc.errors(include_input=False, include_url=False):
            location = ".".join(str(part) for part in error["loc"])
            issues.append(
                {
                    "field": location,
                    "message": str(error["msg"]),
                    "type": str(error["type"]),
                }
            )
    else:
        # SettingsError names the field it choked on but nothing else; its message never
        # carries the value, and its __cause__ (a JSONDecodeError, say) can, so only the
        # field name is lifted out of it.
        located = re.search(r'field "([^"]+)"', str(exc))
        field = located.group(1) if located is not None else "environment"
        issues = [{"field": field, "message": "Could not parse the configured value.", "type": "settings"}]

    return ConfigurationError(
        _issues_message(f"Application configuration has {len(issues)} error(s)", issues),
        context={"issues": cast(list[Mapping[str, Any]], issues)},
        developer_action="Correct the listed SQUID_* settings and restart the process.",
    )


def _load_settings[ConfigT: _ProcessSettings](config_type: type[ConfigT]) -> ConfigT:
    try:
        config = config_type()  # type: ignore[call-arg]
    except (ValidationError, SettingsError) as exc:
        raise _configuration_error(exc) from None
    _audit_unknown_environment_keys(strict=config.strict_unknown_keys)
    return config


def load_or_exit[ConfigT](loader: Callable[[], ConfigT]) -> ConfigT:
    """Load a process configuration, reporting a boot failure without a traceback.

    Logging is configured from the very settings being loaded, so the report goes straight to
    stderr. The guard covers one call and one exception type: a configuration error raised
    later, by real work, still surfaces with its traceback.
    """
    try:
        return loader()
    except ConfigurationError as exc:
        print(exc.backend_detail(), file=sys.stderr)
        raise SystemExit(1) from None


def load_application_config() -> ApplicationConfig:
    """Load and validate settings for the combined application launcher."""
    return _load_settings(ApplicationConfig)


def load_bot_process_config() -> BotProcessConfig:
    """Load and validate settings for the standalone Discord process."""
    return _load_settings(BotProcessConfig)


def load_api_process_config() -> ApiProcessConfig:
    """Load and validate settings for the standalone HTTP API process."""
    return _load_settings(ApiProcessConfig)


def load_worker_process_config() -> WorkerProcessConfig:
    """Load and validate settings for the standalone database worker process."""
    return _load_settings(WorkerProcessConfig)


def load_database_config() -> DatabaseConfig:
    """Load only the database settings needed by migration tooling."""

    class DatabaseSettings(BaseSettings):
        model_config = _ProcessSettings.model_config
        database: DatabaseConfig
        strict_unknown_keys: bool = False

    try:
        settings = DatabaseSettings()  # type: ignore[call-arg]
    except (ValidationError, SettingsError) as exc:
        raise _configuration_error(exc) from None
    _audit_unknown_environment_keys(strict=settings.strict_unknown_keys)
    return settings.database


def load_worker_observability_config() -> ObservabilityConfig:
    """Load only inherited observability settings in a schematic worker child."""

    class WorkerObservabilitySettings(BaseSettings):
        model_config = _ProcessSettings.model_config
        observability: ObservabilityConfig = ObservabilityConfig()
        strict_unknown_keys: bool = False

    try:
        settings = WorkerObservabilitySettings()  # type: ignore[call-arg]
    except (ValidationError, SettingsError) as exc:
        raise _configuration_error(exc) from None
    _audit_unknown_environment_keys(strict=settings.strict_unknown_keys)
    return settings.observability


def load_worker_log_config() -> LogConfig:
    """Load only inherited logging settings in a schematic worker child."""

    class WorkerLogSettings(BaseSettings):
        model_config = _ProcessSettings.model_config
        log: LogConfig = LogConfig()
        strict_unknown_keys: bool = False

    try:
        settings = WorkerLogSettings()  # type: ignore[call-arg]
    except (ValidationError, SettingsError) as exc:
        raise _configuration_error(exc) from None
    _audit_unknown_environment_keys(strict=settings.strict_unknown_keys)
    return settings.log


__all__ = [
    "EMBEDDING_DIMENSION",
    "ApiProcessConfig",
    "ApplicationConfig",
    "BotIdentityConfig",
    "BotProcessConfig",
    "BuildConfig",
    "CatboxConfig",
    "CommunityConfig",
    "DatabaseConfig",
    "DiagnosticsConfig",
    "EmbeddingConfig",
    "GoogleConfig",
    "IdempotencyEncryptionConfig",
    "LoggingConfig",
    "NotificationConfig",
    "OAuthConfig",
    "ObservabilityConfig",
    "OpenAIConfig",
    "RateLimitConfig",
    "RuntimeConfig",
    "SchematicConfig",
    "UpstreamHttpConfig",
    "WorkerConfig",
    "WorkerProcessConfig",
    "load_api_process_config",
    "load_application_config",
    "load_bot_process_config",
    "load_database_config",
    "load_worker_log_config",
    "load_worker_observability_config",
    "load_worker_process_config",
]
