"""Private video requests backed by the existing durable job and artifact lifecycle."""

import hashlib
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel
from whenever import Instant

from squid.core.actors import ActingContext
from squid.core.artifacts import ArtifactStore
from squid.core.errors import ConflictError, InfrastructureError, NotFoundError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.core.permissions.domain.catalogue import BUILD_SCHEMATIC_VIEW_PRIVATE
from squid.core.schematics.application.attachments import StoredSchematic
from squid.core.schematics.application.jobs import SchematicJobService, SchematicJobSnapshot
from squid.core.schematics.application.ports import SchematicRepository, SchematicResourcePackProvider
from squid.core.schematics.application.video import VideoRenderRequest
from squid.core.schematics.errors import SchematicSupportUnavailableError, SchematicTooLargeError

if TYPE_CHECKING:
    from squid.core.permissions.application import PermissionService


class VideoStatus(BaseModel):
    id: int
    build_id: int
    schematic_id: int
    status: Literal["queued", "running", "succeeded", "failed", "expired"]
    expires_at: str | None = None
    error: str | None = None
    input_candidates: tuple[tuple[int, int, int], ...] = ()


class SchematicVideoService:
    """Pin inputs at submission and authorize every result read against current source access.

    Video metadata lives in the durable job's parameters so ownership and the native job
    are committed together. Artifact expiry and claim fencing are owned by SchematicJobService.
    """

    def __init__(
        self,
        jobs: SchematicJobService,
        artifacts: ArtifactStore,
        store: SchematicRepository,
        resources: SchematicResourcePackProvider | None,
        *,
        permissions: PermissionService,
        enabled: bool,
        max_blocks: int,
        max_volume: int,
        public_base_url: str | None = None,
    ) -> None:
        self._jobs = jobs
        self._artifacts = artifacts
        self._store = store
        self._resources = resources
        self._permissions = permissions
        self.enabled = enabled
        self._max_blocks = max_blocks
        self._max_volume = max_volume
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None

    async def submit_video(
        self,
        build_id: int,
        schematic_id: int,
        request: VideoRenderRequest,
        *,
        context: ActingContext,
    ) -> VideoStatus:
        with context.scope():
            if not self.enabled or self._resources is None:
                raise SchematicSupportUnavailableError
            stored = await self._authorized_source(build_id, schematic_id, context)
            metrics = stored.analysis.metrics
            for actual, limit, measure in (
                (metrics.block_count, self._max_blocks, "video block count"),
                (metrics.bounding_volume, self._max_volume, "video bounding volume"),
            ):
                if actual > limit:
                    raise SchematicTooLargeError(actual=actual, limit=limit, measure=measure)
            source = await self._store.get_file(stored.file_sha256)
            if source is None:
                raise NotFoundError
            pack = await self._resources.load()
            keys: list[str] = []
            for data in (source, pack.data):
                digest = hashlib.sha256(data).hexdigest()
                key = f"schematics/{digest[:2]}/{digest}"
                receipt = await self._artifacts.put(key, data, content_type="application/octet-stream")
                if receipt.byte_size != len(data) or receipt.sha256 not in (None, digest):
                    msg = "Object storage did not confirm a staged video input."
                    raise InfrastructureError(msg, resource="schematic-video")
                keys.append(key)
            job_id = await self._jobs.submit(
                "render_video",
                {
                    "request": request.model_dump(mode="json"),
                    "resource_pack": {"sha256": pack.sha256, "media_type": pack.media_type},
                    "video": {
                        "account_id": context.effective.require_account_id(),
                        "build_id": build_id,
                        "schematic_id": schematic_id,
                        "source_sha256": stored.file_sha256,
                        "renderer_version": "nucleation-0.12.2-video-1",
                    },
                },
                keys,
                context=context,
            )
            return VideoStatus(id=job_id, build_id=build_id, schematic_id=schematic_id, status="queued")

    async def get_video(self, job_id: int, *, context: ActingContext) -> VideoStatus:
        with context.scope():
            return _status(await self._authorized_job(job_id, context))

    async def list_videos(
        self,
        *,
        schematic_id: int | None = None,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        context: ActingContext,
    ) -> Page[VideoStatus]:
        """Page the caller's own videos newest first, optionally for one attachment.

        Videos of a file the attachment no longer holds are left out, as `get_video` refuses them.
        Publication is checked when the caller opens a video, not here.
        """
        with context.scope():
            page = await self._jobs.list_videos(
                context.effective.require_account_id(),
                schematic_id=schematic_id,
                selector=selector,
                page_size=page_size,
                context=context,
            )
            return Page(tuple(_status(job) for job in page.items), page.total, page.next, page.prev)

    async def open_video_content(self, job_id: int, *, context: ActingContext) -> bytes:
        with context.scope():
            snapshot = await self._authorized_job(job_id, context)
            if snapshot.expires_at is not None and snapshot.expires_at <= Instant.now():
                raise NotFoundError
            if snapshot.completed_at is None or snapshot.result_object_key is None:
                raise ConflictError
            content = await self._artifacts.get(snapshot.result_object_key, max_bytes=64 * 1024 * 1024)
            if content is None:
                raise NotFoundError
            return content

    async def _authorized_job(self, job_id: int, context: ActingContext) -> SchematicJobSnapshot:
        account_id = context.effective.require_account_id()
        snapshot = await self._jobs.get(job_id, context=context)
        if snapshot is None or snapshot.operation != "render_video":
            raise NotFoundError
        metadata = snapshot.params.get("video", {})
        if metadata.get("account_id") != account_id:
            raise NotFoundError
        source = await self._authorized_source(metadata["build_id"], metadata["schematic_id"], context)
        if source.file_sha256 != metadata.get("source_sha256"):
            raise NotFoundError
        return snapshot

    async def _authorized_source(
        self,
        build_id: int,
        schematic_id: int,
        context: ActingContext,
    ) -> StoredSchematic:
        account_id = context.effective.require_account_id()
        stored = await self._store.get_for_build(build_id, schematic_id)
        if stored is None:
            raise NotFoundError
        if not stored.publication.is_public_downloadable and stored.uploaded_by_account_id != account_id:
            await self._permissions.require(context.effective, BUILD_SCHEMATIC_VIEW_PRIVATE)
        return stored


def _status(job: SchematicJobSnapshot) -> VideoStatus:
    metadata = job.params["video"]
    state: Literal["queued", "running", "succeeded", "failed", "expired"] = "queued"
    if job.expires_at is not None and job.expires_at <= Instant.now():
        state = "expired"
    elif job.dead_at is not None:
        state = "failed"
    elif job.completed_at is not None:
        state = "succeeded"
    elif job.claimed_at is not None:
        state = "running"
    return VideoStatus(
        id=job.id,
        build_id=metadata["build_id"],
        schematic_id=metadata["schematic_id"],
        status=state,
        expires_at=job.expires_at.format_iso() if job.expires_at is not None else None,
        error=job.error_kind if state == "failed" else None,
        input_candidates=tuple(tuple(position) for position in job.error_context.get("input_candidates", ())),
    )
