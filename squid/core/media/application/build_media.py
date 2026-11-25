"""Read the ready media outputs attached to approved builds."""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from squid.core.actors import ActingContext
from squid.core.artifacts import ArtifactStore
from squid.core.media.domain.models import IMAGE_CONTENT_TYPES
from squid.core.media.models import MediaArtifactRecord
from squid.core.unit_of_work import UnitOfWork, UnitOfWorkFactory


class PublishedMediaRepository(Protocol):
    async def published_outputs(self, build_id: int, *, upload_id: UUID | None) -> list[MediaArtifactRecord]: ...


class PublishedMediaUnitOfWork(UnitOfWork, Protocol):
    @property
    def media_jobs(self) -> PublishedMediaRepository: ...


@dataclass(frozen=True, slots=True)
class PublishedMediaItem:
    kind: Literal["image", "video"]
    url: str
    width: int | None
    height: int | None


class PublishedBuildMedia:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory[PublishedMediaUnitOfWork],
        artifacts: ArtifactStore,
        *,
        public_base_url: str,
    ) -> None:
        self._uow_factory = uow_factory
        self.artifacts = artifacts
        self._public_base_url = public_base_url.rstrip("/")

    async def outputs(
        self, build_id: int, *, context: ActingContext, upload_id: UUID | None = None
    ) -> list[MediaArtifactRecord]:
        """Return outputs retained by a receipt for this build's own submission."""
        with context.scope():
            async with self._uow_factory() as uow:
                return await uow.media_jobs.published_outputs(build_id, upload_id=upload_id)

    async def gallery(self, build_id: int, *, context: ActingContext) -> list[PublishedMediaItem]:
        """Return unique image and video URLs in retained output order."""
        with context.scope():
            items: list[PublishedMediaItem] = []
            seen: set[str] = set()
            for artifact in await self.outputs(build_id, context=context):
                if artifact.sha256 in seen:
                    continue
                seen.add(artifact.sha256)
                url = f"{self._public_base_url}/v1/builds/{build_id}/media/{artifact.upload_id}/content"
                items.append(
                    PublishedMediaItem(
                        "image" if artifact.content_type in IMAGE_CONTENT_TYPES else "video",
                        url,
                        artifact.width,
                        artifact.height,
                    )
                )
            return items

    async def urls(self, build_id: int, *, context: ActingContext) -> tuple[list[str], list[str]]:
        items = await self.gallery(build_id, context=context)
        return [item.url for item in items if item.kind == "image"], [
            item.url for item in items if item.kind == "video"
        ]
