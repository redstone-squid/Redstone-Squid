"""Fail-closed Docker inspection and cleanup for disposable API resources."""

import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from tests.fuzz.api.environment import (
    RESOURCE_LABEL,
    RUN_ID_ENV,
    SENTINEL_ENV,
    RunIdentity,
    UnsafeEnvironmentError,
)

_RESOURCE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,47}")


class DockerContainerHandle(Protocol):
    """Docker SDK container operations needed by guarded cleanup."""

    @property
    def attrs(self) -> Mapping[str, object]: ...

    def reload(self) -> None: ...

    def remove(self, *, force: bool, v: bool) -> None: ...


class DockerNetworkHandle(Protocol):
    """Docker SDK network operations needed by guarded cleanup."""

    @property
    def attrs(self) -> Mapping[str, object]: ...

    def reload(self) -> None: ...

    def remove(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ContainerExpectation:
    """Exact immutable facts assigned when one disposable container is created."""

    container_id: str
    name: str
    resource: str
    network_id: str
    container_port: int
    tmpfs_targets: frozenset[str] = frozenset()
    require_published_port: bool = True

    def __post_init__(self) -> None:
        if not self.container_id or not self.network_id:
            msg = "Expected Docker IDs must be non-empty."
            raise ValueError(msg)
        if _RESOURCE_NAME.fullmatch(self.resource) is None:
            msg = "Docker resource labels must be short lowercase identifiers."
            raise ValueError(msg)
        if not 1 <= self.container_port <= 65535:
            msg = "Expected Docker container ports must be between 1 and 65535."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class NetworkExpectation:
    """Exact immutable facts assigned when the disposable internal network is created."""

    network_id: str
    name: str
    attached_container_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.network_id or not self.name:
            msg = "Expected Docker network identity must be non-empty."
            raise ValueError(msg)


def resource_labels(identity: RunIdentity, resource: str) -> dict[str, str]:
    """Return exact labels for one kind of disposable Docker resource."""
    if _RESOURCE_NAME.fullmatch(resource) is None:
        msg = "Docker resource labels must be short lowercase identifiers."
        raise ValueError(msg)
    return {**identity.labels, RESOURCE_LABEL: resource}


def verify_container(attrs: Mapping[str, object], identity: RunIdentity, expected: ContainerExpectation) -> None:
    """Require exact ownership, isolation, resource limits, and host binding."""
    failures: list[str] = []
    if attrs.get("Id") != expected.container_id:
        failures.append("container_id")
    if attrs.get("Name") != f"/{expected.name}":
        failures.append("container_name")

    config = _mapping(attrs.get("Config"))
    labels = _string_mapping(config.get("Labels"))
    if any(labels.get(key) != value for key, value in resource_labels(identity, expected.resource).items()):
        failures.append("labels")
    environment = _environment(config.get("Env"))
    if environment.get(RUN_ID_ENV) != identity.run_id:
        failures.append("run_id")
    sentinel = environment.get(SENTINEL_ENV)
    if sentinel is None or not secrets.compare_digest(sentinel, identity.sentinel):
        failures.append("sentinel")

    network_settings = _mapping(attrs.get("NetworkSettings"))
    networks = _mapping(network_settings.get("Networks"))
    attached_ids = {_mapping(value).get("NetworkID") for value in networks.values()}
    if attached_ids != {expected.network_id}:
        failures.append("network_id")
    _verify_ports(network_settings, expected, failures)

    host_config = _mapping(attrs.get("HostConfig"))
    if not _positive_int(host_config.get("Memory")):
        failures.append("memory_limit")
    if not _positive_int(host_config.get("NanoCpus")):
        failures.append("cpu_limit")
    if not _positive_int(host_config.get("PidsLimit")):
        failures.append("pid_limit")
    if host_config.get("ReadonlyRootfs") is not True:
        failures.append("readonly_root")
    if host_config.get("Privileged") is not False:
        failures.append("privileged")
    cap_drop = host_config.get("CapDrop")
    if not isinstance(cap_drop, list) or "ALL" not in cap_drop:
        failures.append("cap_drop")
    security_options = host_config.get("SecurityOpt")
    if not isinstance(security_options, list) or "no-new-privileges:true" not in security_options:
        failures.append("security_options")
    if not _empty_collection(host_config.get("CapAdd")):
        failures.append("cap_add")
    if not _empty_collection(host_config.get("Binds")):
        failures.append("binds")
    if not _empty_collection(host_config.get("Devices")):
        failures.append("devices")
    if host_config.get("PidMode") not in {None, ""}:
        failures.append("pid_mode")
    tmpfs = _mapping(host_config.get("Tmpfs"))
    if set(tmpfs) != expected.tmpfs_targets:
        failures.append("tmpfs")
    mounts = attrs.get("Mounts")
    if not isinstance(mounts, list) or any(
        _mapping(mount).get("Type") != "tmpfs" or _mapping(mount).get("Destination") not in expected.tmpfs_targets
        for mount in mounts
    ):
        failures.append("mounts")
    log_config = _mapping(host_config.get("LogConfig"))
    log_options = _string_mapping(log_config.get("Config"))
    if log_config.get("Type") != "json-file" or not log_options.get("max-size") or not log_options.get("max-file"):
        failures.append("log_limit")
    _raise_failures("container", failures)


def verify_network(attrs: Mapping[str, object], identity: RunIdentity, expected: NetworkExpectation) -> None:
    """Require the exact empty internal network before removal."""
    failures: list[str] = []
    if attrs.get("Id") != expected.network_id:
        failures.append("network_id")
    if attrs.get("Name") != expected.name:
        failures.append("network_name")
    labels = _string_mapping(attrs.get("Labels"))
    if any(labels.get(key) != value for key, value in resource_labels(identity, "network").items()):
        failures.append("labels")
    if attrs.get("Internal") is not True:
        failures.append("internal")
    containers = _mapping(attrs.get("Containers"))
    if frozenset(str(container_id) for container_id in containers) != expected.attached_container_ids:
        failures.append("attached_containers")
    _raise_failures("network", failures)


def guarded_remove_container(
    handle: DockerContainerHandle, identity: RunIdentity, expected: ContainerExpectation
) -> None:
    """Re-inspect and remove only the exact still-attested container."""
    handle.reload()
    verify_container(handle.attrs, identity, expected)
    handle.remove(force=True, v=True)


def guarded_remove_network(handle: DockerNetworkHandle, identity: RunIdentity, expected: NetworkExpectation) -> None:
    """Re-inspect and remove only the exact still-attested empty network."""
    handle.reload()
    verify_network(handle.attrs, identity, expected)
    handle.remove()


def validate_local_docker_endpoint(base_url: str) -> None:
    """Refuse TCP/SSH Docker daemons whose loopback is not coordinator loopback."""
    local_transport = base_url == "http+docker://localhost" or base_url.startswith("unix:///")
    if not local_transport or "?" in base_url or "#" in base_url:
        msg = "API fuzzing requires a local Unix-socket Docker daemon."
        raise UnsafeEnvironmentError(msg)


def _verify_ports(network_settings: Mapping[str, object], expected: ContainerExpectation, failures: list[str]) -> None:
    ports = _mapping(network_settings.get("Ports"))
    expected_key = f"{expected.container_port}/tcp"
    published = {key: value for key, value in ports.items() if value is not None}
    if not expected.require_published_port:
        if published:
            failures.append("published_ports")
        return
    bindings = published.get(expected_key)
    if len(published) != 1 or not isinstance(bindings, list) or len(bindings) != 1:
        failures.append("published_ports")
        return
    binding = _mapping(bindings[0])
    host_port = binding.get("HostPort")
    if (
        binding.get("HostIp") != "127.0.0.1"
        or not isinstance(host_port, str)
        or not host_port.isdigit()
        or not 1 <= int(host_port) <= 65_535
    ):
        failures.append("published_ports")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _string_mapping(value: object) -> dict[str, str]:
    mapping = _mapping(value)
    return {key: item for key, item in mapping.items() if isinstance(key, str) and isinstance(item, str)}


def _environment(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    environment: dict[str, str] = {}
    for item in value:
        if isinstance(item, str):
            key, separator, setting = item.partition("=")
            if separator:
                environment[key] = setting
    return environment


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _empty_collection(value: object) -> bool:
    return value is None or value == [] or value == ()


def _raise_failures(resource: str, failures: list[str]) -> None:
    if failures:
        names = ", ".join(failures)
        msg = f"Disposable Docker {resource} attestation failed: {names}."
        raise UnsafeEnvironmentError(msg)
