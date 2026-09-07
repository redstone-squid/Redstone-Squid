"""Durable inference inputs and stable candidates, independent of Discord delivery."""

import hashlib
from dataclasses import dataclass, replace
from typing import Literal, Protocol
from uuid import UUID, uuid5

import anyio
from pydantic import TypeAdapter

from squid.artifacts.application import ArtifactStore
from squid.builds.application.inference import BuildInferenceInput, BuildInferenceService
from squid.builds.domain import BuildDraft
from squid.core.errors import AuthorizationError, ConflictError, JSONValue, NotFoundError
from squid.permissions.application import PermissionService
from squid.permissions.domain import Subject
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT
from squid.submissions.domain.source_files import SubmissionSourceFile

_INPUT = TypeAdapter(BuildInferenceInput)
_DRAFT = TypeAdapter(BuildDraft)


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
    async def public_status(self, run_id: UUID) -> InferenceStatus | None: ...
    async def list_active(self, owner: int | None) -> tuple[UUID, ...]: ...
    async def expired(self) -> tuple[tuple[UUID, int], ...]: ...
    async def delete_expired(self, run_id: UUID) -> None: ...


class SubmissionInferenceRuns:
    """Persist exact model inputs before invocation and reuse retained candidates on replay."""

    def __init__(
        self,
        repository: InferenceRunRepository,
        inference: BuildInferenceService,
        artifacts: ArtifactStore,
        *,
        capacity: int = 1_000,
        permissions: PermissionService | None = None,
    ) -> None:
        self._repository = repository
        self._inference = inference
        self._artifacts = artifacts
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
        """Replay completed work; leave interrupted invocations reclaimable after their lease."""
        inputs: dict[str, JSONValue] = {
            "schema_revision": 1,
            "purpose": purpose,
            "source_files": TypeAdapter(tuple[SubmissionSourceFile, ...]).dump_python(source_files, mode="json"),
            "bundle": _INPUT.dump_python(replace(bundle, images=()), mode="json"),
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
        claim = await self._repository.begin(run_id, owner, inputs, capacity=self._capacity)
        if claim.token is None:
            return claim.candidates
        try:
            with anyio.fail_after(240):
                for index, image in enumerate(bundle.images):
                    await self._artifacts.put(
                        f"submission-inference/{run_id}/{index}", image.data, content_type=image.content_type
                    )
                drafts = await self._inference.infer(bundle, model=model, reasoning_effort=reasoning_effort)
                return await self._repository.complete(run_id, claim.token, tuple(drafts))
        except Exception:
            await self._repository.fail(run_id, claim.token)
            raise

    async def get(self, run_id: UUID, actor: Subject) -> tuple[InferenceCandidate, ...]:
        """Reopen retained candidates under the current owner or staff authority."""
        result = await self._repository.get(run_id)
        if result is None:
            raise NotFoundError
        owner, candidates = result
        if actor.account_id != owner and not (
            self._permissions is not None and await self._permissions.allows(actor, BUILD_SUBMISSION_EDIT)
        ):
            raise AuthorizationError
        return candidates

    async def list_active(self, actor: Subject, *, inbox: bool = False) -> tuple[UUID, ...]:
        if actor.account_id is None:
            raise AuthorizationError
        if inbox and not (
            self._permissions is not None and await self._permissions.allows(actor, BUILD_SUBMISSION_EDIT)
        ):
            raise AuthorizationError
        return await self._repository.list_active(None if inbox else actor.account_id)

    async def public_status(self, run_id: UUID) -> InferenceStatus | None:
        """Read minimal status for the source-channel reconciler without exposing inferred facts."""
        return await self._repository.public_status(run_id)

    async def cleanup(self) -> None:
        """Delete expired private images before releasing their retained database inventory."""
        for run_id, image_count in await self._repository.expired():
            for index in range(image_count):
                await self._artifacts.delete(f"submission-inference/{run_id}/{index}")
            await self._repository.delete_expired(run_id)


def candidate_id(run_id: UUID, index: int) -> UUID:
    """Derive identity from the retained run, never from mutable candidate content."""
    return uuid5(run_id, f"candidate:{index}")


def encode_facts(draft: BuildDraft) -> dict[str, JSONValue]:
    return _DRAFT.dump_python(draft, mode="json")


def decode_facts(facts: dict[str, JSONValue]) -> BuildDraft:
    return _DRAFT.validate_python(facts)


def inference_busy() -> ConflictError:
    return ConflictError("This inference run is already processing; its retained result can be reopened later.")
