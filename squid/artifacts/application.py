"""Application-facing binary artifact storage contracts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    """What a store knows about one stored object; `sha256` is absent when the store did not read the body."""

    byte_size: int
    sha256: str | None = None


class ArtifactStore(Protocol):
    """Bounded binary storage addressed by application-owned object keys.

    `aclose` releases whatever connections the adapter holds; the store is unusable afterwards. Keys are relative
    POSIX paths: an absolute, empty or traversing key raises `ValueError`. Writes are atomic, so a reader sees
    either the previous object or the whole new one. Every `max_bytes` is a hard budget and must be positive.
    """

    async def put(self, key: str, data: bytes, *, content_type: str) -> ArtifactMetadata:
        """Store `data` under `key`, replacing anything already there, and return its size and digest."""
        ...

    async def put_path(
        self,
        key: str,
        source: Path,
        *,
        content_type: str,
        max_bytes: int,
    ) -> ArtifactMetadata:
        """Store a staged file without loading it into memory.

        Raises `ArtifactTooLargeError` if the file exceeds `max_bytes`, `ArtifactSourceChangedError` if it is
        modified mid-transfer, and `ValueError` if it is not a regular file.
        """
        ...

    async def get(self, key: str, *, max_bytes: int) -> bytes | None:
        """Read the whole object, or `None` if the key is unknown.

        Raises `ArtifactTooLargeError` rather than returning more than `max_bytes`.
        """
        ...

    async def get_path(
        self,
        key: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> ArtifactMetadata | None:
        """Write the object to a caller-owned path, or return `None` if the key is unknown.

        Raises `ArtifactTooLargeError` above `max_bytes`, and `ArtifactSourceChangedError` if the transfer does not
        match the size the store declared.
        """
        ...

    async def stat(self, key: str) -> ArtifactMetadata | None:
        """Metadata without the body, or `None` if the key is unknown."""
        ...

    async def delete(self, key: str) -> None:
        """Remove the object; deleting an unknown key succeeds."""
        ...

    async def aclose(self) -> None:
        """Release the adapter's connections; no other method may be called afterwards."""
        ...
