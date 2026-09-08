"""Recovery reuses retained inference input and stable candidates."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from squid.builds.application.inference import BuildInferenceInput
from squid.core.errors import AuthorizationError
from squid.permissions.domain import Subject
from squid.submissions.application.inference_runs import InferenceClaim, SubmissionInferenceRuns
from squid.submissions.infrastructure.inference_codec import PydanticInferenceRunCodec


async def test_retry_uses_saved_model_and_original_message_facts() -> None:
    run = uuid4()
    bundle = BuildInferenceInput.from_single_message(
        author_name="owner", content="original facts", message_id=1, author_id=2, channel_id=3, server_id=4
    )
    inputs = {
        "schema_revision": 1,
        "purpose": "submission",
        "bundle": TypeAdapter(BuildInferenceInput).dump_python(bundle, mode="json"),
        "images": [],
        "model": "original-model",
        "reasoning_effort": "low",
        "source_files": [],
    }
    repository = SimpleNamespace(
        retained=AsyncMock(return_value=(7, inputs, False, ())),
        begin=AsyncMock(return_value=InferenceClaim(uuid4())),
        complete=AsyncMock(return_value=()),
    )
    inference = SimpleNamespace(infer=AsyncMock(return_value=[]))
    service = SubmissionInferenceRuns(
        cast(Any, repository), cast(Any, inference), cast(Any, object()), PydanticInferenceRunCodec()
    )
    assert await service.resume(run, Subject(account_id=7)) == ()
    inference.infer.assert_awaited_once_with(bundle, model="original-model", reasoning_effort="low")
    assert repository.begin.await_args.args[2] == inputs


async def test_completed_run_does_not_invoke_model_or_read_private_images() -> None:
    repository = SimpleNamespace(retained=AsyncMock(return_value=(7, {"purpose": "submission"}, True, ())))
    service = SubmissionInferenceRuns(
        cast(Any, repository), cast(Any, object()), cast(Any, object()), PydanticInferenceRunCodec()
    )
    assert await service.resume(uuid4(), Subject(account_id=7)) == ()
    with pytest.raises(AuthorizationError):
        await service.resume(uuid4(), Subject(account_id=8))
