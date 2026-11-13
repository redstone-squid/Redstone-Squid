"""Convert retained inference candidates into the shared editable submission form."""

from dataclasses import replace
from uuid import uuid4

from squid.builds.domain import BuildCategory
from squid.core.actors import Actor
from squid.core.errors import ValidationError
from squid.submissions.application.drafts import StoredDraft, SubmissionDraftService
from squid.submissions.application.forms import SubmissionFormService
from squid.submissions.application.inference_runs import InferenceCandidate
from squid.submissions.application.prefill import prefill_submission
from squid.submissions.domain import DraftChange, DraftChangeKey, FieldOperation, FieldOperationKind, SubmissionOrigin


async def materialize_candidate(
    candidate: InferenceCandidate,
    drafts: SubmissionDraftService,
    forms: SubmissionFormService,
    *,
    category: BuildCategory | None = None,
    actor: Actor,
) -> StoredDraft:
    """Open a draft using the candidate ID and prefill it only at revision zero.

    Existing edits are preserved. If inference did not identify a category, the
    caller must choose one before the draft can be created.
    """
    if candidate.facts.category is not None and category not in {None, candidate.facts.category}:
        message = "The candidate already has a category; its identity cannot be reused for a different category."
        raise ValidationError(message)
    selected = candidate.facts.category or category
    if selected is None:
        message = "Choose a category before opening this candidate as a draft."
        raise ValidationError(message)
    source = replace(candidate.facts, category=selected, ai_generated=True)
    prefill = await prefill_submission(source, forms, origin=SubmissionOrigin.DISCORD)
    assert prefill.category is not None
    manifest = await forms.manifest(locale=None)
    stored = await drafts.create(
        draft_id=candidate.id,
        owner_account_id=candidate.owner_account_id,
        category=prefill.category,
        origin=SubmissionOrigin.DISCORD,
        inferred=True,
        source_messages=source.source_messages,
        source_files=candidate.source_files,
        source_issues=prefill.unresolved,
        inference_run_id=candidate.run_id,
        client_capabilities=frozenset(
            field.required_capability
            for field in manifest.fields_for(prefill.category)
            if field.required_capability is not None
        ),
        locale=None,
    )
    if stored.snapshot.revision == 0:
        await drafts.apply_change(
            candidate.id,
            actor,
            DraftChange(
                0,
                "inference",
                DraftChangeKey(f"inference:{candidate.id}"),
                tuple(
                    FieldOperation(uuid4(), key, FieldOperationKind.SET, value)
                    for key, value in prefill.answers.items()
                ),
            ),
            locale=None,
        )
    return await drafts.get_accessible(candidate.id, actor)
