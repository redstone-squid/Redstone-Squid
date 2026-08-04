"""Resource-pack loading tests."""

import hashlib
from pathlib import Path

import pytest

from squid.schematics.errors import SchematicRenderUnavailableError
from squid.schematics.infrastructure.resource_pack import ResourcePackLoader


async def test_local_pack_is_verified_and_loaded_once(tmp_path: Path) -> None:
    path = tmp_path / "pack.zip"
    data = b"operator-owned-pack"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    loader = ResourcePackLoader(path=path, url=None, expected_sha256=digest, cache_dir=tmp_path / "cache")

    assert await loader.load() == (data, digest)
    path.write_bytes(b"changed-after-first-use")
    assert await loader.load() == (data, digest)


async def test_local_pack_hash_mismatch_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "pack.zip"
    path.write_bytes(b"wrong-pack")
    loader = ResourcePackLoader(path=path, url=None, expected_sha256="0" * 64, cache_dir=tmp_path / "cache")

    with pytest.raises(SchematicRenderUnavailableError):
        await loader.load()


async def test_verified_remote_pack_cache_prevents_a_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"previously-downloaded-pack"
    digest = hashlib.sha256(data).hexdigest()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / f"{digest}.zip").write_bytes(data)

    def fail_if_fetched(*_args: object, **_kwargs: object) -> None:
        pytest.fail("A verified cached pack must not be fetched again.")

    monkeypatch.setattr("squid.schematics.infrastructure.resource_pack.aiohttp.ClientSession", fail_if_fetched)
    loader = ResourcePackLoader(
        path=None,
        url="https://packs.example/vanilla.zip",
        expected_sha256=digest,
        cache_dir=cache_dir,
    )

    assert await loader.load() == (data, digest)
