"""Recovery reuses retained inference input and stable candidates."""

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, call
from uuid import UUID, uuid4

import pytest

from squid.artifacts.infrastructure import LocalArtifactStore
from squid.builds.application.inference import BuildInferenceInput, BuildInferenceService, InlineImage
from squid.builds.domain import BuildDraft
from squid.core.errors import AuthorizationError, ConflictError, JSONValue
from squid.permissions.domain import Subject
from squid.submissions.application.inference_runs import (
    InferenceCandidate,
    InferenceClaim,
    InferenceRunRepository,
    SubmissionInferenceRuns,
    candidate_id,
)
from squid.submissions.infrastructure.inference_codec import PydanticInferenceRunCodec


@dataclass
class RetainedRun:
    """One inference run, keeping inputs across failed and completed claims."""

    id: UUID = field(default_factory=uuid4)
    owner: int = 7
    inputs: dict[str, JSONValue] | None = None
    token: UUID | None = None
    completed: bool = False
    candidates: tuple[InferenceCandidate, ...] = ()

    async def begin(self, run_id: UUID, owner: int, inputs: dict[str, JSONValue], *, capacity: int) -> InferenceClaim:
        assert (run_id, owner) == (self.id, self.owner)
        assert capacity > 0
        assert self.inputs is None or self.inputs == inputs, "a retry must retain exactly the original inputs"
        self.inputs = deepcopy(inputs)
        if self.completed:
            return InferenceClaim(None, self.candidates)
        assert self.token is None, "the failed claim must be released before retrying"
        self.token = uuid4()
        return InferenceClaim(self.token)

    async def complete(
        self, run_id: UUID, token: UUID, drafts: tuple[BuildDraft, ...]
    ) -> tuple[InferenceCandidate, ...]:
        assert (run_id, token) == (self.id, self.token)
        self.candidates = tuple(
            InferenceCandidate(candidate_id(run_id, index), run_id, self.owner, draft)
            for index, draft in enumerate(drafts)
        )
        self.completed, self.token = True, None
        return self.candidates

    async def fail(self, run_id: UUID, token: UUID) -> None:
        assert (run_id, token) == (self.id, self.token)
        self.token = None

    async def retained(self, run_id: UUID) -> tuple[int, dict[str, JSONValue], bool, tuple[InferenceCandidate, ...]]:
        assert run_id == self.id
        assert self.inputs is not None
        return self.owner, deepcopy(self.inputs), self.completed, self.candidates


@dataclass
class Recovery:
    service: SubmissionInferenceRuns
    repository: RetainedRun
    artifacts: LocalArtifactStore
    inference: AsyncMock
    bundle: BuildInferenceInput

    @property
    def image_key(self) -> str:
        return f"submission-inference/{self.repository.id}/0"

    async def resume(self, *, account_id: int = 7) -> tuple[InferenceCandidate, ...] | None:
        return await self.service.resume(self.repository.id, Subject(account_id=account_id))


@pytest.fixture
async def recovery(tmp_path: Path) -> Recovery:
    repository = RetainedRun()
    artifacts = LocalArtifactStore(tmp_path)
    inference = AsyncMock(spec=BuildInferenceService)
    inference.infer.side_effect = [RuntimeError("model unavailable"), [BuildDraft(door_width=3)]]
    bundle = BuildInferenceInput.from_single_message(
        author_name="owner",
        content="original facts",
        message_id=1,
        author_id=2,
        channel_id=3,
        server_id=4,
        images=(InlineImage(b"private image", "image/png", 1, "attachment"),),
    )
    service = SubmissionInferenceRuns(
        cast(InferenceRunRepository, repository), inference, artifacts, PydanticInferenceRunCodec()
    )
    with pytest.raises(RuntimeError, match="model unavailable"):
        await service.infer(repository.id, repository.owner, bundle, model="original-model", reasoning_effort="low")
    assert not repository.completed
    assert repository.token is None
    return Recovery(service, repository, artifacts, inference, bundle)


async def test_retry_recovers_original_inputs_and_replays_candidates_without_private_images(recovery: Recovery) -> None:
    candidates = await recovery.resume()

    assert candidates is not None
    assert len(candidates) == 1
    assert candidates[0].facts.door_width == 3
    assert candidates[0].owner_account_id == recovery.repository.owner
    assert candidates[0].run_id == recovery.repository.id
    expected = call(recovery.bundle, model="original-model", reasoning_effort="low")
    assert recovery.inference.infer.await_args_list == [expected, expected]

    await recovery.artifacts.delete(recovery.image_key)
    assert await recovery.resume() == candidates
    assert recovery.inference.infer.await_count == 2
    with pytest.raises(AuthorizationError):
        await recovery.resume(account_id=8)


async def test_failed_run_checks_owner_and_image_integrity_before_invoking_model(recovery: Recovery) -> None:
    await recovery.artifacts.put(recovery.image_key, b"changed image", content_type="image/png")

    with pytest.raises(AuthorizationError):
        await recovery.resume(account_id=8)
    with pytest.raises(ConflictError, match="retained inference image is unavailable"):
        await recovery.resume()
    assert recovery.inference.infer.await_count == 1
    assert not recovery.repository.completed
    assert recovery.repository.token is None

    image = recovery.bundle.images[0]
    await recovery.artifacts.put(recovery.image_key, image.data, content_type=image.content_type)
    assert await recovery.resume()
    assert recovery.repository.completed
