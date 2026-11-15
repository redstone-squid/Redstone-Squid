"""Retained candidates keep message-owned files separate and ambiguous files shared."""

from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from whenever import Instant

from squid.builds.domain import BuildCategory, BuildDraft
from squid.core.errors import JSONValue
from squid.messages.domain import MessageSnapshot
from squid.submissions.domain.source_files import SubmissionSourceFile
from squid.submissions.infrastructure.inference_codec import PydanticInferenceRunCodec
from squid.submissions.infrastructure.inference_payloads import BuildDraftPayload
from squid.submissions.infrastructure.inference_runs import _candidates
from squid.submissions.models.inference import SubmissionInferenceRun


def test_legacy_source_files_reencode_without_changing_retained_input() -> None:
    identity = uuid4()
    legacy: list[JSONValue] = [
        {
            "id": str(identity),
            "filename": "image.png",
            "content_type": None,
            "url": "https://example.com/a.png",
            "size": 4,
        }
    ]
    codec = PydanticInferenceRunCodec()

    assert codec.encode_source_files(codec.decode_source_files(legacy)) == legacy


def test_source_message_ownership_survives_retained_input_roundtrip() -> None:
    source = SubmissionSourceFile(uuid4(), "image.png", "image/png", "https://example.com/image.png", 4, 10)
    codec = PydanticInferenceRunCodec()

    assert codec.decode_source_files(codec.encode_source_files((source,))) == (source,)


@pytest.mark.parametrize("origin", [None, 10, 20, 30])
def test_candidate_files_follow_primary_message_ownership(origin: int | None) -> None:
    source = SubmissionSourceFile(uuid4(), "image.png", "image/png", "https://example.com/image.png", 4, origin)
    drafts = (
        BuildDraft(category=BuildCategory.DOOR, source_messages=(MessageSnapshot(10),)),
        BuildDraft(category=BuildCategory.DOOR, source_messages=(MessageSnapshot(20),)),
    )
    encoded = TypeAdapter(SubmissionSourceFile).dump_python(source, mode="json")
    if origin is None:
        # Runs retained before message ownership was recorded must still replay.
        del encoded["source_message_id"]
    inputs: dict[str, JSONValue] = {"source_files": [encoded]}
    adapter = TypeAdapter(BuildDraftPayload)
    row = SubmissionInferenceRun(
        id=uuid4(),
        owner_account_id=7,
        inputs=inputs,
        state="completed",
        expires_at=Instant.now().add(days=1, days_assumed_24h_ok=True),
        claim_token=None,
        claim_expires_at=None,
        candidates=[adapter.dump_python(BuildDraftPayload.from_draft(draft), mode="json") for draft in drafts],
    )

    candidates = _candidates(row)

    assert candidates[0].source_files == ((source,) if origin in {None, 10, 30} else ())
    assert candidates[1].source_files == ((source,) if origin in {None, 20, 30} else ())
    assert _candidates(row) == candidates


def test_two_builds_from_one_message_keep_its_files_shared() -> None:
    source = SubmissionSourceFile(uuid4(), "image.png", "image/png", "https://example.com/image.png", 4, 10)
    adapter = TypeAdapter(BuildDraftPayload)
    facts = adapter.dump_python(
        BuildDraftPayload.from_draft(BuildDraft(category=BuildCategory.DOOR, source_messages=(MessageSnapshot(10),))),
        mode="json",
    )
    row = SubmissionInferenceRun(
        id=uuid4(),
        owner_account_id=7,
        inputs={"source_files": TypeAdapter(list[SubmissionSourceFile]).dump_python([source], mode="json")},
        state="completed",
        expires_at=Instant.now().add(days=1, days_assumed_24h_ok=True),
        claim_token=None,
        claim_expires_at=None,
        candidates=[facts, facts],
    )

    assert [candidate.source_files for candidate in _candidates(row)] == [(source,), (source,)]
