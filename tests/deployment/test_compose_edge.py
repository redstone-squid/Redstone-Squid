"""Exercise host file selection with Compose's own parser, without starting containers."""

import json
import os
import shutil
import subprocess
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
