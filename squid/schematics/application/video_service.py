"""Private video requests backed by the existing durable job and artifact lifecycle."""

import hashlib
from typing import Literal

from pydantic import BaseModel
from whenever import Instant

from squid.artifacts import ArtifactStore
from squid.core.errors import AuthorizationError, ConflictError, InfrastructureError, NotFoundError
from squid.schematics.application.attachments import StoredSchematic
from squid.schematics.application.jobs import SchematicJobService, SchematicJobSnapshot
from squid.schematics.application.ports import SchematicResourcePackProvider, SchematicStore
from squid.schematics.application.video import VideoRenderRequest
from squid.schematics.errors import SchematicSupportUnavailableError, SchematicTooLargeError


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
        store: SchematicStore,
        resources: SchematicResourcePackProvider | None,
        *,
        enabled: bool,
        max_blocks: int,
        max_volume: int,
        public_base_url: str | None = None,
    ) -> None:
        self._jobs = jobs
        self._artifacts = artifacts
        self._store = store
        self._resources = resources
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
        account_id: int,
        can_view_private: bool,
    ) -> VideoStatus:
        if not self.enabled or self._resources is None:
            raise SchematicSupportUnavailableError
        stored = await self._authorized_source(build_id, schematic_id, account_id, can_view_private)
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
                    "account_id": account_id,
                    "build_id": build_id,
                    "schematic_id": schematic_id,
                    "source_sha256": stored.file_sha256,
                    "renderer_version": "nucleation-0.12.2-video-1",
                },
            },
            keys,
        )
        return VideoStatus(id=job_id, build_id=build_id, schematic_id=schematic_id, status="queued")

    async def get_video(self, job_id: int, *, account_id: int, can_view_private: bool) -> VideoStatus:
        snapshot = await self._authorized_job(job_id, account_id, can_view_private)
        metadata = snapshot.params["video"]
        state: Literal["queued", "running", "succeeded", "failed", "expired"] = "queued"
        if snapshot.expires_at is not None and snapshot.expires_at <= Instant.now():
            state = "expired"
        elif snapshot.dead_at is not None:
            state = "failed"
        elif snapshot.completed_at is not None:
            state = "succeeded"
        elif snapshot.claimed_at is not None:
            state = "running"
        return VideoStatus(
            id=job_id,
            build_id=metadata["build_id"],
            schematic_id=metadata["schematic_id"],
            status=state,
            expires_at=snapshot.expires_at.format_iso() if snapshot.expires_at is not None else None,
            error=snapshot.error_kind if state == "failed" else None,
            input_candidates=tuple(tuple(position) for position in snapshot.error_context.get("input_candidates", ())),
        )

    async def open_video_content(self, job_id: int, *, account_id: int, can_view_private: bool) -> bytes:
        snapshot = await self._authorized_job(job_id, account_id, can_view_private)
        if snapshot.expires_at is not None and snapshot.expires_at <= Instant.now():
            raise NotFoundError
        if snapshot.completed_at is None or snapshot.result_object_key is None:
            raise ConflictError
        content = await self._artifacts.get(snapshot.result_object_key, max_bytes=64 * 1024 * 1024)
        if content is None:
            raise NotFoundError
        return content

    async def _authorized_job(self, job_id: int, account_id: int, can_view_private: bool) -> SchematicJobSnapshot:
        snapshot = await self._jobs.get(job_id)
        if snapshot is None or snapshot.operation != "render_video":
            raise NotFoundError
        metadata = snapshot.params.get("video", {})
        if metadata.get("account_id") != account_id:
            raise NotFoundError
        source = await self._authorized_source(
            metadata["build_id"], metadata["schematic_id"], account_id, can_view_private
        )
        if source.file_sha256 != metadata.get("source_sha256"):
            raise NotFoundError
        return snapshot

    async def _authorized_source(
        self,
        build_id: int,
        schematic_id: int,
        account_id: int,
        can_view_private: bool,
    ) -> StoredSchematic:
        stored = await self._store.get_for_build(build_id, schematic_id)
        if stored is None:
            raise NotFoundError
        if not (
            stored.publication.is_public_downloadable or stored.uploaded_by_account_id == account_id or can_view_private
        ):
            raise AuthorizationError
        return stored
