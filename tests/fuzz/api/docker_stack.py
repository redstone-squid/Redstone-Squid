"""Concrete, resource-bounded Docker composition for local API fuzzing."""

import asyncio
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docker
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
from tests.fuzz.api.redis_state import RedisController, RedisLocation

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


def docker_api_environment() -> ApiEnvironment:
    """Return an environment backed by the concrete local Docker starter."""
    return ApiEnvironment(start_docker_stack)


@dataclass(slots=True)
class DockerRunningApi(RunningApi):
    """Running API with narrow lifecycle inspection helpers for integration coverage."""

    database_controller: DatabaseController
    redis_controller: RedisController
    api_container: Container
    secrets: SyntheticSecrets

    def verification_code_count(self) -> int:
        """Return the narrow database mutation count used by lifecycle coverage."""
        return self.database_controller.verification_code_count()

    def redis_keys(self) -> set[bytes]:
        """Return the bounded dedicated Redis namespace contents."""
        return self.redis_controller.keys()

    def fake_snapshot(self) -> SnapshotDocument:
        """Return redacted fake-upstream observations through the control channel."""
        return fake_snapshot(self.api_container, self.identity)


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

    credentials = DatabaseCredentials.for_identity(identity)
    secrets_ = SyntheticSecrets.for_identity(identity)
    postgres = _run_postgres(client, identity, credentials, network)
    postgres_expectation = _expectation(
        postgres,
        identity,
        "postgres",
        POSTGRES_CONTAINER_PORT,
        tmpfs_targets=frozenset({"/var/lib/postgresql/data", "/var/run/postgresql"}),
        require_published_port=False,
    )
    stack.callback(guarded_remove_container, postgres, identity, postgres_expectation)
    redis = _run_redis(client, identity, network)
    redis_expectation = _expectation(
        redis,
        identity,
        "redis",
        REDIS_CONTAINER_PORT,
        tmpfs_targets=frozenset({"/data"}),
        require_published_port=False,
    )
    stack.callback(guarded_remove_container, redis, identity, redis_expectation)
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
    )
    database.wait_until_ready()
    seeded_ids, baseline_checksum = database.bootstrap()
    redis_state.initialize()

    production_image = _build_production_image(client, identity)
    stack.callback(_remove_image, client, _required_identifier(production_image.id, "image ID"))
    fuzz_image = _build_fuzz_image(client, identity, production_image)
    stack.callback(_remove_image, client, _required_identifier(fuzz_image.id, "image ID"))
    endpoints = SyntheticEndpoints(
        api_container_port=API_CONTAINER_PORT,
        postgres_url=database.application_container_url,
        redis_url=redis_state.container_url,
        mojang_profile_url=f"http://127.0.0.1:{FAKE_CONTAINER_PORT}/mojang/profile",
        discord_api_url=f"http://127.0.0.1:{FAKE_CONTAINER_PORT}/discord/api",
        discord_authorize_url=f"http://127.0.0.1:{FAKE_CONTAINER_PORT}/discord/authorize",
    )
    api = _run_api(
        client, identity, fuzz_image, network, synthetic_api_environment(identity, endpoints, secrets_=secrets_)
    )
    api_expectation = _expectation(
        api,
        identity,
        "api",
        API_CONTAINER_PORT,
        tmpfs_targets=frozenset({"/tmp"}),
        require_published_port=False,
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
            _required_identifier(api.id, "container ID"),
        }
    )

    async def read_attestation() -> ResourceAttestation:
        await asyncio.to_thread(
            _verify_live_resources,
            identity,
            network,
            network_expectation,
            ((postgres, postgres_expectation), (redis, redis_expectation), (api, api_expectation)),
            expected_containers,
        )
        sentinel, application_name = await asyncio.to_thread(database.verify)
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
        await asyncio.to_thread(_fake_control, api, identity, "reset")

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
        secrets=secrets_,
    )


def fake_snapshot(api: Container, identity: RunIdentity) -> SnapshotDocument:
    """Return the redacted fake-upstream state through its in-container control channel."""
    payload = _fake_control(api, identity, "snapshot")
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


def _run_redis(client: docker.DockerClient, identity: RunIdentity, network: Network) -> Container:
    return client.containers.run(
        REDIS_IMAGE,
        ["redis-server", "--save", "", "--appendonly", "no", "--maxmemory", "64mb", "--maxmemory-policy", "noeviction"],
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
    container_port: int,
    *,
    tmpfs_targets: frozenset[str] = frozenset(),
    require_published_port: bool = True,
) -> ContainerExpectation:
    container.reload()
    return ContainerExpectation(
        container_id=_required_identifier(container.id, "container ID"),
        name=_required_identifier(container.name, "container name"),
        resource=resource,
        network_id=_single_network_id(container),
        container_port=container_port,
        tmpfs_targets=tmpfs_targets,
        require_published_port=require_published_port,
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
        except (OSError, urllib.error.HTTPError):
            time.sleep(0.1)
    msg = "Disposable API container did not become ready."
    raise TimeoutError(msg)


def _fake_control(container: Container, identity: RunIdentity, action: str) -> bytes:
    if action not in {"reset", "snapshot"}:
        raise ValueError(action)
    method = "POST" if action == "reset" else "GET"
    script = (
        "import os,urllib.request;"
        f"r=urllib.request.Request('http://127.0.0.1:{FAKE_CONTAINER_PORT}/__fuzz/{action}',method='{method}',"
        f"headers={{'{CONTROL_HEADER}':os.environ['REDSTONE_SQUID_FUZZ_CONTROL_NONCE']}});"
        "print(urllib.request.urlopen(r,timeout=2).read().decode())"
    )
    result = container.exec_run(["python", "-c", script], demux=True)
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
