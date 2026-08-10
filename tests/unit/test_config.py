"""Typed startup configuration tests."""

import logging
from pathlib import Path
from typing import cast

import pytest

from squid.config import (
    EMBEDDING_DIMENSION,
    ApplicationConfig,
    load_api_process_config,
    load_application_config,
    load_bot_process_config,
    load_worker_observability_config,
)
from squid.core.errors import ConfigurationError

BASE_ENVIRONMENT = {
    "SQUID_DATABASE_URL": "postgresql://user:password@database.example/squid",
    "SQUID_VERIFICATION_CODE_PEPPER": "verification-pepper",
    "SQUID_CURSOR_SECRET": "cursor-secret-for-tests",
    "SQUID_API_KEY_PEPPER": "api-key-pepper-for-tests",
    "SQUID_API_SESSION_PEPPER": "session-pepper-for-tests",
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
        SQUID_DATABASE_LISTENER_URL="postgresql://listener:password@database.example/squid",
        SQUID_API_SECRET="api-secret",
        SQUID_API_PORT="9000",
        SQUID_DEVELOPMENT_MODE="true",
        SQUID_OPENAI_API_KEY="text-key",
        SQUID_OPENAI_BASE_URL="https://text.example/v1",
        SQUID_EMBEDDING_API_KEY="embedding-key",
        SQUID_EMBEDDING_BASE_URL="https://embedding.example/v1",
        SQUID_EMBEDDING_MODEL="embedding-model",
        SQUID_LOG_DIRECTORY=str(tmp_path),
        SQUID_BOT_LOG_FILE="bot/discord.log",
        SQUID_API_LOG_FILE="api.log",
        SQUID_API_ACCESS_LOG_FILE="access.log",
        SQUID_BUILD_COMMIT_HASH="abcdef123456",
        SQUID_BUILD_COMMIT_MESSAGE="configuration rewrite",
        SQUID_NOTIFICATION_PUBLIC_SITE_URL="https://catalogue.example/",
        SQUID_NOTIFICATION_RETENTION_DAYS="120",
        SQUID_NOTIFICATION_STAFF_DISCORD_IDS="[123,456]",
        SQUID_OBSERVABILITY_ENABLED="true",
        SQUID_OBSERVABILITY_ENDPOINT="http://collector.example:4318",
        SQUID_OBSERVABILITY_HEADERS='{"Authorization":"secret-token"}',
        SQUID_OBSERVABILITY_SAMPLE_RATIO="0.25",
        SQUID_OBSERVABILITY_SERVICE_NAME="custom-squid",
        SQUID_OBSERVABILITY_ENVIRONMENT="staging",
        SQUID_OBSERVABILITY_RELEASE="abcdef123456",
    )

    config = load_application_config()

    assert isinstance(config, ApplicationConfig)
    assert config.database.url.get_secret_value() == BASE_ENVIRONMENT["SQUID_DATABASE_URL"]
    assert config.database.listener_url is not None
    assert config.database.listener_url.get_secret_value() == "postgresql://listener:password@database.example/squid"
    assert config.runtime.openai.api_key is not None
    assert config.runtime.openai.api_key.get_secret_value() == "text-key"
    assert config.runtime.embeddings.api_key is not None
    assert config.runtime.embeddings.api_key.get_secret_value() == "embedding-key"
    assert str(config.runtime.embeddings.base_url) == "https://embedding.example/v1"
    assert config.runtime.embeddings.model == "embedding-model"
    assert config.runtime.cursor_secret.get_secret_value() == "cursor-secret-for-tests"
    assert config.bot_process().logging.root_level == "INFO"
    assert config.bot_process().logging.log_file == "bot/discord.log"
    assert config.api_process().api.port == 9000
    assert config.api_process().api.key_pepper.get_secret_value() == "api-key-pepper-for-tests"
    assert config.api_process().logging.access_log_file == "access.log"
    assert config.build.commit_hash == "abcdef123456"
    for process_config in (config.bot_process(), config.api_process(), config.worker_process()):
        assert str(process_config.notification.public_site_url) == "https://catalogue.example/"
        assert process_config.notification.retention_days == 120
        assert process_config.notification.staff_discord_ids == (123, 456)
        assert process_config.observability.enabled is True
        assert str(process_config.observability.endpoint) == "http://collector.example:4318/"
        assert process_config.observability.headers["Authorization"].get_secret_value() == "secret-token"
        assert process_config.observability.sample_ratio == 0.25
        assert process_config.observability.service_name == "custom-squid"
        assert process_config.observability.environment == "staging"
        assert process_config.observability.release == "abcdef123456"
        assert "secret-token" not in repr(process_config.observability)
    assert EMBEDDING_DIMENSION == 1536


def test_observability_is_disabled_and_inert_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(monkeypatch, SQUID_DISCORD_TOKEN="discord-token")

    config = load_bot_process_config().observability

    assert config.enabled is False
    assert config.endpoint is None
    assert config.headers == {}
    assert config.sample_ratio == 1.0


def test_worker_loads_only_inherited_observability_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SQUID_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("SQUID_OBSERVABILITY_ENDPOINT", "http://collector:4318/v1/traces")

    config = load_worker_observability_config()

    assert config.enabled is True
    assert str(config.endpoint) == "http://collector:4318/v1/traces"


@pytest.mark.parametrize("ratio", ["-0.1", "1.1"])
def test_observability_sample_ratio_is_bounded(monkeypatch: pytest.MonkeyPatch, ratio: str) -> None:
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_OBSERVABILITY_SAMPLE_RATIO=ratio,
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_bot_process_config()

    assert any(issue["field"] == "observability.sample_ratio" for issue in _issues(exc_info.value))


def test_enabled_observability_requires_an_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_OBSERVABILITY_ENABLED="true",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_bot_process_config()

    assert any(issue["field"] == "observability" for issue in _issues(exc_info.value))


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


def test_community_ids_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_COMMUNITY_REDSTONER_STARBOARD_AUTHOR_ID="1",
        SQUID_COMMUNITY_REDSTONER_STARBOARD_CHANNEL_ID="2",
        SQUID_COMMUNITY_WELCOME_CHANNEL_ID="3",
        SQUID_COMMUNITY_WELCOME_RELAY_CHANNEL_ID="4",
        SQUID_COMMUNITY_VERSION_TRACKER_CHANNEL_ID="5",
        SQUID_COMMUNITY_BUILD_LOG_CHANNEL_IDS="[6, 7]",
    )

    config = load_bot_process_config().runtime.community

    assert config.redstoner_starboard_author_id == 1
    assert config.redstoner_starboard_channel_id == 2
    assert config.welcome_channel_id == 3
    assert config.welcome_relay_channel_id == 4
    assert config.version_tracker_channel_id == 5
    assert config.build_log_channel_ids == (6, 7)


def test_schematic_duplicate_thresholds_load_from_flat_environment_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_SCHEMATIC_DUPLICATE_METRIC_TOLERANCE="0.15",
        SQUID_SCHEMATIC_DUPLICATE_NEAR_DISTANCE="0.75",
        SQUID_SCHEMATIC_DUPLICATE_MAX_COMPARISONS="4",
        SQUID_SCHEMATIC_DUPLICATE_RESULT_LIMIT="2",
        SQUID_SCHEMATIC_DUPLICATE_TOTAL_TIMEOUT_SECONDS="12",
    )

    config = load_bot_process_config().runtime.schematics

    assert config.duplicate_metric_tolerance == 0.15
    assert config.duplicate_near_distance == 0.75
    assert config.duplicate_max_comparisons == 4
    assert config.duplicate_result_limit == 2
    assert config.duplicate_total_timeout_seconds == 12


def test_schematic_render_settings_load_from_flat_environment_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pack_path = tmp_path / "vanilla.zip"
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_SCHEMATIC_RENDER_ENABLED="true",
        SQUID_SCHEMATIC_RENDER_PACK_PATH=str(pack_path),
        SQUID_SCHEMATIC_RENDER_PUBLIC_BASE_URL="https://api.example",
        SQUID_SCHEMATIC_RENDER_WIDTH="640",
        SQUID_SCHEMATIC_RENDER_HEIGHT="480",
        SQUID_SCHEMATIC_RENDER_MAX_BLOCK_COUNT="12345",
    )

    config = load_bot_process_config().runtime.schematics

    assert config.render_enabled is True
    assert config.render_pack_path == pack_path
    assert (config.render_width, config.render_height) == (640, 480)
    assert config.render_max_block_count == 12345


def test_enabled_schematic_rendering_requires_a_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(monkeypatch, SQUID_DISCORD_TOKEN="discord-token", SQUID_SCHEMATIC_RENDER_ENABLED="true")

    with pytest.raises(ConfigurationError) as exc_info:
        load_bot_process_config()

    assert any(issue["field"] == "schematic" for issue in _issues(exc_info.value))


def test_remote_schematic_render_pack_requires_a_sha256(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_SCHEMATIC_RENDER_PACK_URL="https://packs.example/vanilla.zip",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_bot_process_config()

    assert any(issue["field"] == "schematic" for issue in _issues(exc_info.value))


def test_process_loaders_require_only_their_own_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(monkeypatch, SQUID_DISCORD_TOKEN="discord-token")
    assert load_bot_process_config().discord.token.get_secret_value() == "discord-token"

    monkeypatch.delenv("SQUID_DISCORD_TOKEN")
    monkeypatch.setenv("SQUID_API_SECRET", "api-secret")
    assert load_api_process_config().api.secret.get_secret_value() == "api-secret"


def test_unknown_environment_keys_warn_with_name_only_and_typo_suggestion(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_value = "must-not-appear-in-diagnostics"
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_SCHEMATIC_WORKRES=secret_value,
    )

    with caplog.at_level(logging.WARNING, logger="squid.config"):
        load_bot_process_config()

    record = next(record for record in caplog.records if record.getMessage().startswith("Unknown SQUID"))
    assert record.__dict__["squid.config.unknown_keys"] == ("SQUID_SCHEMATIC_WORKRES",)
    assert record.__dict__["squid.config.suggestions"] == {"SQUID_SCHEMATIC_WORKRES": "SQUID_SCHEMATIC_WORKERS"}
    assert secret_value not in str(record.__dict__)


def test_strict_unknown_environment_keys_fail_without_exposing_values(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_value = "must-not-appear-in-diagnostics"
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_STRICT_UNKNOWN_KEYS="true",
        SQUID_SCHEMATIC_WORKRES=secret_value,
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_bot_process_config()

    assert _issues(exc_info.value) == [
        {
            "field": "SQUID_SCHEMATIC_WORKRES",
            "message": "Unknown configuration key; did you mean SQUID_SCHEMATIC_WORKERS?",
            "type": "unknown_key",
        }
    ]
    assert secret_value not in str(exc_info.value.context)


def test_sibling_process_keys_in_shared_dotenv_are_not_reported_unknown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _set_environment(monkeypatch, SQUID_DISCORD_TOKEN="discord-token")
    (tmp_path / ".env").write_text("SQUID_API_PORT=9000\nSQUID_WORKER_HEALTH_PORT=9001\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="squid.config"):
        load_bot_process_config()

    assert not any(record.getMessage().startswith("Unknown SQUID") for record in caplog.records)


def test_configuration_reports_all_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*BASE_ENVIRONMENT, "SQUID_DISCORD_TOKEN", "SQUID_API_SECRET"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigurationError) as exc_info:
        load_application_config()

    issues = _issues(exc_info.value)
    fields = {issue["field"] for issue in issues}
    assert fields == {"database", "verification", "cursor", "discord", "api"}


def test_cursor_secret_requires_enough_entropy_material(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(monkeypatch, SQUID_DISCORD_TOKEN="discord-token", SQUID_CURSOR_SECRET="too-short")

    with pytest.raises(ConfigurationError) as exc_info:
        load_bot_process_config()

    assert any(issue["field"] == "cursor.secret" for issue in _issues(exc_info.value))


def test_api_key_pepper_requires_enough_entropy_material(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(monkeypatch, SQUID_API_SECRET="api-secret-long-enough", SQUID_API_KEY_PEPPER="too-short")

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    assert any(issue["field"] == "api.key_pepper" for issue in _issues(exc_info.value))


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


def test_api_rate_limit_and_trusted_proxy_settings_load(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(
        monkeypatch,
        SQUID_API_SECRET="api-secret",
        SQUID_API_TRUSTED_PROXY_IPS='["127.0.0.1", "10.0.0.0/8"]',
        SQUID_RATE_LIMIT_REDIS_URL="rediss://user:secret@redis.example/0",
        SQUID_RATE_LIMIT_WINDOW_SECONDS="60",
        SQUID_RATE_LIMIT_IP_REQUESTS="120",
        SQUID_RATE_LIMIT_PRINCIPAL_REQUESTS="90",
        SQUID_RATE_LIMIT_WRITE_REQUESTS="30",
        SQUID_RATE_LIMIT_VOTE_REQUESTS="10",
        SQUID_RATE_LIMIT_REDIS_TIMEOUT_SECONDS="0.5",
        SQUID_RATE_LIMIT_REDIS_RETRY_SECONDS="7",
        SQUID_RATE_LIMIT_LOCAL_MAX_KEYS="4096",
    )

    config = load_api_process_config()

    assert config.api.trusted_proxy_ips == ("127.0.0.1", "10.0.0.0/8")
    assert config.rate_limit.redis_url is not None
    assert config.rate_limit.redis_url.get_secret_value() == "rediss://user:secret@redis.example/0"
    assert config.rate_limit.window_seconds == 60
    assert config.rate_limit.ip_requests == 120
    assert config.rate_limit.principal_requests == 90
    assert config.rate_limit.write_requests == 30
    assert config.rate_limit.vote_requests == 10
    assert config.rate_limit.redis_timeout_seconds == 0.5
    assert config.rate_limit.redis_retry_seconds == 7
    assert config.rate_limit.local_max_keys == 4_096
    assert "secret@redis" not in repr(config)


@pytest.mark.parametrize(
    ("setting", "field"),
    [
        ({"SQUID_API_TRUSTED_PROXY_IPS": '["not-an-address"]'}, "api.trusted_proxy_ips"),
        ({"SQUID_RATE_LIMIT_REDIS_URL": "https://redis.example"}, "rate_limit.redis_url"),
        ({"SQUID_RATE_LIMIT_IP_REQUESTS": "0"}, "rate_limit.ip_requests"),
    ],
)
def test_invalid_api_rate_limit_settings_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    setting: dict[str, str],
    field: str,
) -> None:
    _set_environment(monkeypatch, SQUID_API_SECRET="api-secret", **setting)

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    assert any(issue["field"] == field for issue in _issues(exc_info.value))


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
