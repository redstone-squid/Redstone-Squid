"""Guard the opt-in, pinned, non-root media worker image contract."""

import re
import subprocess
import tomllib
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[2]
RUNTIME_TARGETS = {
    "runtime-base": (False, False),
    "runtime-media": (True, False),
    "runtime-software-gpu": (False, True),
    "runtime-media-software-gpu": (True, True),
}
FFMPEG_VERSION = "7:7.1.5-0+deb13u1"


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text()


def _required_python_version() -> str:
    """Return the `X.Y` the project declares it needs.

    Hardcoding the interpreter version here just moved the maintenance burden: `c4c10005` bumped
    the Dockerfile and pyproject together and this test kept asserting 3.12, so it failed for
    months on a tree that was internally consistent. Deriving it instead turns the assertion into
    the thing actually worth guarding -- that the image ships the interpreter the code requires.
    """
    metadata = tomllib.loads(_read("pyproject.toml"))
    requires = metadata["project"]["requires-python"]
    match = re.fullmatch(r">=\s*(\d+\.\d+)", requires)
    assert match is not None, f"cannot derive an image tag from requires-python {requires!r}"
    return match.group(1)


def _argument(dockerfile: str, name: str) -> str:
    match = re.search(rf"^ARG {re.escape(name)}=([^\s]+)$", dockerfile, flags=re.MULTILINE)
    assert match is not None
    return match.group(1)


def _command(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=check, capture_output=True, text=True)


@pytest.fixture(scope="module")
def runtime_images() -> Iterator[dict[str, str]]:
    """Build each runtime image target and remove its local tag after inspection."""
    identity = uuid.uuid4().hex
    images = {target: f"redstone-squid-contract:{identity}-{target}" for target in RUNTIME_TARGETS}
    try:
        for target, image in images.items():
            _command(
                "docker",
                "build",
                "--target",
                target,
                "--build-arg",
                "WITH_OBSERVABILITY=0",
                "--tag",
                image,
                str(PROJECT_ROOT),
            )
        yield images
    finally:
        _command("docker", "image", "rm", "--force", *images.values(), check=False)


def _run_image(image: str, executable: str, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _command("docker", "run", "--rm", "--entrypoint", executable, image, *arguments, check=check)


def test_ffmpeg_install_is_pinned_to_base_and_debian_snapshot() -> None:
    dockerfile = _read("Dockerfile")

    python_image = _argument(dockerfile, "PYTHON_IMAGE")
    snapshot = _argument(dockerfile, "DEBIAN_SNAPSHOT")
    ffmpeg_version = _argument(dockerfile, "FFMPEG_VERSION")

    assert re.match(r"# syntax=docker/dockerfile:1@sha256:[0-9a-f]{64}\n", dockerfile)
    expected_python = re.escape(_required_python_version())
    assert re.fullmatch(rf"python:{expected_python}-slim@sha256:[0-9a-f]{{64}}", python_image)
    assert re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", snapshot)
    assert ffmpeg_version == FFMPEG_VERSION
    assert dockerfile.count("snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}") == 2
    assert dockerfile.count("snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}") == 2


def test_deployment_consumers_select_named_runtime_targets() -> None:
    compose = _read("compose.yml")
    workflow = _read(".github/workflows/cd.yml")
    example_environment = _read(".env.example")
    worker = compose.split("\n  worker:\n", maxsplit=1)[1].split("\n  redis:\n", maxsplit=1)[0]

    assert "target: runtime-base" in compose
    assert "target: runtime-media" in worker
    assert "--target runtime-base" in workflow
    assert "--target runtime-media" in workflow
    assert "WITH_MEDIA" not in compose
    assert "WITH_MEDIA" not in workflow
    assert "SQUID_MEDIA_FFMPEG: /usr/bin/ffmpeg" in worker
    assert "SQUID_MEDIA_FFPROBE: /usr/bin/ffprobe" in worker
    assert "SQUID_MEDIA_WORKING_DIRECTORY: /var/lib/app/media-tmp" in worker
    assert not any(line.startswith("SQUID_MEDIA_ENABLED=") for line in example_environment.splitlines())
    assert "SQUID_MEDIA_ENABLED" not in compose


@pytest.mark.docker
def test_runtime_targets_install_only_their_declared_features(runtime_images: dict[str, str]) -> None:
    for target, image in runtime_images.items():
        with_media, with_software_gpu = RUNTIME_TARGETS[target]
        ffmpeg = _run_image(
            image,
            "/usr/bin/dpkg-query",
            "--show",
            "--showformat=${Version}",
            "ffmpeg",
            check=False,
        )
        assert (ffmpeg.returncode == 0) is with_media
        if with_media:
            assert ffmpeg.stdout == FFMPEG_VERSION

        for package in ("libvulkan1", "mesa-vulkan-drivers"):
            query = _run_image(image, "/usr/bin/dpkg-query", "--show", package, check=False)
            assert (query.returncode == 0) is with_software_gpu


@pytest.mark.docker
def test_runtime_targets_preserve_unprivileged_directory_contract(runtime_images: dict[str, str]) -> None:
    for image in runtime_images.values():
        configured_user = _command("docker", "image", "inspect", "--format={{.Config.User}}", image)
        assert configured_user.stdout.strip() == "appuser"
        assert _run_image(image, "/usr/bin/id", "-u").stdout.strip() == "10001"

        modes = _run_image(
            image,
            "/usr/bin/stat",
            "--format=%a:%u",
            "/var/log/app",
            "/var/lib/app",
            "/var/lib/app/objects",
            "/var/lib/app/.cache",
            "/var/lib/app/media-tmp",
            "/var/lib/app/tmp",
        ).stdout.splitlines()
        assert modes == ["750:10001"] * 3 + ["700:10001"] * 3


def test_worker_drops_privileges_and_uses_private_temporary_directories() -> None:
    compose = _read("compose.yml")
    worker = compose.split("\n  worker:\n", maxsplit=1)[1].split("\n  redis:\n", maxsplit=1)[0]

    assert "cap_drop:\n      - ALL" in worker
    assert "security_opt:\n      - no-new-privileges:true" in worker


def test_backend_build_context_excludes_independent_clients() -> None:
    dockerignore = _read(".dockerignore")

    assert "minecraft/" in dockerignore.splitlines()
    assert "web/" in dockerignore.splitlines()


def test_documented_media_settings_and_resource_arithmetic_are_complete() -> None:
    documentation = _read("docs/media-normalization.md")
    example_environment = _read(".env.example")
    required_names = {
        "SQUID_MEDIA_ENABLED",
        "SQUID_MEDIA_FFMPEG",
        "SQUID_MEDIA_FFPROBE",
        "SQUID_MEDIA_WORKING_DIRECTORY",
        "SQUID_WORKER_MEDIA_JOB_CONCURRENCY",
        "SQUID_MEDIA_MEMORY_BYTES",
        "SQUID_MEDIA_CPU_SECONDS",
        "SQUID_MEDIA_MAX_OPEN_FILES",
        "SQUID_MEDIA_THREADS",
    }

    for name in required_names:
        assert name in documentation
        assert name in example_environment
    assert "1.5 GiB" in documentation
    assert "3 GiB" in documentation
    assert "disabled by default" in documentation
    assert "--target runtime-media" in documentation
    assert "WITH_MEDIA" not in documentation
