"""Application-facing binary artifact storage contracts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    """Size and optional SHA-256 hex digest of stored bytes.

    A missing digest means the store did not provide one, as with a local stat.
    A stored digest is metadata, not proof that a download has been verified.
    """

    byte_size: int
    sha256: str | None = None


class ArtifactStore(Protocol):
    """Store binary objects under keys chosen by the application.

    Keys are relative paths using forward slashes. Uploading to an existing key
    replaces its object. Methods with `max_bytes` require a positive byte limit
    and raise ArtifactTooLargeError if the transfer exceeds it. `put()` receives
    bytes already in memory and does not impose a size limit.

    The process that creates this store must call `aclose()` during shutdown.
    """

    async def put(self, key: str, data: bytes, *, content_type: str) -> ArtifactMetadata:
        """Store bytes and return their size and digest."""
        ...

    async def put_path(
        self,
        key: str,
        source: Path,
        *,
        content_type: str,
        max_bytes: int,
    ) -> ArtifactMetadata:
        """Copy a regular file into storage, checking for changes during the transfer."""
        ...

    async def get(self, key: str, *, max_bytes: int) -> bytes | None:
        """Read the object into memory, or return None if it is missing."""
        ...

    async def get_path(
        self,
        key: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> ArtifactMetadata | None:
        """Copy an object to disk, or return None if it is missing.

        Replace the destination only after the copy succeeds. The returned digest
        describes the bytes copied, rather than trusting the object's metadata.
        """
        ...

    async def stat(self, key: str) -> ArtifactMetadata | None:
        """Read stored metadata without downloading the object, or return None if missing."""
        ...

    async def delete(self, key: str) -> None:
        """Remove the object if it exists."""
        ...

    async def aclose(self) -> None:
        """Release connections and other resources owned by the store."""
        ...
