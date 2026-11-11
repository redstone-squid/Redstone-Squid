"""Durable inference inputs and stable candidates, independent of Discord delivery."""

import hashlib
from dataclasses import dataclass, replace
from typing import Literal, Protocol
from uuid import UUID, uuid5

import anyio

from squid.artifacts.application import ArtifactStore
from squid.builds.application.inference import BuildInferenceInput, BuildInferenceService, InlineImage
from squid.builds.domain import BuildDraft
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, ConflictError, JSONValue, NotFoundError
from squid.permissions.application import PermissionService
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT
from squid.submissions.domain.source_files import SubmissionSourceFile
from squid.unit_of_work import UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class InferenceCandidate:
    """A stable candidate identity whose original inferred facts are retained."""

    id: UUID
    run_id: UUID
    owner_account_id: int
    facts: BuildDraft
    source_files: tuple[SubmissionSourceFile, ...] = ()


@dataclass(frozen=True, slots=True)
class InferenceClaim:
    """Authority to finish one inference invocation, expiring with its database lease."""

    token: UUID | None
    candidates: tuple[InferenceCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class InferenceStatus:
    """Minimal public progress for one source-message bundle."""

    channel_id: int
    guild_id: int | None
    source_message_id: int
    state: str
    candidates: tuple[str, ...]


class InferenceRunRepository(Protocol):
    """Retain inputs before invocation and fence candidate writes by the active claim."""

    async def begin(
        self, run_id: UUID, owner: int, inputs: dict[str, JSONValue], *, capacity: int
    ) -> InferenceClaim: ...
    async def complete(
        self, run_id: UUID, token: UUID, drafts: tuple[BuildDraft, ...]
    ) -> tuple[InferenceCandidate, ...]: ...
    async def fail(self, run_id: UUID, token: UUID) -> None: ...
    async def get(self, run_id: UUID) -> tuple[int, tuple[InferenceCandidate, ...]] | None: ...
    async def retained(
        self, run_id: UUID
    ) -> tuple[int, dict[str, JSONValue], bool, tuple[InferenceCandidate, ...]] | None: ...
    async def public_status(self, run_id: UUID) -> InferenceStatus | None: ...
    async def list_active(self, owner: int | None) -> tuple[UUID, ...]: ...
    async def expired(self) -> tuple[tuple[UUID, int], ...]: ...
    async def delete_expired(self, run_id: UUID) -> None: ...


class InferenceRunCodec(Protocol):
    """Translate retained JSON at the infrastructure boundary."""

    def encode_bundle(self, bundle: BuildInferenceInput) -> dict[str, JSONValue]: ...
    def decode_bundle(self, value: JSONValue) -> BuildInferenceInput: ...
    def encode_source_files(self, source_files: tuple[SubmissionSourceFile, ...]) -> list[JSONValue]: ...
    def decode_source_files(self, value: JSONValue) -> tuple[SubmissionSourceFile, ...]: ...
    def decode_image(self, value: dict[str, JSONValue], data: bytes) -> InlineImage: ...


class SubmissionInferenceRuns:
    """Retain a model invocation's inputs and inferred build candidates.

    A run ID identifies the same input bundle across retries. Completed runs
    return their retained candidates, each with a stable ID, so reopening a
    submission does not call the model again or replace the user's draft edits.
    materialize_candidate turns a candidate into an editable submission draft.
    """

    def __init__(
        self,
        inference: BuildInferenceService,
        artifacts: ArtifactStore,
        codec: InferenceRunCodec,
        *,
        uow_factory: UnitOfWorkFactory[InferenceUnitOfWork],
        capacity: int = 1_000,
        permissions: PermissionService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._inference = inference
        self._artifacts = artifacts
        self._codec = codec
        self._capacity = capacity
        self._permissions = permissions

    async def infer(
        self,
        run_id: UUID,
        owner: int,
        bundle: BuildInferenceInput,
        *,
        model: str,
        reasoning_effort: str | None = None,
        source_files: tuple[SubmissionSourceFile, ...] = (),
        purpose: Literal["submission", "recalculation"] = "submission",
    ) -> tuple[InferenceCandidate, ...]:
        """Retain inputs, run inference, and save candidates under a worker claim.

        Completed runs replay their candidates. New work stores private images
        before calling the model. Upload, inference, and completion share a four-minute
        timeout once the claim is committed.
        Ordinary failures release the claim; cancellation leaves it to expire.
        """
        inputs: dict[str, JSONValue] = {
            "schema_revision": 1,
            "purpose": purpose,
            "source_files": self._codec.encode_source_files(source_files),
            "bundle": self._codec.encode_bundle(replace(bundle, images=())),
            "model": model,
            "reasoning_effort": reasoning_effort,
            "images": [
                {
                    "key": f"submission-inference/{run_id}/{index}",
                    "sha256": hashlib.sha256(image.data).hexdigest(),
                    "content_type": image.content_type,
                    "source_message_id": image.source_message_id,
                    "origin": image.origin,
                }
                for index, image in enumerate(bundle.images)
            ],
        }
        async with self._uow_factory() as uow:
            claim = await uow.inference_runs.begin(run_id, owner, inputs, capacity=self._capacity)
            await uow.commit()
        if claim.token is None:
            return claim.candidates
        try:
            with anyio.fail_after(240):
                for index, image in enumerate(bundle.images):
                    await self._artifacts.put(
                        f"submission-inference/{run_id}/{index}", image.data, content_type=image.content_type
                    )
                drafts = await self._inference.infer(bundle, model=model, reasoning_effort=reasoning_effort)
                async with self._uow_factory() as uow:
                    result = await uow.inference_runs.complete(run_id, claim.token, tuple(drafts))
                    await uow.commit()
                    return result
        except Exception:
            async with self._uow_factory() as uow:
                await uow.inference_runs.fail(run_id, claim.token)
                await uow.commit()
            raise

    async def resume(self, run_id: UUID, actor: Actor) -> tuple[InferenceCandidate, ...] | None:
        """Retry retained inputs, or return completed candidates without rereading the source message."""
        async with self._uow_factory() as uow:
            retained = await uow.inference_runs.retained(run_id)
            if retained is None:
                return None
            owner, inputs, completed, candidates = retained
            if inputs.get("purpose") != "submission":
                raise AuthorizationError
            if actor.account_id != owner and not (
                self._permissions is not None and await self._permissions.allows_in(uow, actor, BUILD_SUBMISSION_EDIT)
            ):
                raise AuthorizationError
            if completed:
                return candidates
        bundle = self._codec.decode_bundle(inputs["bundle"])
        descriptors = inputs.get("images", [])
        assert isinstance(descriptors, list)
        images: list[InlineImage] = []
        for index, descriptor in enumerate(descriptors):
            assert isinstance(descriptor, dict)
            data = await self._artifacts.get(f"submission-inference/{run_id}/{index}", max_bytes=16 * 1024 * 1024)
            if data is None or hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
                message = "A retained inference image is unavailable; retry after private storage is restored."
                raise ConflictError(message)
            images.append(self._codec.decode_image(descriptor, data))
        model = inputs["model"]
        reasoning_effort = inputs.get("reasoning_effort")
        assert isinstance(model, str)
        assert reasoning_effort is None or isinstance(reasoning_effort, str)
        return await self.infer(
            run_id,
            owner,
            replace(bundle, images=tuple(images)),
            model=model,
            reasoning_effort=reasoning_effort,
            source_files=self._codec.decode_source_files(inputs.get("source_files", [])),
        )

    async def get(self, run_id: UUID, actor: Actor) -> tuple[InferenceCandidate, ...]:
        """Reopen retained candidates under the current owner or staff authority."""
        async with self._uow_factory() as uow:
            result = await uow.inference_runs.get(run_id)
            if result is None:
                raise NotFoundError
            owner, candidates = result
            if actor.account_id != owner and not (
                self._permissions is not None and await self._permissions.allows_in(uow, actor, BUILD_SUBMISSION_EDIT)
            ):
                raise AuthorizationError
            return candidates

    async def list_active(self, actor: Actor, *, inbox: bool = False) -> tuple[UUID, ...]:
        """List retained runs for the actor, or for all accounts after a staff check."""
        async with self._uow_factory() as uow:
            if actor.account_id is None:
                raise AuthorizationError
            if inbox and not (
                self._permissions is not None and await self._permissions.allows_in(uow, actor, BUILD_SUBMISSION_EDIT)
            ):
                raise AuthorizationError
            return await uow.inference_runs.list_active(None if inbox else actor.account_id)

    async def public_status(self, run_id: UUID) -> InferenceStatus | None:
        """Read minimal status for the source-channel reconciler without exposing inferred facts."""
        async with self._uow_factory() as uow:
            return await uow.inference_runs.public_status(run_id)

    async def cleanup(self) -> None:
        """Delete expired private images before releasing their retained database inventory."""
        async with self._uow_factory() as uow:
            expired = await uow.inference_runs.expired()
        for run_id, image_count in expired:
            for index in range(image_count):
                await self._artifacts.delete(f"submission-inference/{run_id}/{index}")
            async with self._uow_factory() as uow:
                await uow.inference_runs.delete_expired(run_id)
                await uow.commit()


def candidate_id(run_id: UUID, index: int) -> UUID:
    """Derive identity from the retained run, never from mutable candidate content."""
    return uuid5(run_id, f"candidate:{index}")


def inference_busy() -> ConflictError:
    return ConflictError("This inference run is already processing; its retained result can be reopened later.")


class InferenceUnitOfWork(PermissionUnitOfWork, Protocol):
    @property
    def inference_runs(self) -> InferenceRunRepository: ...
