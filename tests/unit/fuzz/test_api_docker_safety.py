"""Fail-closed Docker resource cleanup tests without a Docker daemon."""

import pytest

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
from tests.fuzz.api.environment import RunIdentity, UnsafeEnvironmentError


class FakeContainer:
    def __init__(self, attrs: dict[str, object]) -> None:
        self.attrs = attrs
        self.reloaded = False
        self.removed = False

    def reload(self) -> None:
        self.reloaded = True

    def remove(self, *, force: bool, v: bool) -> None:
        assert force
        assert v
        self.removed = True


class FakeNetwork:
    def __init__(self, attrs: dict[str, object]) -> None:
        self.attrs = attrs
        self.reloaded = False
        self.removed = False

    def reload(self) -> None:
        self.reloaded = True

    def remove(self) -> None:
        self.removed = True


def identity() -> RunIdentity:
    return RunIdentity(
        run_id="0123456789abcdef0123456789abcdef",
        sentinel="synthetic-sentinel-with-sufficient-entropy",
        database_name="squid_fuzz_0123456789abcdef0123456789abcdef",
        application_name="squid-api-fuzz-0123456789abcdef0123456789abcdef",
        network_name="redstone-squid-api-fuzz-0123456789abcdef0123456789abcdef",
    )


def expectation() -> ContainerExpectation:
    return ContainerExpectation(
        container_id="container-id",
        name="squid-fuzz-api",
        resource="api",
        network_id="network-id",
        container_port=8000,
    )


def container_attrs() -> dict[str, object]:
    run = identity()
    return {
        "Id": "container-id",
        "Name": "/squid-fuzz-api",
        "Config": {
            "Labels": resource_labels(run, "api"),
            "Env": [
                f"REDSTONE_SQUID_FUZZ_RUN_ID={run.run_id}",
                f"REDSTONE_SQUID_FUZZ_SENTINEL={run.sentinel}",
            ],
        },
        "NetworkSettings": {
            "Networks": {"fuzz": {"NetworkID": "network-id"}},
            "Ports": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}]},
        },
        "HostConfig": {
            "Memory": 536_870_912,
            "NanoCpus": 500_000_000,
            "PidsLimit": 128,
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "Binds": None,
            "Devices": None,
            "PidMode": "",
            "Tmpfs": {},
            "SecurityOpt": ["no-new-privileges:true"],
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "1"}},
        },
        "Mounts": [],
    }


def network_attrs() -> dict[str, object]:
    return {
        "Id": "network-id",
        "Name": identity().network_name,
        "Labels": resource_labels(identity(), "network"),
        "Internal": True,
        "Containers": {},
    }


@pytest.mark.parametrize(
    ("mutate", "failure"),
    [
        (lambda attrs: attrs.update(Id="other"), "container_id"),
        (lambda attrs: attrs.update(Name="/production-api"), "container_name"),
        (lambda attrs: attrs["Config"].update(Labels={}), "labels"),
        (lambda attrs: attrs["Config"].update(Env=[]), "run_id"),
        (lambda attrs: attrs["NetworkSettings"].update(Networks={"fuzz": {"NetworkID": "other"}}), "network_id"),
        (
            lambda attrs: attrs["NetworkSettings"].update(
                Ports={"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49152"}]}
            ),
            "published_ports",
        ),
        (
            lambda attrs: attrs["NetworkSettings"].update(
                Ports={"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "0"}]}
            ),
            "published_ports",
        ),
        (lambda attrs: attrs["HostConfig"].update(Memory=0), "memory_limit"),
        (lambda attrs: attrs["HostConfig"].update(Memory=1_073_741_824), "memory_limit"),
        (lambda attrs: attrs["HostConfig"].update(NanoCpus=0), "cpu_limit"),
        (lambda attrs: attrs["HostConfig"].update(NanoCpus=1_000_000_000), "cpu_limit"),
        (lambda attrs: attrs["HostConfig"].update(PidsLimit=0), "pid_limit"),
        (lambda attrs: attrs["HostConfig"].update(PidsLimit=512), "pid_limit"),
        (lambda attrs: attrs["HostConfig"].update(ReadonlyRootfs=False), "readonly_root"),
        (lambda attrs: attrs["HostConfig"].update(Privileged=True), "privileged"),
        (lambda attrs: attrs["HostConfig"].update(CapAdd=["SYS_ADMIN"]), "cap_add"),
        (lambda attrs: attrs["HostConfig"].update(Binds=["/host:/data"]), "binds"),
        (lambda attrs: attrs["HostConfig"].update(Devices=[{"PathOnHost": "/dev/kvm"}]), "devices"),
        (lambda attrs: attrs.update(Mounts=[{"Type": "bind", "Destination": "/data"}]), "mounts"),
        (lambda attrs: attrs["HostConfig"].update(CapDrop=[]), "cap_drop"),
        (lambda attrs: attrs["HostConfig"].update(SecurityOpt=[]), "security_options"),
        (lambda attrs: attrs["HostConfig"].update(LogConfig={}), "log_limit"),
        (
            lambda attrs: attrs["HostConfig"].update(
                LogConfig={"Type": "json-file", "Config": {"max-size": "100m", "max-file": "1"}}
            ),
            "log_limit",
        ),
    ],
)
def test_container_attestation_requires_every_safety_fact(mutate, failure: str) -> None:
    attrs = container_attrs()
    mutate(attrs)

    with pytest.raises(UnsafeEnvironmentError, match=failure):
        verify_container(attrs, identity(), expectation())


def test_guarded_container_cleanup_reinspects_and_removes_exact_resource() -> None:
    handle = FakeContainer(container_attrs())

    guarded_remove_container(handle, identity(), expectation())

    assert handle.reloaded
    assert handle.removed


def test_container_attestation_can_avoid_target_visible_identity_environment() -> None:
    attrs = container_attrs()
    config = attrs["Config"]
    assert isinstance(config, dict)
    config["Env"] = []
    expected = ContainerExpectation(
        container_id="container-id",
        name="squid-fuzz-api",
        resource="api",
        network_id="network-id",
        container_port=8000,
        require_identity_environment=False,
    )

    verify_container(attrs, identity(), expected)


def test_container_attestation_rejects_forbidden_target_visible_environment() -> None:
    attrs = container_attrs()
    expected = ContainerExpectation(
        container_id="container-id",
        name="squid-fuzz-api",
        resource="api",
        network_id="network-id",
        container_port=8000,
        require_identity_environment=False,
        forbidden_environment=frozenset({"REDSTONE_SQUID_FUZZ_SENTINEL"}),
    )

    with pytest.raises(UnsafeEnvironmentError, match="forbidden_environment"):
        verify_container(attrs, identity(), expected)


def test_container_attestation_rejects_an_unexpected_healthcheck() -> None:
    attrs = container_attrs()
    config = attrs["Config"]
    assert isinstance(config, dict)
    config["Healthcheck"] = {"Test": ["CMD-SHELL", "curl -f http://localhost:8000/readyz"]}
    expected = ContainerExpectation(
        container_id="container-id",
        name="squid-fuzz-api",
        resource="api",
        network_id="network-id",
        container_port=8000,
        healthcheck_test=("CMD-SHELL", "python -c 'pass'"),
    )

    with pytest.raises(UnsafeEnvironmentError, match="healthcheck"):
        verify_container(attrs, identity(), expected)


def test_container_attestation_rejects_changed_tmpfs_options() -> None:
    attrs = container_attrs()
    host_config = attrs["HostConfig"]
    assert isinstance(host_config, dict)
    host_config["Tmpfs"] = {"/tmp": "rw,noexec,nosuid,size=64m"}
    attrs["Mounts"] = [{"Type": "tmpfs", "Destination": "/tmp"}]
    expected = ContainerExpectation(
        container_id="container-id",
        name="squid-fuzz-api",
        resource="api",
        network_id="network-id",
        container_port=8000,
        tmpfs_targets=frozenset({"/tmp"}),
        tmpfs_options={"/tmp": "rw,noexec,nosuid,size=32m"},
    )

    with pytest.raises(UnsafeEnvironmentError, match="tmpfs"):
        verify_container(attrs, identity(), expected)


def test_guarded_container_cleanup_leaks_a_resource_if_live_labels_changed() -> None:
    attrs = container_attrs()
    config = attrs["Config"]
    assert isinstance(config, dict)
    config["Labels"] = {}
    handle = FakeContainer(attrs)

    with pytest.raises(UnsafeEnvironmentError, match="labels"):
        guarded_remove_container(handle, identity(), expectation())
    assert handle.reloaded
    assert not handle.removed


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("Id", "other", "network_id"),
        ("Name", "production", "network_name"),
        ("Labels", {}, "labels"),
        ("Internal", False, "internal"),
        ("Containers", {"container-id": {}}, "attached_containers"),
    ],
)
def test_network_attestation_requires_every_safety_fact(field: str, value: object, failure: str) -> None:
    attrs = network_attrs()
    attrs[field] = value

    with pytest.raises(UnsafeEnvironmentError, match=failure):
        verify_network(attrs, identity(), NetworkExpectation("network-id", identity().network_name))


def test_network_attestation_accepts_only_the_exact_live_attachment_set() -> None:
    attrs = network_attrs()
    attrs["Containers"] = {"container-id": {}}

    verify_network(
        attrs,
        identity(),
        NetworkExpectation("network-id", identity().network_name, frozenset({"container-id"})),
    )


def test_guarded_network_cleanup_reinspects_and_removes_only_an_empty_network() -> None:
    handle = FakeNetwork(network_attrs())

    guarded_remove_network(handle, identity(), NetworkExpectation("network-id", identity().network_name))

    assert handle.reloaded
    assert handle.removed


@pytest.mark.parametrize(
    "url",
    [
        "tcp://127.0.0.1:2375",
        "ssh://builder/run/docker.sock",
        "https://docker.test",
        "http+docker://localhost.attacker.test",
        "unix://relative.sock",
        "unix:///var/run/docker.sock?other=true",
    ],
)
def test_remote_docker_daemons_are_refused(url: str) -> None:
    with pytest.raises(UnsafeEnvironmentError, match="local Unix-socket"):
        validate_local_docker_endpoint(url)


def test_local_docker_daemon_transports_are_allowed() -> None:
    validate_local_docker_endpoint("unix:///var/run/docker.sock")
    validate_local_docker_endpoint("http+docker://localhost")
