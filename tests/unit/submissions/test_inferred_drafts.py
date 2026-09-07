"""Retained candidate conversion preserves identity, missing facts, and user corrections."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from whenever import Instant

from squid.builds.domain import BuildCategory, BuildDraft, SourceMessage
from squid.core.errors import ValidationError
from squid.submissions.application.drafts import StoredDraft, SubmissionDraftService
from squid.submissions.application.forms import SubmissionFormService, build_submission_manifest
from squid.submissions.application.inference_runs import InferenceCandidate
from squid.submissions.application.inferred_drafts import materialize_candidate
from squid.submissions.domain import DraftChange, DraftSnapshot, SubmissionOrigin


class Drafts:
    current: StoredDraft | None = None
    changes: int = 0

    async def create(self, **kwargs: Any) -> StoredDraft:
        if self.current is None:
            now = Instant.now()
            self.current = StoredDraft(
                DraftSnapshot(
                    kwargs["draft_id"], kwargs["owner_account_id"], "build_submission.v1", 1, kwargs["category"]
                ),
                SubmissionOrigin.DISCORD,
                now,
                now,
                now.add(days=7, days_assumed_24h_ok=True),
                inferred=kwargs["inferred"],
                source_messages=kwargs["source_messages"],
            )
        return self.current

    async def apply_change(self, identifier: object, owner: int, change: DraftChange, **kwargs: Any) -> None:
        assert self.current is not None
        self.current = replace(self.current, snapshot=self.current.snapshot.apply(change))
        self.changes += 1

    async def get_accessible(self, identifier: object, owner: int) -> StoredDraft:
        assert self.current is not None
        return self.current


class Forms:
    async def manifest(self, *, locale: str | None) -> object:
        return build_submission_manifest()

    async def options(self, *args: object, **kwargs: object) -> object:
        return SimpleNamespace(options=())


async def test_materialization_reuses_identity_without_overwriting_corrections() -> None:
    drafts = Drafts()
    source = SourceMessage(123)
    candidate = InferenceCandidate(
        uuid4(), uuid4(), 7, BuildDraft(category=BuildCategory.DOOR, door_width=3, source_messages=(source,))
    )
    first = await materialize_candidate(
        candidate, cast(SubmissionDraftService, drafts), cast(SubmissionFormService, Forms())
    )
    assert first.snapshot.id == candidate.id
    assert first.source_messages == (source,)
    assert first.inferred
    assert first.snapshot.answers["opening_width"] == 3
    assert "opening_height" not in first.snapshot.answers
    assert "source_version" not in first.snapshot.answers
    replay = await materialize_candidate(
        candidate, cast(SubmissionDraftService, drafts), cast(SubmissionFormService, Forms())
    )
    assert replay == first
    assert drafts.changes == 1


async def test_unknown_category_requires_explicit_selection() -> None:
    drafts = Drafts()
    candidate = InferenceCandidate(uuid4(), uuid4(), 7, BuildDraft())
    with pytest.raises(ValidationError):
        await materialize_candidate(
            candidate, cast(SubmissionDraftService, drafts), cast(SubmissionFormService, Forms())
        )
    assert drafts.current is None
    result = await materialize_candidate(
        candidate,
        cast(SubmissionDraftService, drafts),
        cast(SubmissionFormService, Forms()),
        category=BuildCategory.EXTENDER,
    )
    assert result.snapshot.category == "extender"
    assert candidate.facts.category is None
