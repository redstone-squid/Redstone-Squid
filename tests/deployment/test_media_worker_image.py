"""Guard the opt-in, pinned, non-root media worker image contract."""

import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]


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


def test_ffmpeg_install_is_pinned_to_base_and_debian_snapshot() -> None:
    dockerfile = _read("Dockerfile")

    python_image = _argument(dockerfile, "PYTHON_IMAGE")
    snapshot = _argument(dockerfile, "DEBIAN_SNAPSHOT")
    ffmpeg_version = _argument(dockerfile, "FFMPEG_VERSION")

    assert re.match(r"# syntax=docker/dockerfile:1@sha256:[0-9a-f]{64}\n", dockerfile)
    expected_python = re.escape(_required_python_version())
    assert re.fullmatch(rf"python:{expected_python}-slim@sha256:[0-9a-f]{{64}}", python_image)
    assert re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", snapshot)
    assert ffmpeg_version == "7:7.1.5-0+deb13u1"
    assert dockerfile.count("snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}") == 2
    assert dockerfile.count("snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}") == 2
    assert '"ffmpeg=${FFMPEG_VERSION}"' in dockerfile
    assert "dpkg-query --showformat='${Version}' --show ffmpeg" in dockerfile


def test_media_toolchain_is_present_but_feature_remains_opt_in() -> None:
    dockerfile = _read("Dockerfile")
    compose = _read("compose.yml")
    workflow = _read(".github/workflows/cd.yml")
    example_environment = _read(".env.example")
    worker = compose.split("\n  worker:\n", maxsplit=1)[1].split("\n  redis:\n", maxsplit=1)[0]

    assert _argument(dockerfile, "WITH_MEDIA") == "0"
    assert "WITH_MEDIA: ${WITH_MEDIA:-1}" in worker
    assert "--build-arg WITH_MEDIA=1" in workflow
    assert "SQUID_MEDIA_FFMPEG: /usr/bin/ffmpeg" in worker
    assert "SQUID_MEDIA_FFPROBE: /usr/bin/ffprobe" in worker
    assert "SQUID_MEDIA_WORKING_DIRECTORY: /var/lib/app/media-tmp" in worker
    assert not any(line.startswith("SQUID_MEDIA_ENABLED=") for line in example_environment.splitlines())
    assert "SQUID_MEDIA_ENABLED" not in compose
    assert "SQUID_MEDIA_ENABLED" not in dockerfile


def test_runtime_user_owns_private_temporary_directories() -> None:
    dockerfile = _read("Dockerfile")
    compose = _read("compose.yml")
    worker = compose.split("\n  worker:\n", maxsplit=1)[1].split("\n  redis:\n", maxsplit=1)[0]

    assert "TMPDIR=/var/lib/app/tmp" in dockerfile
    assert "-m 0700 /var/lib/app/.cache /var/lib/app/media-tmp /var/lib/app/tmp" in dockerfile
    assert dockerfile.rfind("USER appuser") > dockerfile.rfind("RUN ")
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
