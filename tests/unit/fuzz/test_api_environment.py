"""Disposable API environment safety and reset contracts."""

from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import replace

import pytest

from tests.fuzz.api.environment import (
    APPLICATION_PREFIX,
    CONTROL_NONCE_ENV,
    DATABASE_PREFIX,
    FAKE_HOST_ENV,
    FAKE_PORT_ENV,
    RUN_ID_ENV,
    SENTINEL_ENV,
    ApiEnvironment,
    ResetHooks,
    ResourceAttestation,
    RunIdentity,
    RunningApi,
    SeededIds,
    SyntheticEndpoints,
    SyntheticSecrets,
    UnsafeEnvironmentError,
    synthetic_api_environment,
    validate_target_url,
)


def identity() -> RunIdentity:
    return RunIdentity(
        run_id="0123456789abcdef0123456789abcdef",
        sentinel="synthetic-sentinel-with-sufficient-entropy",
        database_name=f"{DATABASE_PREFIX}0123456789abcdef0123456789abcdef",
        application_name=f"{APPLICATION_PREFIX}0123456789abcdef0123456789abcdef",
        network_name="redstone-squid-api-fuzz-0123456789abcdef0123456789abcdef",
    )


def attestation(run: RunIdentity) -> ResourceAttestation:
    return ResourceAttestation(
        labels=run.labels,
        network_id="network-0123",
        network_internal=True,
        database_name=run.database_name,
        sentinel=run.sentinel,
        application_name=run.application_name,
    )


def seeded_ids() -> SeededIds:
    return SeededIds(1, 2, 3, 4, 1, "alice", "bob", "alice-session", "bob-session", "pending", "admin", "api")


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.2:8000",
        "http://[::1]:8000",
        "http://192.0.2.1:8000",
        "http://127.0.0.1",
        "http://user:password@127.0.0.1:8000",
        "http://127.0.0.1:8000/api",
        "http://127.0.0.1:8000?target=other",
    ],
)
def test_target_validation_refuses_non_literal_or_ambiguous_origins(url: str) -> None:
    with pytest.raises(UnsafeEnvironmentError, match=r"127\.0\.0\.1"):
        validate_target_url(url)


def test_target_validation_normalizes_ipv4_loopback() -> None:
    assert validate_target_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"


def test_run_identity_derives_every_disposable_database_role_and_namespace() -> None:
    run = identity()

    assert run.template_database_name == f"squid_fuzz_template_{run.run_id}"
    assert run.migrator_role == f"squid_fuzz_migrator_{run.run_id}"
    assert run.application_role == f"squid_fuzz_app_{run.run_id}"
    assert run.observer_role == f"squid_fuzz_observer_{run.run_id}"
    assert run.redis_namespace == f"squid:fuzz:{run.run_id}"
    assert run.labels == {"dev.redstone-squid.api-fuzz.run": run.run_id}


def test_synthetic_secrets_are_deterministic_domain_separated_and_redacted() -> None:
    run = identity()

    first = SyntheticSecrets.for_identity(run)
    second = SyntheticSecrets.for_identity(run)

    assert first == second
    assert (
        len(
            {
                first.api_secret,
                first.api_key_pepper,
                first.session_pepper,
                first.alice_web_session,
                first.bob_web_session,
            }
        )
        == 5
    )
    assert run.sentinel not in repr(first)
    assert first.service_api_token not in repr(first)


@pytest.mark.parametrize(
    ("mutate", "failure"),
    [
        (lambda observed: replace(observed, labels={}), "label:"),
        (lambda observed: replace(observed, network_id="other-network"), "network_id"),
        (lambda observed: replace(observed, network_internal=False), "network_internal"),
        (lambda observed: replace(observed, database_name="production"), "database_name"),
        (lambda observed: replace(observed, sentinel="wrong-sentinel"), "sentinel"),
        (lambda observed: replace(observed, application_name="psql"), "application_name"),
    ],
)
def test_resource_attestation_requires_every_ownership_fact(
    mutate: Callable[[ResourceAttestation], ResourceAttestation], failure: str
) -> None:
    run = identity()
    observed = mutate(attestation(run))

    with pytest.raises(UnsafeEnvironmentError, match=failure):
        observed.verify(run, expected_network_id="network-0123")


async def test_reset_attests_then_runs_every_hook_and_checks_the_baseline() -> None:
    run = identity()
    events: list[str] = []

    async def action(name: str) -> None:
        events.append(name)

    async def seed() -> SeededIds:
        events.append("seed")
        return seeded_ids()

    async def read_attestation() -> ResourceAttestation:
        events.append("attest")
        return attestation(run)

    async def checksum() -> str:
        events.append("checksum")
        return "baseline-v1"

    running = RunningApi(
        identity=run,
        base_url="http://127.0.0.1:8000",
        network_id="network-0123",
        read_attestation=read_attestation,
        reset_hooks=ResetHooks(
            quiesce=lambda: action("quiesce"),
            reset_database=lambda: action("database"),
            clear_redis=lambda: action("redis"),
            seed=seed,
            resume=lambda: action("resume"),
            reset_fakes=lambda: action("fakes"),
            checksum=checksum,
            seeded_ids=seeded_ids(),
            baseline_checksum="baseline-v1",
        ),
    )

    await running.reset()

    assert events == ["attest", "quiesce", "database", "redis", "seed", "fakes", "checksum", "resume"]


async def test_reset_stops_before_mutation_when_attestation_fails() -> None:
    run = identity()
    mutations: list[str] = []

    async def unsafe_attestation() -> ResourceAttestation:
        return replace(attestation(run), database_name="squid")

    async def mutate() -> None:
        mutations.append("called")

    async def seed() -> SeededIds:
        mutations.append("called")
        return seeded_ids()

    async def checksum() -> str:
        return "baseline-v1"

    running = RunningApi(
        identity=run,
        base_url="http://127.0.0.1:8000",
        network_id="network-0123",
        read_attestation=unsafe_attestation,
        reset_hooks=ResetHooks(mutate, mutate, mutate, seed, mutate, mutate, checksum, seeded_ids(), "baseline-v1"),
    )

    with pytest.raises(UnsafeEnvironmentError, match="database_name"):
        await running.reset()
    assert mutations == []


async def test_reset_refuses_a_seed_that_changes_stable_identifiers() -> None:
    run = identity()

    async def noop() -> None:
        pass

    async def reseed() -> SeededIds:
        return replace(seeded_ids(), alice_account_id=999)

    async def read_attestation() -> ResourceAttestation:
        return attestation(run)

    async def checksum() -> str:
        return "baseline-v1"

    running = RunningApi(
        identity=run,
        base_url="http://127.0.0.1:8000",
        network_id="network-0123",
        read_attestation=read_attestation,
        reset_hooks=ResetHooks(
            noop,
            noop,
            noop,
            reseed,
            noop,
            noop,
            checksum,
            seeded_ids(),
            "baseline-v1",
        ),
    )

    with pytest.raises(UnsafeEnvironmentError, match="different stable identifiers"):
        await running.reset()


async def test_environment_closes_registered_resources_when_startup_attestation_fails() -> None:
    run = identity()
    events: list[str] = []

    async def cleanup() -> None:
        events.append("cleanup")

    async def starter(_identity: RunIdentity, stack: AsyncExitStack) -> RunningApi:
        stack.push_async_callback(cleanup)

        async def read_attestation() -> ResourceAttestation:
            return replace(attestation(run), sentinel="wrong")

        async def noop() -> None:
            pass

        async def seed() -> SeededIds:
            return seeded_ids()

        async def checksum() -> str:
            return "baseline"

        return RunningApi(
            identity=run,
            base_url="http://127.0.0.1:8000",
            network_id="network-0123",
            read_attestation=read_attestation,
            reset_hooks=ResetHooks(noop, noop, noop, seed, noop, noop, checksum, seeded_ids(), "baseline"),
        )

    with pytest.raises(UnsafeEnvironmentError, match="sentinel"):
        async with ApiEnvironment(starter, identity=run):
            pytest.fail("Unsafe environment entered")
    assert events == ["cleanup"]


def test_synthetic_environment_is_allowlisted_and_uses_only_supplied_endpoints() -> None:
    run = identity()
    environment = synthetic_api_environment(
        run,
        SyntheticEndpoints(
            api_container_port=8123,
            postgres_url="postgresql://synthetic:synthetic@postgres:5432/squid_fuzz",
            redis_url="redis://redis:6379/0",
            mojang_profile_url="http://127.0.0.1:8101/profile",
            discord_api_url="http://127.0.0.1:8102/api",
            discord_authorize_url="http://127.0.0.1:8102/authorize",
        ),
    )

    assert "PATH" not in environment
    assert "HOME" not in environment
    assert environment["SQUID_API_PORT"] == "8123"
    assert environment["SQUID_API_SECRET"] == SyntheticSecrets.for_identity(run).api_secret
    assert run.sentinel not in environment["SQUID_API_SECRET"]
    assert CONTROL_NONCE_ENV not in environment
    assert FAKE_HOST_ENV not in environment
    assert FAKE_PORT_ENV not in environment
    assert RUN_ID_ENV not in environment
    assert SENTINEL_ENV not in environment
    assert set(environment) == {
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUTF8",
        "PYTHONUNBUFFERED",
        "TMPDIR",
        "SQUID_STRICT_UNKNOWN_KEYS",
        "SQUID_DATABASE_URL",
        "SQUID_VERIFICATION_CODE_PEPPER",
        "SQUID_API_SECRET",
        "SQUID_API_KEY_PEPPER",
        "SQUID_API_SESSION_PEPPER",
        "SQUID_API_IDEMPOTENCY_ACTIVE_KEY_ID",
        "SQUID_API_IDEMPOTENCY_KEYS",
        "SQUID_API_PORT",
        "SQUID_RATE_LIMIT_REDIS_URL",
        "SQUID_STORAGE_LOCAL_DIRECTORY",
        "SQUID_UPSTREAM_HTTP_MOJANG_PROFILE_URL",
        "SQUID_UPSTREAM_HTTP_DISCORD_API_URL",
        "SQUID_UPSTREAM_HTTP_DISCORD_AUTHORIZE_URL",
        "SQUID_SCHEMATIC_ENABLED",
        "SQUID_MEDIA_ENABLED",
        "SQUID_OBSERVABILITY_ENABLED",
    }
