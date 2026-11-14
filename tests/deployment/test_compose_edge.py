"""Exercise host file selection with Compose's own parser, without starting containers."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).parents[2]
COMPOSE_FILES = ("compose.yml", "compose.override.yml", "deploy/compose.edge.yml", "deploy/compose.production.yml")


@pytest.fixture
def compose_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is required to validate deployment configuration")
    for key in os.environ:
        if key.startswith(("COMPOSE_", "SQUID_")) or key in {"SITE_URL", "API_BASE_URL", "EDGE_NETWORK", "WEB_IMAGE"}:
            monkeypatch.delenv(key)
    for name in COMPOSE_FILES:
        destination = tmp_path / name
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / name, destination)
    (tmp_path / ".env").write_text("")
    return tmp_path


def compose_config(project: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_default_development_stays_independent_of_the_gateway(compose_project: Path) -> None:
    config = compose_config(compose_project)

    assert "web" not in config["services"]
    assert "edge" not in config["networks"]
    assert config["services"]["api"]["ports"][0]["published"] == "8000"
    assert "migrate" in config["services"]["api"]["depends_on"]


@pytest.mark.parametrize("with_renderer", [False, True])
def test_plain_compose_preserves_host_routing_and_local_mounts(compose_project: Path, with_renderer: bool) -> None:
    files = ["compose.yml", "compose.override.yml"]
    if with_renderer:
        files.append("compose.render.yml")
        (compose_project / "compose.render.yml").write_text(
            "services:\n  api:\n    volumes:\n      - ./pack.zip:/run/resources/pack.zip:ro\n"
        )
    files.append("deploy/compose.edge.yml")
    (compose_project / ".env").write_text(
        f"COMPOSE_FILE={':'.join(files)}\n"
        "COMPOSE_PROJECT_NAME=redstone-squid\n"
        "SITE_URL=https://squid.test\n"
        "EDGE_NETWORK=test-edge\n"
    )

    config = compose_config(compose_project)
    api = config["services"]["api"]
    web = config["services"]["web"]

    assert config["name"] == "redstone-squid"
    assert not api.get("ports")
    assert api["networks"]["edge"]["aliases"] == ["redstone-api"]
    assert "default" in api["networks"]
    assert web["networks"]["edge"]["aliases"] == ["redstone-web"]
    assert not web.get("ports")
    assert web["environment"]["API_BASE_URL"] == "https://squid.test"
    assert web["environment"]["SITE_URL"] == "https://squid.test"
    assert web["build"]["context"] == str(compose_project / "web")
    assert config["networks"]["edge"]["name"] == "test-edge"
    assert config["networks"]["edge"]["external"] is True
    assert not config["services"]["pgweb"].get("profiles")
    if with_renderer:
        assert any(volume["target"] == "/run/resources/pack.zip" for volume in api["volumes"])


@pytest.mark.parametrize("gateway", [False, True])
@pytest.mark.parametrize("fail_cutover", [False, True])
def test_release_and_rollback_preserve_host_selection(
    compose_project: Path, monkeypatch: pytest.MonkeyPatch, gateway: bool, fail_cutover: bool
) -> None:
    real_docker = shutil.which("docker")
    assert real_docker is not None
    if gateway:
        (compose_project / ".env").write_text(
            "COMPOSE_FILE=compose.yml:compose.override.yml:compose.render.yml:deploy/compose.edge.yml\n"
            "SITE_URL=https://squid.test\n"
        )
        (compose_project / "compose.render.yml").write_text(
            "services:\n  api:\n    volumes:\n      - ./pack.zip:/run/resources/pack.zip:ro\n"
        )
    previous_app = f"app@sha256:{'1' * 64}"
    previous_worker = f"worker@sha256:{'2' * 64}"
    app = f"app@sha256:{'3' * 64}"
    worker = f"worker@sha256:{'4' * 64}"
    state = compose_project / ".deploy"
    state.mkdir()
    (state / "current-release").write_text(f"{previous_app}\n{previous_worker}\n")

    # Use the real Compose parser; replace only daemon operations so no live
    # containers are touched while exercising cutover and rollback.
    bin_directory = compose_project / "bin"
    bin_directory.mkdir()
    fake_docker = bin_directory / "docker"
    fake_docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys\n"
        "from pathlib import Path\n"
        f"docker = {real_docker!r}\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['compose', 'config']:\n"
        "    sys.exit(subprocess.run([docker, *args]).returncode)\n"
        "result = subprocess.run([docker, 'compose', '--profile', '*', 'config', '--format', 'json'],\n"
        "                        capture_output=True, text=True, check=True)\n"
        "with open('operations.jsonl', 'a') as log:\n"
        "    log.write(json.dumps({'args': args, 'config': json.loads(result.stdout)}) + '\\n')\n"
        "marker = Path('failed-cutover')\n"
        "if args[:2] == ['compose', 'up'] and os.environ['FAIL_CUTOVER'] == '1' and not marker.exists():\n"
        "    marker.touch()\n"
        "    sys.exit(1)\n"
    )
    fake_docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_directory}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAIL_CUTOVER", str(int(fail_cutover)))

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts/deploy.sh"), app, worker],
        cwd=compose_project,
        capture_output=True,
        text=True,
    )

    assert result.returncode == int(fail_cutover), result.stderr
    operations = [json.loads(line) for line in (compose_project / "operations.jsonl").read_text().splitlines()]
    assert len(operations) == (4 if fail_cutover else 3)
    for operation in operations:
        config = operation["config"]
        services = config["services"]
        rolling_back = operation is operations[-1] and fail_cutover
        assert services["api"]["image"] == (previous_app if rolling_back else app)
        assert services["worker"]["image"] == (previous_worker if rolling_back else worker)
        assert services["migrate"]["image"] == services["api"]["image"]
        assert "migrate" not in services["api"]["depends_on"]
        if gateway:
            assert not services["api"].get("ports")
            assert services["api"]["networks"]["edge"]["aliases"] == ["redstone-api"]
            assert any(volume["target"] == "/run/resources/pack.zip" for volume in services["api"]["volumes"])
            assert "web" in services
        else:
            assert "web" not in services
        if operation["args"][1] == "up":
            assert operation["args"][-4:] == ["redis", "api", "bot", "worker"]
    expected = f"{previous_app}\n{previous_worker}\n" if fail_cutover else f"{app}\n{worker}\n"
    assert (state / "current-release").read_text() == expected
