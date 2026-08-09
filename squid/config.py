"""Typed process configuration loaded and validated at application boundaries."""

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self, cast, override

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
from sqlalchemy import make_url

from squid.core.errors import ConfigurationError

EMBEDDING_DIMENSION = 1536
"""Vector dimension fixed by the application-owned PostgreSQL schema."""


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

    _validate_url = field_validator("url")(_validate_postgres_url)


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
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    read_timeout_seconds: float = Field(default=30.0, gt=0)
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


class CursorConfig(_FrozenModel):
    """Shared signing material for opaque pagination cursors."""

    secret: SecretStr

    @field_validator("secret")
    @classmethod
    def _require_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < 16:
            msg = "Must contain at least 16 bytes."
            raise ValueError(msg)
        return value


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
    bot_token: SecretStr | None = None
    port: int = Field(default=8000, ge=1, le=65535)
    log_file: str | None = None
    access_log_file: str | None = None
    cors_origins: tuple[str, ...] = ()

    _empty_log_file = field_validator("log_file", "access_log_file", mode="before")(_empty_to_none)
    _empty_bot_token = field_validator("bot_token", mode="before")(_empty_to_none)
    _validate_log_files = field_validator("log_file", "access_log_file")(_validate_relative_log_file)

    @field_validator("secret")
    @classmethod
    def _require_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            msg = "Must not be empty."
            raise ValueError(msg)
        return value

    @field_validator("key_pepper", "session_pepper")
    @classmethod
    def _require_peppers(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < 16:
            msg = "Must contain at least 16 bytes."
            raise ValueError(msg)
        return value


class OAuthConfig(_FrozenModel):
    """Discord OAuth2 authorization-code configuration."""

    discord_client_id: str | None = None
    discord_client_secret: SecretStr | None = None
    redirect_uri: AnyHttpUrl | None = None
    session_ttl_hours: int = Field(default=336, ge=1)

    _empty_client_id = field_validator("discord_client_id", mode="before")(_empty_to_none)
    _empty_client_secret = field_validator("discord_client_secret", mode="before")(_empty_to_none)
    _empty_redirect_uri = field_validator("redirect_uri", mode="before")(_empty_to_none)

    @model_validator(mode="after")
    def _require_complete_credentials(self) -> Self:
        configured = (self.discord_client_id, self.discord_client_secret, self.redirect_uri)
        if any(value is not None for value in configured) and not all(value is not None for value in configured):
            msg = "Discord OAuth client ID, secret, and redirect URI must be configured together."
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

    _empty_log_file = field_validator("log_file", mode="before")(_empty_to_none)
    _validate_log_file = field_validator("log_file")(_validate_relative_log_file)


class CommunityConfig(_FrozenModel):
    """Discord channel and user IDs used by community automation."""

    redstoner_starboard_author_id: int = 700796664276844612
    redstoner_starboard_channel_id: int = 1332630008270684241
    welcome_channel_id: int = 1356094722531393680
    welcome_relay_channel_id: int = 433618741528625155


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

    max_upload_bytes: int = Field(default=2 * 1024 * 1024, ge=1)
    """Largest attachment accepted, checked before the file is downloaded from Discord."""
    max_inflated_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    """Largest inflated size accepted, enforced while streaming decompression."""
    max_allocated_volume: int = Field(default=20_000_000, ge=1)
    """Largest allocated bounding box accepted, checked in the worker right after loading."""
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
        if self.render_enabled and self.render_pack_path is None and self.render_pack_url is None:
            msg = "Rendering requires render_pack_path or render_pack_url."
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


class LoggingConfig(_FrozenModel):
    """Resolved logging settings for one process."""

    level: str
    root_level: str
    directory: Path
    log_file: str | None
    access_log_file: str | None


class ObservabilityConfig(_FrozenModel):
    """Optional OTLP trace export settings shared by all application processes."""

    enabled: bool = False
    endpoint: AnyHttpUrl | None = None
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    service_name: str = Field(default="redstone-squid", min_length=1)

    _empty_endpoint = field_validator("endpoint", mode="before")(_empty_to_none)

    @field_validator("service_name")
    @classmethod
    def _normalize_service_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "Must contain a non-whitespace service name."
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
    community: CommunityConfig
    verification_code_pepper: SecretStr
    cursor_secret: SecretStr
    api_key_pepper: SecretStr | None = None
    discord_bot_token: SecretStr | None = None
    session_pepper: SecretStr | None = None
    oauth: OAuthConfig | None = None


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
    cursor: CursorConfig
    openai: OpenAIConfig = OpenAIConfig()
    embedding: EmbeddingProviderConfig = EmbeddingProviderConfig()
    storage: ObjectStorageConfig = ObjectStorageConfig()
    schematic: SchematicConfig = SchematicConfig()
    community: CommunityConfig = CommunityConfig()
    log: LogConfig = LogConfig()
    observability: ObservabilityConfig = ObservabilityConfig()

    @property
    def runtime(self) -> RuntimeConfig:
        """Return the resolved framework-neutral runtime settings."""
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
            community=self.community,
            verification_code_pepper=self.verification.code_pepper,
            cursor_secret=self.cursor.secret,
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
        )


class ApiProcessConfig(_ProcessSettings):
    """Validated configuration required by the HTTP API process."""

    api: ApiConfig
    oauth: OAuthConfig = OAuthConfig()

    @property
    @override
    def runtime(self) -> RuntimeConfig:
        """Return runtime settings including API-only credential adapters."""
        return super().runtime.model_copy(
            update={
                "api_key_pepper": self.api.key_pepper,
                "discord_bot_token": self.api.bot_token,
                "session_pepper": self.api.session_pepper,
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
        )


class ApplicationConfig(BotProcessConfig):
    """Complete configuration required by the combined application launcher."""

    api: ApiConfig
    oauth: OAuthConfig = OAuthConfig()
    worker: WorkerConfig = WorkerConfig()

    def bot_process(self) -> BotProcessConfig:
        """Project the combined settings into the Discord process."""
        return BotProcessConfig.model_validate(
            self.model_dump(
                include={
                    "database",
                    "verification",
                    "cursor",
                    "openai",
                    "embedding",
                    "vector",
                    "schematic",
                    "community",
                    "log",
                    "observability",
                    "development_mode",
                    "discord",
                    "bot",
                    "catbox",
                    "google",
                    "build",
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
                    "cursor",
                    "openai",
                    "embedding",
                    "vector",
                    "schematic",
                    "community",
                    "log",
                    "observability",
                    "oauth",
                    "api",
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
                    "cursor",
                    "openai",
                    "embedding",
                    "vector",
                    "schematic",
                    "community",
                    "log",
                    "observability",
                    "worker",
                }
            )
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
        issues = [{"field": "environment", "message": "Could not parse a configured value.", "type": "settings"}]

    return ConfigurationError(
        f"Application configuration has {len(issues)} error(s).",
        context={"issues": cast(list[Mapping[str, Any]], issues)},
        developer_action="Correct the listed SQUID_* settings and restart the process.",
    )


def _load_settings[ConfigT: _ProcessSettings](config_type: type[ConfigT]) -> ConfigT:
    try:
        return config_type()  # type: ignore[call-arg]
    except (ValidationError, SettingsError) as exc:
        raise _configuration_error(exc) from None


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

    try:
        return DatabaseSettings().database  # type: ignore[call-arg]
    except (ValidationError, SettingsError) as exc:
        raise _configuration_error(exc) from None


def load_worker_observability_config() -> ObservabilityConfig:
    """Load only inherited observability settings in a schematic worker child."""

    class WorkerObservabilitySettings(BaseSettings):
        model_config = _ProcessSettings.model_config
        observability: ObservabilityConfig = ObservabilityConfig()

    try:
        return WorkerObservabilitySettings().observability  # type: ignore[call-arg]
    except (ValidationError, SettingsError) as exc:
        raise _configuration_error(exc) from None


__all__ = [
    "EMBEDDING_DIMENSION",
    "ApiProcessConfig",
    "ApplicationConfig",
    "BotIdentityConfig",
    "BotProcessConfig",
    "BuildConfig",
    "CatboxConfig",
    "CommunityConfig",
    "CursorConfig",
    "DatabaseConfig",
    "EmbeddingConfig",
    "GoogleConfig",
    "LoggingConfig",
    "OAuthConfig",
    "ObservabilityConfig",
    "OpenAIConfig",
    "RuntimeConfig",
    "SchematicConfig",
    "WorkerConfig",
    "WorkerProcessConfig",
    "load_api_process_config",
    "load_application_config",
    "load_bot_process_config",
    "load_database_config",
    "load_worker_observability_config",
    "load_worker_process_config",
]
