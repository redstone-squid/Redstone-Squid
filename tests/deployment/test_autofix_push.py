"""Exercise automatic formatting pushes against a real local Git remote."""

import os
import subprocess
from pathlib import Path

import pytest

PUSH_SCRIPT = Path(__file__).parents[2] / ".github" / "scripts" / "push-autofix.sh"


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit_file(repository: Path, name: str, content: str) -> str:
    (repository / name).write_text(content, encoding="utf-8")
    git(repository, "add", name)
    git(repository, "commit", "-m", f"Update {name}")
    return git(repository, "rev-parse", "HEAD")


@pytest.fixture
def repositories(tmp_path: Path) -> tuple[Path, Path, Path]:
    remote = tmp_path / "remote.git"
    upstream = tmp_path / "upstream"
    formatter = tmp_path / "formatter"
    git(tmp_path, "init", "--bare", "--initial-branch=master", str(remote))
    git(tmp_path, "clone", str(remote), str(upstream))
    git(upstream, "config", "user.name", "CI test")
    git(upstream, "config", "user.email", "ci@example.invalid")
    commit_file(upstream, "catalog.txt", "original\n")
    git(upstream, "push", "origin", "master")
    git(tmp_path, "clone", str(remote), str(formatter))
    git(formatter, "config", "user.name", "CI test")
    git(formatter, "config", "user.email", "ci@example.invalid")
    return remote, upstream, formatter


def push_fixes(formatter: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(PUSH_SCRIPT)],
        cwd=formatter,
        env={**os.environ, "BRANCH": "master"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_autofix_preserves_a_concurrent_commit(repositories: tuple[Path, Path, Path]) -> None:
    remote, upstream, formatter = repositories
    commit_file(formatter, "catalog.txt", "formatted\n")
    upstream_head = commit_file(upstream, "requirements.txt", "updated dependency\n")
    git(upstream, "push", "origin", "master")

    result = push_fixes(formatter)

    assert result.returncode == 0, result.stderr
    assert git(remote, "show", "master:catalog.txt") == "formatted"
    assert git(remote, "show", "master:requirements.txt") == "updated dependency"
    assert git(remote, "rev-parse", "master^") == upstream_head


def test_autofix_leaves_conflicting_upstream_changes_intact(repositories: tuple[Path, Path, Path]) -> None:
    remote, upstream, formatter = repositories
    commit_file(formatter, "catalog.txt", "formatted\n")
    upstream_head = commit_file(upstream, "catalog.txt", "new source references\n")
    git(upstream, "push", "origin", "master")

    result = push_fixes(formatter)

    assert result.returncode != 0
    assert git(remote, "rev-parse", "master") == upstream_head
    assert git(remote, "show", "master:catalog.txt") == "new source references"


def test_autofix_reports_persistent_push_rejection(repositories: tuple[Path, Path, Path]) -> None:
    remote, _, formatter = repositories
    original_head = git(remote, "rev-parse", "master")
    commit_file(formatter, "catalog.txt", "formatted\n")
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    result = push_fixes(formatter)

    assert result.returncode != 0
    assert result.stdout.count("could not be pushed") == 3
    assert git(remote, "rev-parse", "master") == original_head
