"""Exercise the Termux launchers without an Android toolchain."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires a POSIX shell")


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "moved checkout"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name in ("termux.sh", "android-link-with-libpython3.sh"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uname = bin_dir / "uname"
    uname.write_text("#!/bin/sh\nprintf 'Android\\n'\n")
    uname.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PREFIX", str(tmp_path / "usr"))
    for name in ("UV_CACHE_DIR", "UV_LINK_MODE", "ANDROID_API_LEVEL", "CARGO_PROFILE_RELEASE_LTO", "CMAKE_ARGS"):
        monkeypatch.delenv(name, raising=False)
    return root


def test_relocated_checkout_preserves_command_arguments(checkout: Path, tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "sh",
            str(checkout / "scripts/termux.sh"),
            sys.executable,
            "-c",
            "import json, os, sys; print(json.dumps([dict(os.environ), sys.argv[1:]])); sys.exit(17)",
            "argument with spaces",
            "",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 17
    env, args = json.loads(result.stdout)
    assert args == ["argument with spaces", ""]
    assert env["UV_CACHE_DIR"] == str(checkout / ".cache/uv")
    assert env["UV_LINK_MODE"] == "copy"
    assert env["ANDROID_API_LEVEL"] == "24"
    assert env["CARGO_PROFILE_RELEASE_LTO"] == "off"
    assert env["CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER"] == str(checkout / "scripts/android-link-with-libpython3.sh")
    assert f"-DPython3_SABI_LIBRARY={tmp_path}/usr/lib/libpython3.so" in env["CMAKE_ARGS"]


def test_default_sync_preserves_overrides(checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uv = tmp_path / "bin/uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$@" "$ANDROID_API_LEVEL" "$UV_CACHE_DIR" "$CMAKE_ARGS"\n')
    uv.chmod(0o755)
    monkeypatch.setenv("ANDROID_API_LEVEL", "28")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "custom cache"))
    monkeypatch.setenv("CMAKE_ARGS", "-DEXISTING=ON")

    result = subprocess.run(["sh", str(checkout / "scripts/termux.sh")], capture_output=True, text=True, check=True)

    assert result.stdout.splitlines() == [
        "sync",
        "--locked",
        "28",
        str(tmp_path / "custom cache"),
        f"-DEXISTING=ON -DPython3_SABI_LIBRARY={tmp_path}/usr/lib/libpython3.so -DCMAKE_MODULE_LINKER_FLAGS=-landroid",
    ]


def test_non_android_host_is_rejected(checkout: Path, tmp_path: Path) -> None:
    (tmp_path / "bin/uname").write_text("#!/bin/sh\nprintf 'GNU/Linux\\n'\n")
    result = subprocess.run(
        ["sh", str(checkout / "scripts/termux.sh"), "echo", "must not run"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Run this wrapper inside Termux." in result.stderr


def test_linker_keeps_arguments_and_python_dependency(checkout: Path, tmp_path: Path) -> None:
    cc = tmp_path / "bin/cc"
    cc.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 19\n')
    cc.chmod(0o755)
    result = subprocess.run(
        ["sh", str(checkout / "scripts/android-link-with-libpython3.sh"), "input with spaces.o", "-shared"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 19
    assert result.stdout.splitlines() == [
        "input with spaces.o",
        "-shared",
        "-Wl,--no-as-needed",
        "-lpython3",
        "-Wl,--as-needed",
    ]
