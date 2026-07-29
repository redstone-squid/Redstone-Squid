"""Typed startup configuration tests."""

from pathlib import Path

import pytest

from squid.config import ApiProcessConfig, BotProcessConfig, EmbeddingConfig, RuntimeConfig
from squid.core.errors import ConfigurationError

RUNTIME_ENVIRONMENT = {
    "DATABASE_URL": "postgresql://user:password@database.example/squid",
    "DB_DRIVER_SYNC": "psycopg",
    "DB_DRIVER_ASYNC": "asyncpg",
}


def test_runtime_config_groups_adapter_settings() -> None:
    environment = {
        **RUNTIME_ENVIRONMENT,
        "OPENAI_API_KEY": "text-key",
        "OPENAI_BASE_URL": "https://text.example/v1",
        "EMBEDDING_OPENAI_API_KEY": "embedding-key",
        "EMBEDDING_OPENAI_BASE_URL": "https://embedding.example/v1",
        "EMBEDDING_MODEL": "embedding-model",
        "EMBEDDING_DIMENSION": "768",
        "DB_CONNECTION": "postgresql://vector.example/squid",
        "VERIFICATION_CODE_PEPPER": "pepper",
    }

    config = RuntimeConfig.from_environment(environment)

    assert config.database.url == environment["DATABASE_URL"]
    assert config.openai.api_key == "text-key"
    assert config.openai.base_url == "https://text.example/v1"
    assert config.embeddings == EmbeddingConfig(
        api_key="embedding-key",
        base_url="https://embedding.example/v1",
        model="embedding-model",
        dimension=768,
        database_connection="postgresql://vector.example/squid",
    )
    assert config.verification_code_pepper == "pepper"


def test_runtime_config_reports_missing_database_field() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        RuntimeConfig.from_environment({})

    assert exc_info.value.context == {"field": "DATABASE_URL"}


@pytest.mark.parametrize("value", ["not-a-number", "0", "-1"])
def test_embedding_dimension_must_be_a_positive_integer(value: str) -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        EmbeddingConfig.from_environment({"EMBEDDING_DIMENSION": value})

    assert exc_info.value.context == {"field": "EMBEDDING_DIMENSION", "value": value}


def test_bot_process_config_collects_token_runtime_and_logging(tmp_path: Path) -> None:
    config = BotProcessConfig.from_environment(
        {
            **RUNTIME_ENVIRONMENT,
            "BOT_TOKEN": "discord-token",
            "LOG_DIR": str(tmp_path),
        }
    )

    assert config.token == "discord-token"
    assert config.logging.directory == tmp_path
    assert config.logging.log_file == "discord.log"


def test_api_process_config_validates_secret_and_port() -> None:
    config = ApiProcessConfig.from_environment({"SYNERGY_SECRET": "secret", "API_PORT": "9000"})

    assert config.synergy_secret == "secret"
    assert config.port == 9000

    with pytest.raises(ConfigurationError) as exc_info:
        ApiProcessConfig.from_environment({"SYNERGY_SECRET": "secret", "API_PORT": "invalid"})
    assert exc_info.value.context == {"field": "API_PORT", "value": "invalid"}
