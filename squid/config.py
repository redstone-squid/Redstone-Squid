"""Typed process configuration loaded at application boundaries."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from squid.core.errors import ConfigurationError

type Environment = Mapping[str, str]


def _required(environment: Environment, name: str) -> str:
    value = environment.get(name)
    if value:
        return value
    msg = f"No {name} environment variable found."
    raise ConfigurationError(msg, context={"field": name})


def _positive_int(environment: Environment, name: str, default: int) -> int:
    raw_value = environment.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        msg = f"{name} must be an integer."
        raise ConfigurationError(msg, context={"field": name, "value": raw_value}) from exc
    if value <= 0:
        msg = f"{name} must be positive."
        raise ConfigurationError(msg, context={"field": name, "value": raw_value})
    return value


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Relational database connection configuration."""

    url: str
    sync_driver: str
    async_driver: str

    @classmethod
    def from_environment(cls, environment: Environment = os.environ) -> "DatabaseConfig":
        return cls(
            url=_required(environment, "DATABASE_URL"),
            sync_driver=_required(environment, "DB_DRIVER_SYNC"),
            async_driver=_required(environment, "DB_DRIVER_ASYNC"),
        )


@dataclass(frozen=True, slots=True)
class OpenAIConfig:
    """OpenAI-compatible text generation configuration."""

    api_key: str | None
    base_url: str

    @classmethod
    def from_environment(cls, environment: Environment = os.environ) -> "OpenAIConfig":
        return cls(
            api_key=environment.get("OPENAI_API_KEY"),
            base_url=environment.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Embedding provider and vector index configuration."""

    api_key: str | None
    base_url: str | None
    model: str
    dimension: int
    database_connection: str | None

    @classmethod
    def from_environment(cls, environment: Environment = os.environ) -> "EmbeddingConfig":
        return cls(
            api_key=environment.get("EMBEDDING_OPENAI_API_KEY") or environment.get("OPENAI_API_KEY"),
            base_url=environment.get("EMBEDDING_OPENAI_BASE_URL") or environment.get("OPENAI_BASE_URL"),
            model=environment.get("EMBEDDING_MODEL", "text-embedding-3-small"),
            dimension=_positive_int(environment, "EMBEDDING_DIMENSION", 1536),
            database_connection=environment.get("DB_CONNECTION"),
        )


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Shared logging environment configuration."""

    level: str
    root_level: str
    directory: Path
    log_file: str | None
    access_log_file: str | None

    @classmethod
    def from_environment(
        cls,
        environment: Environment = os.environ,
        *,
        default_root_level: str = "WARNING",
        default_log_file: str | None = None,
        default_access_log_file: str | None = None,
    ) -> "LoggingConfig":
        log_dir = environment.get("LOG_DIR")
        return cls(
            level=environment.get("LOG_LEVEL", "INFO"),
            root_level=environment.get("ROOT_LOG_LEVEL", default_root_level),
            directory=Path(log_dir) if log_dir else Path.cwd() / "logs",
            log_file=environment.get("LOG_FILE", default_log_file),
            access_log_file=environment.get("LOG_ACCESS_FILE", default_access_log_file),
        )


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Infrastructure configuration for the application service graph."""

    database: DatabaseConfig
    openai: OpenAIConfig
    embeddings: EmbeddingConfig
    verification_code_pepper: str

    @classmethod
    def from_environment(cls, environment: Environment = os.environ) -> "RuntimeConfig":
        return cls(
            database=DatabaseConfig.from_environment(environment),
            openai=OpenAIConfig.from_environment(environment),
            embeddings=EmbeddingConfig.from_environment(environment),
            verification_code_pepper=environment.get("VERIFICATION_CODE_PEPPER", ""),
        )


@dataclass(frozen=True, slots=True)
class BotProcessConfig:
    """Secrets and infrastructure needed to start the Discord process."""

    token: str
    runtime: RuntimeConfig
    logging: LoggingConfig

    @classmethod
    def from_environment(cls, environment: Environment = os.environ) -> "BotProcessConfig":
        return cls(
            token=_required(environment, "BOT_TOKEN"),
            runtime=RuntimeConfig.from_environment(environment),
            logging=LoggingConfig.from_environment(environment, default_log_file="discord.log"),
        )


@dataclass(frozen=True, slots=True)
class ApiProcessConfig:
    """Secrets and infrastructure needed to start the HTTP API process."""

    synergy_secret: str
    port: int
    logging: LoggingConfig

    @classmethod
    def from_environment(cls, environment: Environment = os.environ) -> "ApiProcessConfig":
        return cls(
            synergy_secret=_required(environment, "SYNERGY_SECRET"),
            port=_positive_int(environment, "API_PORT", 8000),
            logging=LoggingConfig.from_environment(environment, default_root_level="INFO"),
        )


def embedding_dimension_from_environment(environment: Environment = os.environ) -> int:
    """Read the schema-level vector dimension with the shared validation rules."""
    return _positive_int(environment, "EMBEDDING_DIMENSION", 1536)
