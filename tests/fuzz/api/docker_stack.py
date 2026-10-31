"""Concrete, resource-bounded Docker composition for local API fuzzing."""

import asyncio
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docker
import docker.errors
from docker.models.containers import Container
from docker.models.images import Image
from docker.models.networks import Network
from docker.types import LogConfig

from tests.fuzz.api.database import (
    DATABASE_STARTUP_SECONDS,
    POSTGRES_DATABASE,
    POSTGRES_USER,
    DatabaseController,
    DatabaseCredentials,
    DatabaseLocation,
)
from tests.fuzz.api.docker_safety import (
    ContainerExpectation,
    NetworkExpectation,
    guarded_remove_container,
    guarded_remove_network,
    resource_labels,
    validate_local_docker_endpoint,
    verify_container,
    verify_network,
)
from tests.fuzz.api.environment import (
    CONTROL_NONCE_ENV,
    FAKE_BIND_HOST_ENV,
    FAKE_HOST_ENV,
    FAKE_PORT_ENV,
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
)
from tests.fuzz.api.fake_upstreams import CONTROL_HEADER, SnapshotDocument
from tests.fuzz.api.loopback_proxy import LoopbackTcpProxy
from tests.fuzz.api.redis_state import RedisController, RedisCredentials, RedisLocation

POSTGRES_IMAGE = "pgvector/pgvector@sha256:3e8b3adfd27b5707128f60956f62a793c3c9326ea8cfaf0eab7adccb5d700b21"
REDIS_IMAGE = "redis:8.8.0-alpine@sha256:9d317178eceac8454a2284a9e6df2466b93c745529947f0cd42a0fa9609d7005"
API_CONTAINER_PORT = 8000
FAKE_CONTAINER_PORT = 8101
POSTGRES_CONTAINER_PORT = 5432
REDIS_CONTAINER_PORT = 6379
API_READY_SECONDS = 30
CONTAINER_STOP_SECONDS = 10
MAX_CONTROL_BYTES = 128 * 1024
_ROOT = Path(__file__).resolve().parents[3]
_FAKE_HEALTHCHECK = (
    "CMD-SHELL",
    'python -c "import urllib.request; '
    f"urllib.request.urlopen('http://127.0.0.1:{FAKE_CONTAINER_PORT}/__fuzz/ready', timeout=5).read()\"",
)


def docker_api_environment() -> ApiEnvironment:
    """Return an environment backed by the concrete local Docker starter."""
    return ApiEnvironment(start_docker_stack)


@dataclass(slots=True)
class DockerRunningApi(RunningApi):
    """Running API with narrow lifecycle inspection helpers for integration coverage."""

    database_controller: DatabaseController
    redis_controller: RedisController
    api_container: Container
    fake_container: Container
    secrets: SyntheticSecrets
    control_nonce: str = field(repr=False)

    def verification_code_count(self) -> int:
        """Return the narrow database mutation count used by lifecycle coverage."""
        return self.database_controller.verification_code_count()

    def redis_keys(self) -> set[bytes]:
        """Return the bounded dedicated Redis namespace contents."""
        return self.redis_controller.keys()

    def fake_snapshot(self) -> SnapshotDocument:
        """Return redacted fake-upstream observations through the control channel."""
        return fake_snapshot(self.fake_container, self.control_nonce)


async def start_docker_stack(identity: RunIdentity, stack: AsyncExitStack) -> RunningApi:
    """Build and launch one attested PostgreSQL, Redis, fake-upstream, and API stack."""
    return await asyncio.to_thread(_start_docker_stack_sync, identity, stack)


def _start_docker_stack_sync(identity: RunIdentity, stack: AsyncExitStack) -> RunningApi:
    client = docker.from_env()
    validate_local_docker_endpoint(client.api.base_url)
    stack.callback(client.close)

    network = client.networks.create(
        identity.network_name,
        driver="bridge",
        internal=True,
        labels=resource_labels(identity, "network"),
        check_duplicate=False,
    )
    network_id = _required_identifier(network.id, "network ID")
    network_name = _required_identifier(network.name, "network name")
    network_expectation = NetworkExpectation(network_id, identity.network_name)
    stack.callback(guarded_remove_network, network, identity, network_expectation)

    credentials = DatabaseCredentials.generate()
    redis_credentials = RedisCredentials.generate()
    secrets_ = SyntheticSecrets.for_identity(identity)
    postgres = _run_postgres(client, identity, credentials, network)
    postgres_expectation = _expectation(
        postgres,
        identity,
        "postgres",
        network_id,
        POSTGRES_CONTAINER_PORT,
        tmpfs_targets=frozenset({"/var/lib/postgresql/data", "/var/run/postgresql"}),
        tmpfs_options={
            "/var/lib/postgresql/data": "rw,noexec,nosuid,size=512m,uid=999,gid=999,mode=0700",
            "/var/run/postgresql": "rw,noexec,nosuid,size=4m,uid=999,gid=999,mode=0755",
        },
        require_published_port=False,
        memory_bytes=805_306_368,
        nano_cpus=750_000_000,
        pids_limit=128,
        log_max_size="10m",
    )
    stack.callback(guarded_remove_container, postgres, identity, postgres_expectation)
    redis = _run_redis(client, identity, redis_credentials, network)
    redis_expectation = _expectation(
        redis,
        identity,
        "redis",
        network_id,
        REDIS_CONTAINER_PORT,
        tmpfs_targets=frozenset({"/data"}),
        tmpfs_options={"/data": "rw,noexec,nosuid,size=8m,mode=0777"},
        require_published_port=False,
        memory_bytes=134_217_728,
        nano_cpus=250_000_000,
        pids_limit=64,
        log_max_size="5m",
    )
    stack.callback(guarded_remove_container, redis, identity, redis_expectation)
    _verify_live_resources(
        identity,
        network,
        network_expectation,
        ((postgres, postgres_expectation), (redis, redis_expectation)),
        frozenset(
            {
                _required_identifier(postgres.id, "container ID"),
                _required_identifier(redis.id, "container ID"),
            }
        ),
    )
    _wait_for_log(postgres, "database system is ready to accept connections", DATABASE_STARTUP_SECONDS)
    _wait_for_log(redis, "Ready to accept connections", DATABASE_STARTUP_SECONDS)

    postgres_host = _container_ipv4(postgres, network_id)
    redis_host = _container_ipv4(redis, network_id)
    database = DatabaseController(
        identity,
        DatabaseLocation(
            postgres_host,
            POSTGRES_CONTAINER_PORT,
            _required_identifier(postgres.name, "container name"),
        ),
        credentials,
        secrets_,
    )
    redis_state = RedisController(
        identity,
        RedisLocation(redis_host, REDIS_CONTAINER_PORT, _required_identifier(redis.name, "container name")),
        redis_credentials,
    )
    database.wait_until_ready()
    seeded_ids, baseline_checksum = database.bootstrap()
    redis_state.initialize()

    production_image = _build_production_image(client, identity)
    stack.callback(_remove_image, client, _required_identifier(production_image.id, "image ID"))
    fuzz_image = _build_fuzz_image(client, identity, production_image)
    stack.callback(_remove_image, client, _required_identifier(fuzz_image.id, "image ID"))
    control_nonce = secrets.token_urlsafe(32)
    fake = _run_fake(client, identity, fuzz_image, control_nonce, network)
    fake_expectation = _expectation(
        fake,
        identity,
        "fake",
        network_id,
        FAKE_CONTAINER_PORT,
        tmpfs_targets=frozenset({"/tmp"}),
        tmpfs_options={"/tmp": "rw,noexec,nosuid,size=16m"},
        require_published_port=False,
        healthcheck_test=_FAKE_HEALTHCHECK,
        memory_bytes=268_435_456,
        nano_cpus=250_000_000,
        pids_limit=64,
        log_max_size="5m",
    )
    stack.callback(guarded_remove_container, fake, identity, fake_expectation)
    _verify_live_resources(
        identity,
        network,
        network_expectation,
        ((postgres, postgres_expectation), (redis, redis_expectation), (fake, fake_expectation)),
        frozenset(
            {
                _required_identifier(postgres.id, "container ID"),
                _required_identifier(redis.id, "container ID"),
                _required_identifier(fake.id, "container ID"),
            }
        ),
    )
    _wait_for_fake_container(fake)
    fake_host = _container_ipv4(fake, network_id)
    endpoints = SyntheticEndpoints(
        api_container_port=API_CONTAINER_PORT,
        postgres_url=database.application_container_url,
        redis_url=redis_state.container_url,
        mojang_profile_url=f"http://127.0.0.1:{FAKE_CONTAINER_PORT}/mojang/profile",
        discord_api_url=f"http://127.0.0.1:{FAKE_CONTAINER_PORT}/discord/api",
        discord_authorize_url=f"http://127.0.0.1:{FAKE_CONTAINER_PORT}/discord/authorize",
    )
    api = _run_api(
        client,
        identity,
        fuzz_image,
        network,
        {
            **synthetic_api_environment(identity, endpoints, secrets_=secrets_),
            FAKE_HOST_ENV: fake_host,
            FAKE_PORT_ENV: str(FAKE_CONTAINER_PORT),
        },
    )
    api_expectation = _expectation(
        api,
        identity,
        "api",
        network_id,
        API_CONTAINER_PORT,
        tmpfs_targets=frozenset({"/tmp"}),
        tmpfs_options={"/tmp": "rw,noexec,nosuid,size=32m"},
        require_published_port=False,
        require_identity_environment=False,
        forbidden_environment=frozenset(
            {CONTROL_NONCE_ENV, "REDSTONE_SQUID_FUZZ_RUN_ID", "REDSTONE_SQUID_FUZZ_SENTINEL"}
        ),
        memory_bytes=536_870_912,
        nano_cpus=500_000_000,
        pids_limit=128,
        log_max_size="10m",
    )
    stack.callback(guarded_remove_container, api, identity, api_expectation)
    proxy = LoopbackTcpProxy(_container_ipv4(api, network_id), API_CONTAINER_PORT)
    proxy.start()
    stack.callback(proxy.close)
    base_url = f"http://127.0.0.1:{proxy.port}"
    _wait_for_api(base_url)

    expected_containers = frozenset(
        {
            _required_identifier(postgres.id, "container ID"),
            _required_identifier(redis.id, "container ID"),
            _required_identifier(fake.id, "container ID"),
            _required_identifier(api.id, "container ID"),
        }
    )

    async def read_attestation() -> ResourceAttestation:
        await asyncio.to_thread(
            _verify_live_resources,
            identity,
            network,
            network_expectation,
            (
                (postgres, postgres_expectation),
                (redis, redis_expectation),
                (fake, fake_expectation),
                (api, api_expectation),
            ),
            expected_containers,
        )
        sentinel, application_name = await asyncio.to_thread(database.verify)
        await asyncio.to_thread(database.verify_live_application_sessions, _container_ipv4(api, network_id))
        await asyncio.to_thread(redis_state.verify)
        proxy.verify()
        return ResourceAttestation(
            labels=identity.labels,
            network_id=network_id,
            network_internal=True,
            database_name=identity.database_name,
            sentinel=sentinel,
            application_name=application_name,
        )

    async def stop_api() -> None:
        await asyncio.to_thread(api.stop, timeout=CONTAINER_STOP_SECONDS)

    async def start_api() -> None:
        await asyncio.to_thread(api.start)
        await asyncio.to_thread(_wait_for_api, base_url)

    async def reset_database() -> None:
        await asyncio.to_thread(database.reset_database)

    async def clear_redis() -> None:
        await asyncio.to_thread(redis_state.clear)

    async def seed_database() -> SeededIds:
        return await asyncio.to_thread(database.seed)

    async def reset_fakes() -> None:
        await asyncio.to_thread(_fake_control, fake, control_nonce, "reset")

    async def checksum() -> str:
        return await asyncio.to_thread(database.checksum)

    return DockerRunningApi(
        identity=identity,
        base_url=base_url,
        network_id=network_id,
        read_attestation=read_attestation,
        reset_hooks=ResetHooks(
            quiesce=stop_api,
            reset_database=reset_database,
            clear_redis=clear_redis,
            seed=seed_database,
            resume=start_api,
            reset_fakes=reset_fakes,
            checksum=checksum,
            seeded_ids=seeded_ids,
            baseline_checksum=baseline_checksum,
        ),
        database_controller=database,
        redis_controller=redis_state,
        api_container=api,
        fake_container=fake,
        secrets=secrets_,
        control_nonce=control_nonce,
    )


def fake_snapshot(fake: Container, control_nonce: str) -> SnapshotDocument:
    """Return the redacted fake-upstream state through its in-container control channel."""
    payload = _fake_control(fake, control_nonce, "snapshot")
    return SnapshotDocument.model_validate_json(payload)


def _run_postgres(
    client: docker.DockerClient,
    identity: RunIdentity,
    credentials: DatabaseCredentials,
    network: Network,
) -> Container:
    return client.containers.run(
        POSTGRES_IMAGE,
        detach=True,
        name=f"squid-fuzz-postgres-{identity.run_id}",
        network=_required_identifier(network.name, "network name"),
        labels=resource_labels(identity, "postgres"),
        environment={
            "POSTGRES_DB": POSTGRES_DATABASE,
            "POSTGRES_PASSWORD": credentials.administrator_password,
            "POSTGRES_USER": POSTGRES_USER,
            "REDSTONE_SQUID_FUZZ_RUN_ID": identity.run_id,
            "REDSTONE_SQUID_FUZZ_SENTINEL": identity.sentinel,
        },
        user="postgres",
        tmpfs={
            "/var/lib/postgresql/data": "rw,noexec,nosuid,size=512m,uid=999,gid=999,mode=0700",
            "/var/run/postgresql": "rw,noexec,nosuid,size=4m,uid=999,gid=999,mode=0755",
        },
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        mem_limit="768m",
        nano_cpus=750_000_000,
        pids_limit=128,
        log_config=LogConfig(type=LogConfig.types.JSON, config={"max-size": "10m", "max-file": "1"}),
    )


def _run_redis(
    client: docker.DockerClient,
    identity: RunIdentity,
    credentials: RedisCredentials,
    network: Network,
) -> Container:
    rate_limit_key_pattern = "{squid-rate-limit}:v1:*"
    return client.containers.run(
        REDIS_IMAGE,
        [
            "redis-server",
            "--save",
            "",
            "--appendonly",
            "no",
            "--maxmemory",
            "64mb",
            "--maxmemory-policy",
            "noeviction",
            "--user",
            "default",
            "off",
            "--user",
            "coordinator",
            "on",
            f">{credentials.coordinator_password}",
            "~*",
            "+@all",
            "--user",
            "application",
            "on",
            f">{credentials.application_password}",
            f"~{rate_limit_key_pattern}",
            "+ping",
            "+hello",
            "+client|setinfo",
            "+eval",
            "+evalsha",
            "+script|load",
            "+time",
            "+zremrangebyscore",
            "+zcard",
            "+zrange",
            "+zadd",
            "+pexpire",
        ],
        detach=True,
        name=f"squid-fuzz-redis-{identity.run_id}",
        network=_required_identifier(network.name, "network name"),
        labels=resource_labels(identity, "redis"),
        environment={
            "REDSTONE_SQUID_FUZZ_RUN_ID": identity.run_id,
            "REDSTONE_SQUID_FUZZ_SENTINEL": identity.sentinel,
        },
        user="redis",
        tmpfs={"/data": "rw,noexec,nosuid,size=8m,mode=0777"},
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        mem_limit="128m",
        nano_cpus=250_000_000,
        pids_limit=64,
        log_config=LogConfig(type=LogConfig.types.JSON, config={"max-size": "5m", "max-file": "1"}),
    )


def _run_fake(
    client: docker.DockerClient,
    identity: RunIdentity,
    image: Image,
    control_nonce: str,
    network: Network,
) -> Container:
    return client.containers.run(
        _required_identifier(image.id, "image ID"),
        ["python", "-m", "tests.fuzz.api.fake_upstreams"],
        detach=True,
        name=f"squid-fuzz-fake-{identity.run_id}",
        network=_required_identifier(network.name, "network name"),
        labels=resource_labels(identity, "fake"),
        environment={
            CONTROL_NONCE_ENV: control_nonce,
            FAKE_BIND_HOST_ENV: "0.0.0.0",
            FAKE_PORT_ENV: str(FAKE_CONTAINER_PORT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "REDSTONE_SQUID_FUZZ_RUN_ID": identity.run_id,
            "REDSTONE_SQUID_FUZZ_SENTINEL": identity.sentinel,
            "TMPDIR": "/tmp",
        },
        tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        mem_limit="256m",
        nano_cpus=250_000_000,
        pids_limit=64,
        healthcheck={
            "test": [*_FAKE_HEALTHCHECK],
            "interval": 30_000_000_000,
            "timeout": 10_000_000_000,
            "start_period": 10_000_000_000,
            "retries": 3,
        },
        log_config=LogConfig(type=LogConfig.types.JSON, config={"max-size": "5m", "max-file": "1"}),
    )


def _run_api(
    client: docker.DockerClient,
    identity: RunIdentity,
    image: Image,
    network: Network,
    environment: Mapping[str, str],
) -> Container:
    return client.containers.run(
        _required_identifier(image.id, "image ID"),
        detach=True,
        name=f"squid-fuzz-api-{identity.run_id}",
        network=_required_identifier(network.name, "network name"),
        labels=resource_labels(identity, "api"),
        environment=dict(environment),
        tmpfs={"/tmp": "rw,noexec,nosuid,size=32m"},
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        mem_limit="512m",
        nano_cpus=500_000_000,
        pids_limit=128,
        log_config=LogConfig(type=LogConfig.types.JSON, config={"max-size": "10m", "max-file": "1"}),
    )


def _build_production_image(client: docker.DockerClient, identity: RunIdentity) -> Image:
    tag = f"redstone-squid-api-fuzz-base:{identity.run_id}"
    image, _logs = client.images.build(
        path=str(_ROOT),
        dockerfile="Dockerfile",
        tag=tag,
        rm=True,
        forcerm=True,
        buildargs={
            "GIT_COMMIT_HASH": "api-fuzz",
            "GIT_COMMIT_MESSAGE": "disposable api fuzz environment",
            "WITH_OBSERVABILITY": "0",
        },
    )
    return image


def _build_fuzz_image(client: docker.DockerClient, identity: RunIdentity, production_image: Image) -> Image:
    tag = f"redstone-squid-api-fuzz:{identity.run_id}"
    image, _logs = client.images.build(
        path=str(_ROOT),
        dockerfile="Dockerfile.api-fuzz",
        tag=tag,
        rm=True,
        forcerm=True,
        buildargs={"BASE_IMAGE": production_image.id},
    )
    return image


def _remove_image(client: docker.DockerClient, image_id: str) -> None:
    with suppress(docker.errors.ImageNotFound):
        client.images.remove(image_id, force=False, noprune=True)


def _expectation(
    container: Container,
    identity: RunIdentity,
    resource: str,
    network_id: str,
    container_port: int,
    *,
    tmpfs_targets: frozenset[str] = frozenset(),
    tmpfs_options: Mapping[str, str] | None = None,
    require_published_port: bool = True,
    require_identity_environment: bool = True,
    forbidden_environment: frozenset[str] = frozenset(),
    healthcheck_test: tuple[str, ...] | None = None,
    memory_bytes: int = 536_870_912,
    nano_cpus: int = 500_000_000,
    pids_limit: int = 128,
    log_max_size: str = "10m",
    log_max_file: str = "1",
) -> ContainerExpectation:
    container.reload()
    if _single_network_id(container) != network_id:
        msg = "Disposable Docker container is not attached to the created internal network."
        raise UnsafeEnvironmentError(msg)
    return ContainerExpectation(
        container_id=_required_identifier(container.id, "container ID"),
        name=_required_identifier(container.name, "container name"),
        resource=resource,
        network_id=network_id,
        container_port=container_port,
        tmpfs_targets=tmpfs_targets,
        tmpfs_options=tmpfs_options or {},
        require_published_port=require_published_port,
        require_identity_environment=require_identity_environment,
        forbidden_environment=forbidden_environment,
        healthcheck_test=healthcheck_test,
        memory_bytes=memory_bytes,
        nano_cpus=nano_cpus,
        pids_limit=pids_limit,
        log_max_size=log_max_size,
        log_max_file=log_max_file,
    )


def _verify_live_resources(
    identity: RunIdentity,
    network: Network,
    network_expectation: NetworkExpectation,
    containers: tuple[tuple[Container, ContainerExpectation], ...],
    expected_container_ids: frozenset[str],
) -> None:
    for container, expectation in containers:
        container.reload()
        verify_container(container.attrs, identity, expectation)
    network.reload()
    verify_network(
        network.attrs,
        identity,
        NetworkExpectation(network_expectation.network_id, network_expectation.name, expected_container_ids),
    )


def _container_ipv4(container: Container, network_id: str) -> str:
    container.reload()
    networks = _mapping(_mapping(container.attrs.get("NetworkSettings")).get("Networks"))
    candidates = [
        _mapping(value).get("IPAddress")
        for value in networks.values()
        if _mapping(value).get("NetworkID") == network_id
    ]
    if len(candidates) != 1 or not isinstance(candidates[0], str) or not candidates[0]:
        msg = "Disposable Docker container has no unique internal IPv4 address."
        raise UnsafeEnvironmentError(msg)
    return candidates[0]


def _single_network_id(container: Container) -> str:
    networks = _mapping(_mapping(container.attrs.get("NetworkSettings")).get("Networks"))
    network_ids = [_mapping(value).get("NetworkID") for value in networks.values()]
    if len(network_ids) != 1 or not isinstance(network_ids[0], str) or not network_ids[0]:
        msg = "Disposable Docker container does not have exactly one network."
        raise UnsafeEnvironmentError(msg)
    return network_ids[0]


def _wait_for_log(container: Container, marker: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        container.reload()
        if container.status == "exited":
            msg = f"Disposable {container.name} container exited during startup."
            raise RuntimeError(msg)
        logs = container.logs(tail=200).decode(errors="replace")
        if marker in logs:
            return
        time.sleep(0.1)
    msg = f"Disposable {container.name} container did not become ready."
    raise TimeoutError(msg)


def _wait_for_api(base_url: str) -> None:
    deadline = time.monotonic() + API_READY_SECONDS
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/readyz", timeout=0.5) as response:
                if response.status == 200 and len(response.read(1024)) < 1024:
                    return
        except OSError, urllib.error.HTTPError:
            time.sleep(0.1)
    msg = "Disposable API container did not become ready."
    raise TimeoutError(msg)


def _wait_for_fake_container(container: Container) -> None:
    deadline = time.monotonic() + API_READY_SECONDS
    script = (
        f"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{FAKE_CONTAINER_PORT}/__fuzz/ready',timeout=1)"
    )
    while time.monotonic() < deadline:
        container.reload()
        if container.status == "exited":
            msg = "Disposable fake-upstream container exited during startup."
            raise RuntimeError(msg)
        result = container.exec_run(["python", "-c", script])
        if result.exit_code == 0:
            return
        time.sleep(0.1)
    msg = "Disposable fake-upstream container did not become ready."
    raise TimeoutError(msg)


def _fake_control(container: Container, control_nonce: str, action: str) -> bytes:
    if action not in {"reset", "snapshot"}:
        raise ValueError(action)
    method = "POST" if action == "reset" else "GET"
    script = (
        "import os,urllib.request;"
        f"r=urllib.request.Request('http://127.0.0.1:{FAKE_CONTAINER_PORT}/__fuzz/{action}',method='{method}',"
        f"headers={{'{CONTROL_HEADER}':os.environ['REDSTONE_SQUID_FUZZ_CONTROL_NONCE']}});"
        "print(urllib.request.urlopen(r,timeout=2).read().decode())"
    )
    result = container.exec_run(["python", "-c", script], demux=True, environment={CONTROL_NONCE_ENV: control_nonce})
    if result.exit_code != 0:
        msg = "Disposable fake-upstream control request failed."
        raise RuntimeError(msg)
    output = result.output
    if not isinstance(output, tuple) or len(output) != 2:
        msg = "Disposable fake-upstream control output was malformed."
        raise RuntimeError(msg)
    stdout, _stderr = output
    if stdout is None:
        payload = b""
    elif isinstance(stdout, bytes):
        payload = stdout
    else:
        msg = "Disposable fake-upstream control output was not bytes."
        raise RuntimeError(msg)
    if len(payload) > MAX_CONTROL_BYTES:
        msg = "Disposable fake-upstream control response exceeded its limit."
        raise UnsafeEnvironmentError(msg)
    return payload.strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _required_identifier(value: str | None, kind: str) -> str:
    if value is None or not value:
        msg = f"Disposable Docker {kind} was empty."
        raise UnsafeEnvironmentError(msg)
    return value
