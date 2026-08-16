"""Typed startup configuration tests."""

import logging
from pathlib import Path
from typing import cast

import pytest

from squid.config import (
    EMBEDDING_DIMENSION,
    ApplicationConfig,
    ObjectStorageConfig,
    UpstreamHttpConfig,
    load_api_process_config,
    load_application_config,
    load_bot_process_config,
    load_or_exit,
    load_worker_observability_config,
    load_worker_process_config,
)
from squid.core.errors import ConfigurationError
from squid.media.application.jobs import MEDIA_ARTIFACT_PUBLICATION_LEASE
from squid.permissions.domain import Pattern

BASE_ENVIRONMENT = {
    "SQUID_DATABASE_URL": "postgresql://user:password@database.example/squid",
    "SQUID_VERIFICATION_CODE_PEPPER": "verification-pepper",
    "SQUID_API_KEY_PEPPER": "api-key-pepper-for-tests",
    "SQUID_API_SESSION_PEPPER": "session-pepper-for-tests",
    "SQUID_API_IDEMPOTENCY_ACTIVE_KEY_ID": "test-v1",
    "SQUID_API_IDEMPOTENCY_KEYS": '{"test-v1":"MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="}',
}


def _issues(error: ConfigurationError) -> list[dict[str, str]]:
    return cast(list[dict[str, str]], error.context["issues"])


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)


def _set_environment(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name, value in {**BASE_ENVIRONMENT, **values}.items():
        monkeypatch.setenv(name, value)


def test_media_publication_lease_exceeds_the_configured_s3_retry_envelope() -> None:
    storage = ObjectStorageConfig(
        connect_timeout_seconds=60,
        read_timeout_seconds=3600,
        max_attempts=10,
    )
    conservative_attempts = storage.max_attempts + 1
    maximum_published_objects = 3
    configured_io_seconds = (
        maximum_published_objects
        * conservative_attempts
        * (storage.connect_timeout_seconds + storage.read_timeout_seconds)
    )
    retry_backoff_margin_seconds = 60 * 60

    assert MEDIA_ARTIFACT_PUBLICATION_LEASE.total_seconds() > configured_io_seconds + retry_backoff_margin_seconds
    with pytest.raises(ValueError, match="less than or equal to 60"):
        ObjectStorageConfig(connect_timeout_seconds=61)
    with pytest.raises(ValueError, match="less than or equal to 3600"):
        ObjectStorageConfig(read_timeout_seconds=3601)


def test_upstream_http_overrides_are_explicitly_loopback_only() -> None:
    configured = UpstreamHttpConfig.model_validate(
        {
            "mojang_profile_url": "http://127.0.0.1:8101/mojang/profile",
            "discord_api_url": "http://localhost:8102/discord/api",
            "discord_authorize_url": "http://[::1]:8103/discord/authorize",
        }
    )

    assert configured.mojang_profile_url.host == "127.0.0.1"
    assert configured.discord_api_url.host == "localhost"
    assert configured.discord_authorize_url.host == "[::1]"


@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("mojang_profile_url", "https://attacker.example/mojang"),
        ("discord_api_url", "https://discord.example/api"),
        ("discord_authorize_url", "http://fake-upstream.internal/authorize"),
    ],
)
def test_upstream_http_rejects_non_official_remote_overrides(field: str, url: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        UpstreamHttpConfig.model_validate({field: url})


def test_upstream_http_rejects_loopback_queries_and_credentials() -> None:
    with pytest.raises(ValueError, match="query or fragment"):
        UpstreamHttpConfig.model_validate({"discord_api_url": "http://127.0.0.1:8102/api?credential=leak"})
    with pytest.raises(ValueError, match="loopback"):
        UpstreamHttpConfig.model_validate({"mojang_profile_url": "http://user:password@127.0.0.1:8101/profile"})


def test_api_process_projects_loopback_upstreams_into_the_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(
        monkeypatch,
        SQUID_API_SECRET="api-secret",
        SQUID_UPSTREAM_HTTP_MOJANG_PROFILE_URL="http://127.0.0.1:8101/mojang/profile",
        SQUID_UPSTREAM_HTTP_DISCORD_API_URL="http://127.0.0.1:8102/discord/api",
        SQUID_UPSTREAM_HTTP_DISCORD_AUTHORIZE_URL="http://127.0.0.1:8102/discord/authorize",
    )

    config = load_api_process_config()

    assert str(config.runtime.upstream_http.mojang_profile_url).startswith("http://127.0.0.1:8101/")
    assert str(config.runtime.upstream_http.discord_api_url).startswith("http://127.0.0.1:8102/")


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
    assert config.bot_process().logging.root_level == "INFO"
    assert config.bot_process().logging.log_file == "bot/discord.log"
    assert config.api_process().api.port == 9000
    active_key_id = BASE_ENVIRONMENT["SQUID_API_IDEMPOTENCY_ACTIVE_KEY_ID"]
    assert config.api_process().api.key_pepper.get_secret_value() == BASE_ENVIRONMENT["SQUID_API_KEY_PEPPER"]
    assert config.api_process().api.idempotency_encryption.decoded_keys() == {active_key_id: b"0" * 32}
    runtime_encryption = config.api_process().runtime.idempotency_encryption
    assert runtime_encryption is not None
    assert runtime_encryption.active_key_id == active_key_id
    assert "MDAwMDAw" not in repr(config.api_process().api)
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


def test_observability_exports_nowhere_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disabled, with no endpoint and no credentials: nothing leaves the process unasked."""
    _set_environment(monkeypatch, SQUID_DISCORD_TOKEN="discord-token")

    config = load_bot_process_config().observability

    assert config.enabled is False
    assert config.endpoint is None
    assert config.headers == {}


def test_observability_samples_everything_until_it_is_tuned(monkeypatch: pytest.MonkeyPatch) -> None:
    """A separate claim from the one above: the ratio only takes effect once exporting is on."""
    _set_environment(monkeypatch, SQUID_DISCORD_TOKEN="discord-token")

    assert load_bot_process_config().observability.sample_ratio == 1.0


def test_worker_loads_only_inherited_observability_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SQUID_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("SQUID_OBSERVABILITY_ENDPOINT", "http://collector:4318/v1/traces")

    config = load_worker_observability_config()

    assert config.enabled is True
    assert str(config.endpoint) == "http://collector:4318/v1/traces"


def test_media_worker_concurrency_is_explicit_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(monkeypatch, SQUID_WORKER_MEDIA_JOB_CONCURRENCY="2")

    assert load_worker_process_config().worker.media_job_concurrency == 2

    monkeypatch.setenv("SQUID_WORKER_MEDIA_JOB_CONCURRENCY", "9")
    with pytest.raises(ConfigurationError) as exc_info:
        load_worker_process_config()

    assert any(issue["field"] == "worker.media_job_concurrency" for issue in _issues(exc_info.value))


def test_media_and_minecraft_auth_runtime_settings_are_process_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    pepper = "minecraft-auth-pepper-with-32-bytes"
    _set_environment(
        monkeypatch,
        SQUID_API_SECRET="api-secret",
        SQUID_MEDIA_ENABLED="true",
        SQUID_MEDIA_THREADS="3",
        SQUID_MINECRAFT_AUTH_PEPPER=pepper,
        SQUID_MINECRAFT_AUTH_VERIFICATION_URI="https://catalogue.example/minecraft/link",
    )

    config = load_api_process_config().runtime

    assert config.media.enabled is True
    assert config.media.threads == 3
    assert config.minecraft_auth.pepper is not None
    assert config.minecraft_auth.pepper.get_secret_value() == pepper
    assert str(config.minecraft_auth.verification_uri) == "https://catalogue.example/minecraft/link"


def test_minecraft_auth_pepper_requires_32_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(
        monkeypatch,
        SQUID_API_SECRET="api-secret",
        SQUID_MINECRAFT_AUTH_PEPPER="too-short",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    assert any(issue["field"] == "minecraft_auth.pepper" for issue in _issues(exc_info.value))


@pytest.mark.parametrize(
    "settings",
    [
        {"SQUID_MINECRAFT_AUTH_PEPPER": "minecraft-auth-pepper-with-32-bytes"},
        {"SQUID_MINECRAFT_AUTH_VERIFICATION_URI": "https://catalogue.example/minecraft/link"},
        {
            "SQUID_MINECRAFT_AUTH_PEPPER": "minecraft-auth-pepper-with-32-bytes",
            "SQUID_MINECRAFT_AUTH_VERIFICATION_URI": "http://catalogue.example/minecraft/link",
        },
    ],
)
def test_minecraft_auth_requires_a_complete_https_device_flow(
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, str],
) -> None:
    _set_environment(monkeypatch, SQUID_API_SECRET="api-secret", **settings)

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    assert any(issue["field"] == "minecraft_auth" for issue in _issues(exc_info.value))


def test_cli_auth_runtime_settings_are_process_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    pepper = "cli-auth-pepper-with-at-least-32-bytes"
    _set_environment(
        monkeypatch,
        SQUID_API_SECRET="api-secret",
        SQUID_CLI_AUTH_PEPPER=pepper,
        SQUID_CLI_AUTH_VERIFICATION_URI="https://catalogue.example/cli/link",
    )

    config = load_api_process_config().runtime

    assert config.cli_auth.pepper is not None
    assert config.cli_auth.pepper.get_secret_value() == pepper
    assert str(config.cli_auth.verification_uri) == "https://catalogue.example/cli/link"


@pytest.mark.parametrize(
    "settings",
    [
        {"SQUID_CLI_AUTH_PEPPER": "cli-auth-pepper-with-at-least-32-bytes"},
        {"SQUID_CLI_AUTH_VERIFICATION_URI": "https://catalogue.example/cli/link"},
        {
            "SQUID_CLI_AUTH_PEPPER": "cli-auth-pepper-with-at-least-32-bytes",
            "SQUID_CLI_AUTH_VERIFICATION_URI": "http://catalogue.example/cli/link",
        },
        {
            "SQUID_CLI_AUTH_PEPPER": "too-short",
            "SQUID_CLI_AUTH_VERIFICATION_URI": "https://catalogue.example/cli/link",
        },
    ],
)
def test_cli_auth_requires_a_complete_strong_https_device_flow(
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, str],
) -> None:
    _set_environment(monkeypatch, SQUID_API_SECRET="api-secret", **settings)

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    assert any(issue["field"].startswith("cli_auth") for issue in _issues(exc_info.value))


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
    """Aggregation is the contract here, not the current list of required groups.

    A loader that raised on the first missing setting would send an operator round the
    boot loop once per variable. The exact field set this used to compare against churned
    with every new required setting while proving nothing beyond "several were named", so
    the required groups are asserted as a lower bound and each issue must be actionable.
    """
    for name in (*BASE_ENVIRONMENT, "SQUID_DISCORD_TOKEN", "SQUID_API_SECRET"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigurationError) as exc_info:
        load_application_config()

    issues = _issues(exc_info.value)
    fields = {issue["field"] for issue in issues}
    assert len(issues) > 1
    assert fields >= {"database", "discord", "api"}
    assert all(issue["message"] for issue in issues)


def test_a_retired_key_left_behind_by_a_deployment_does_not_block_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """SQUID_CURSOR_SECRET outlived the signed cursors it fed.

    Strict mode turns an unknown key into a boot failure, so without the retired-key tombstone a
    stale line in an env file would take down deployments during a release that changed nothing
    for them.
    """
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_STRICT_UNKNOWN_KEYS="true",
        SQUID_CURSOR_SECRET="left-over-from-an-older-release",
    )

    assert load_bot_process_config().database is not None


def test_api_key_pepper_requires_enough_entropy_material(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(monkeypatch, SQUID_API_SECRET="api-secret-long-enough", SQUID_API_KEY_PEPPER="too-short")

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    assert any(issue["field"] == "api.key_pepper" for issue in _issues(exc_info.value))


def test_a_malformed_bootstrap_secret_node_fails_configuration_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """The column downstream is free text, so a typo here would otherwise become
    a credential that authenticates and then silently authorizes nothing."""
    _set_environment(
        monkeypatch,
        SQUID_API_SECRET="api-secret-long-enough",
        SQUID_API_SECRET_NODES='["build.re*.read"]',
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    assert any(issue["field"] == "api.secret_nodes" for issue in _issues(exc_info.value))


def test_bootstrap_secret_nodes_are_parsed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(
        monkeypatch,
        SQUID_API_SECRET="api-secret-long-enough",
        SQUID_API_SECRET_NODES='["account.verify.relay"]',
    )

    config = load_api_process_config()

    assert config.api.secret_patterns == {Pattern.parse("account.verify.relay")}


@pytest.mark.parametrize(
    ("active_key_id", "keys", "field"),
    [
        ("missing", '{"current":"MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="}', "api"),
        ("current", '{"current":"dG9vLXNob3J0"}', "api.idempotency_keys"),
        ("bad key id", '{"bad key id":"MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="}', "api.idempotency_keys"),
    ],
)
def test_idempotency_encryption_keyring_is_complete_and_strong(
    monkeypatch: pytest.MonkeyPatch,
    active_key_id: str,
    keys: str,
    field: str,
) -> None:
    """The exact field matters because the rendered message is derived from it.

    A keyring rejected by the sibling IdempotencyEncryptionConfig used to be reported against
    that model's own `keys` field, which names no variable an operator can set. Only the
    membership check spans two settings and so belongs to `api` as a whole.
    """
    _set_environment(
        monkeypatch,
        SQUID_API_SECRET="api-secret-long-enough",
        SQUID_API_IDEMPOTENCY_ACTIVE_KEY_ID=active_key_id,
        SQUID_API_IDEMPOTENCY_KEYS=keys,
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    assert any(issue["field"] == field for issue in _issues(exc_info.value))


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

    rendered_error = f"{exc_info.value} {exc_info.value.backend_detail()} {exc_info.value.context}"
    assert "super-secret-password" not in rendered_error
    assert "not-a-port" not in rendered_error
    assert "very-loud" not in rendered_error


def test_configuration_failures_name_the_variables_to_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rendered text is the whole diagnostic an operator gets.

    Nothing renders `context["issues"]`: logging is configured from the settings being loaded,
    so a boot failure is printed before any handler exists. A count with no names sends the
    operator to grep the settings model.
    """
    _set_environment(
        monkeypatch,
        SQUID_DISCORD_TOKEN="discord-token",
        SQUID_API_PORT="65536",
        SQUID_LOG_LEVEL="very-loud",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_application_config()

    rendered = exc_info.value.backend_detail()
    assert "  - SQUID_API_PORT: " in rendered
    assert "  - SQUID_LOG_LEVEL: " in rendered
    assert rendered.endswith("Correct the listed SQUID_* settings and restart the process.")


def test_a_missing_settings_group_names_its_required_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """`SQUID_DATABASE_*: Field required` would not say that the variable is SQUID_DATABASE_URL."""
    for name in (*BASE_ENVIRONMENT, "SQUID_DISCORD_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigurationError) as exc_info:
        load_bot_process_config()

    rendered = exc_info.value.backend_detail()
    assert "  - SQUID_DATABASE_URL: " in rendered
    assert "  - SQUID_VERIFICATION_CODE_PEPPER: " in rendered
    assert "  - SQUID_DISCORD_TOKEN: " in rendered


def test_a_rendered_failure_never_invents_an_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A name assembled from a validation location can be one no source ever reads.

    Setting an invented variable looks like a fix and is then silently ignored, so a location
    that maps to no declared key degrades to its group rather than being spelled out.
    """
    _set_environment(
        monkeypatch,
        SQUID_API_SECRET="api-secret-long-enough",
        SQUID_API_IDEMPOTENCY_KEYS='{"test-v1":"not-valid-base64!!"}',
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    rendered = exc_info.value.backend_detail()
    assert "  - SQUID_API_IDEMPOTENCY_KEYS: Every idempotency key must be valid padded base64." in rendered
    assert "SQUID_API_KEYS" not in rendered


def test_a_rendered_failure_omits_configured_dictionary_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A validation location can end in a key the deployment chose, so only two levels are read.

    `context["issues"]` keeps the full internal path — that is the long-standing structured
    contract — but the operator-facing text is assembled from declared field names alone.
    """
    _set_environment(
        monkeypatch,
        SQUID_API_SECRET="api-secret-long-enough",
        SQUID_API_IDEMPOTENCY_KEYS='{"a-key-id-naming-an-internal-system": 5}',
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    assert "a-key-id-naming-an-internal-system" not in exc_info.value.backend_detail()


def test_an_unparsable_setting_names_the_group_it_belongs_to(monkeypatch: pytest.MonkeyPatch) -> None:
    """pydantic-settings fails structured values before validation, with only free text.

    Its message names the field but its cause can quote the input, so the field name is taken
    from a fixed pattern and accepted only when it matches a declared key.
    """
    _set_environment(
        monkeypatch,
        SQUID_API_SECRET="api-secret-long-enough",
        SQUID_API_IDEMPOTENCY_KEYS="}not-json-and-not-a-secret{",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_api_process_config()

    rendered = exc_info.value.backend_detail()
    assert "  - SQUID_API_*: Could not parse the configured value." in rendered
    assert "not-json-and-not-a-secret" not in rendered
    assert _issues(exc_info.value) == [
        {"field": "api", "message": "Could not parse the configured value.", "type": "settings"}
    ]


def test_a_configuration_failure_exits_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Entrypoints load settings before logging exists, so the report goes to stderr."""
    _set_environment(monkeypatch, SQUID_API_PORT="65536")

    with pytest.raises(SystemExit) as exit_info:
        load_or_exit(load_api_process_config)

    assert exit_info.value.code == 1
    assert "  - SQUID_API_PORT: " in capsys.readouterr().err


def test_load_or_exit_returns_a_valid_configuration_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_environment(monkeypatch, SQUID_API_SECRET="api-secret-long-enough", SQUID_API_PORT="9000")

    assert load_or_exit(load_api_process_config).api.port == 9000


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
        SQUID_RATE_LIMIT_MINECRAFT_CHALLENGE_START_REQUESTS="6",
        SQUID_RATE_LIMIT_MINECRAFT_CHALLENGE_EXCHANGE_REQUESTS="110",
        SQUID_RATE_LIMIT_MINECRAFT_CHALLENGE_APPROVAL_REQUESTS="8",
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
    assert config.rate_limit.minecraft_challenge_start_requests == 6
    assert config.rate_limit.minecraft_challenge_exchange_requests == 110
    assert config.rate_limit.minecraft_challenge_approval_requests == 8
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
