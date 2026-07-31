"""Typed startup configuration tests."""

from pathlib import Path
from typing import cast

import pytest

from squid.config import (
    EMBEDDING_DIMENSION,
    ApplicationConfig,
    load_api_process_config,
    load_application_config,
    load_bot_process_config,
)
from squid.core.errors import ConfigurationError

BASE_ENVIRONMENT = {
    "SQUID_DATABASE_URL": "postgresql://user:password@database.example/squid",
    "SQUID_VERIFICATION_CODE_PEPPER": "verification-pepper",
}


def _issues(error: ConfigurationError) -> list[dict[str, str]]:
    return cast(list[dict[str, str]], error.context["issues"])


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)


def _set_environment(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name, value in {**BASE_ENVIRONMENT, **values}.items():
        monkeypatch.setenv(name, value)


def test_application_config_groups_and_resolves_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_API_SECRET="api-secret",
        SQUID_API_PORT="9000",
        SQUID_DEVELOPMENT_MODE="true",
        SQUID_OPENAI_API_KEY="text-key",
        SQUID_OPENAI_BASE_URL="https://text.example/v1",
        SQUID_EMBEDDING_API_KEY="embedding-key",
        SQUID_EMBEDDING_BASE_URL="https://embedding.example/v1",
        SQUID_EMBEDDING_MODEL="embedding-model",
        SQUID_VECTOR_DATABASE_URL="postgresql://vector.example/squid",
        SQUID_LOG_DIRECTORY=str(tmp_path),
        SQUID_BOT_LOG_FILE="bot/discord.log",
        SQUID_API_LOG_FILE="api.log",
        SQUID_API_ACCESS_LOG_FILE="access.log",
        SQUID_BUILD_COMMIT_HASH="abcdef123456",
        SQUID_BUILD_COMMIT_MESSAGE="configuration rewrite",
    )

    config = load_application_config()

    assert isinstance(config, ApplicationConfig)
    assert config.database.url.get_secret_value() == BASE_ENVIRONMENT["SQUID_DATABASE_URL"]
    assert config.runtime.openai.api_key is not None
    assert config.runtime.openai.api_key.get_secret_value() == "text-key"
    assert config.runtime.embeddings.api_key is not None
    assert config.runtime.embeddings.api_key.get_secret_value() == "embedding-key"
    assert str(config.runtime.embeddings.base_url) == "https://embedding.example/v1"
    assert config.runtime.embeddings.model == "embedding-model"
    assert config.runtime.embeddings.database_connection is not None
    assert config.runtime.embeddings.database_connection.get_secret_value() == "postgresql://vector.example/squid"
    assert config.bot_process().logging.root_level == "INFO"
    assert config.bot_process().logging.log_file == "bot/discord.log"
    assert config.api_process().api.port == 9000
    assert config.api_process().logging.access_log_file == "access.log"
    assert config.build.commit_hash == "abcdef123456"
    assert EMBEDDING_DIMENSION == 1536


def test_embedding_credentials_fall_back_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_OPENAI_API_KEY="shared-key",
        SQUID_OPENAI_BASE_URL="https://shared.example/v1",
    )

    config = load_bot_process_config()

    assert config.runtime.embeddings.api_key is config.openai.api_key
    assert config.runtime.embeddings.base_url == config.openai.base_url


def test_process_loaders_require_only_their_own_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(monkeypatch, SQUID_DISCORD_TOKEN="discord-token")
    assert load_bot_process_config().discord.token.get_secret_value() == "discord-token"

    monkeypatch.delenv("SQUID_DISCORD_TOKEN")
    monkeypatch.setenv("SQUID_API_SECRET", "api-secret")
    assert load_api_process_config().api.secret.get_secret_value() == "api-secret"


def test_configuration_reports_all_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*BASE_ENVIRONMENT, "SQUID_DISCORD_TOKEN", "SQUID_API_SECRET"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigurationError) as exc_info:
        load_application_config()

    issues = _issues(exc_info.value)
    fields = {issue["field"] for issue in issues}
    assert fields == {"database", "verification", "discord", "api"}


def test_configuration_errors_do_not_expose_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_value = "postgresql://user:super-secret-password@database.example/squid"
    _set_environment(
        monkeypatch,
        SQUID_DATABASE_URL=secret_value,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_API_SECRET="api-secret",
        SQUID_API_PORT="not-a-port",
        SQUID_LOG_LEVEL="very-loud",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_application_config()

    rendered_error = f"{exc_info.value} {exc_info.value.context}"
    assert "super-secret-password" not in rendered_error
    assert "not-a-port" not in rendered_error
    assert "very-loud" not in rendered_error


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_api_port_is_bounded(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    _set_environment(monkeypatch, SQUID_API_SECRET="api-secret", SQUID_API_PORT=port)

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    assert any(issue["field"] == "api.port" for issue in _issues(exc_info.value))


def test_google_credentials_are_mutually_exclusive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text("{}", encoding="utf-8")
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_GOOGLE_CREDENTIALS_JSON="{}",
        SQUID_GOOGLE_CREDENTIALS_FILE=str(credentials_file),
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_bot_process_config()

    assert any(issue["field"] == "google" for issue in _issues(exc_info.value))


@pytest.mark.parametrize("log_file", ["/absolute/application.log", "../outside.log"])
def test_log_files_must_stay_beneath_log_directory(
    monkeypatch: pytest.MonkeyPatch,
    log_file: str,
) -> None:
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_BOT_LOG_FILE=log_file,
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_bot_process_config()

    assert any(issue["field"] == "bot.log_file" for issue in _issues(exc_info.value))
