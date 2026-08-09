"""Application-facing binary artifact storage contracts."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    """Trusted metadata returned by an artifact store."""

    byte_size: int
    sha256: str | None = None


class ArtifactStore(Protocol):
    """Bounded binary storage addressed by application-owned object keys."""

    async def put(self, key: str, data: bytes, *, content_type: str) -> ArtifactMetadata: ...

    async def get(self, key: str, *, max_bytes: int) -> bytes | None: ...

    async def stat(self, key: str) -> ArtifactMetadata | None: ...

    async def delete(self, key: str) -> None: ...

    async def aclose(self) -> None: ...
