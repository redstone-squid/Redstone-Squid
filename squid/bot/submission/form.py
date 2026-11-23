"""Create or resume a submission draft from supplied slash-command options."""

from collections.abc import Sequence
from functools import partial
from uuid import NAMESPACE_URL, uuid4, uuid5

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.accounts.consent import ensure_consented_account
from squid.bot.routes import AppRequest
from squid.bot.submission.build_card import render_build_card
from squid.bot.submission.draft_intake import receive_attachments
from squid.bot.submission.input import optional_text, split_values
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.submission.ui.drafts import DraftEditorScreen, field_names
from squid.bot.ui import error_node
from squid.core.builds.domain import BuildCategory, BuildInput, DoorDraftDetails, DoorOrientationLiteral
from squid.core.builds.domain.models import BuildCategoryLiteral
from squid.core.submissions.application.credit_text import parse_credit_lines
from squid.core.submissions.application.prefill import prefill_submission
from squid.core.submissions.domain import (
    DraftChange,
    DraftChangeKey,
    FieldOperation,
    FieldOperationKind,
    FormManifest,
    SubmissionOrigin,
)
from squid_i18n import md_tr, tr
from squid_ui.ext.discord.target_types import ComponentsV2Target


async def open_submission_form(
    request: AppRequest,
    *,
    category: BuildCategoryLiteral = "Door",
    name: str | None = None,
    door_size: str | None = None,
    door_type: DoorOrientationLiteral | None = None,
    build_size: str | None = None,
    creators: str | None = None,
    versions: str | None = None,
    patterns: str | None = None,
    restrictions: str | None = None,
    description: str | None = None,
    file: discord.Attachment | None = None,
    file_2: discord.Attachment | None = None,
    file_3: discord.Attachment | None = None,
    file_4: discord.Attachment | None = None,
) -> DraftEditorScreen | sl.LayoutNode[ComponentsV2Target, sd.Request] | None:
    """Submit a build. Every option is optional; fill in the rest in the form that opens."""
    # Obtain consent before creating a draft or downloading attachments.
    uploader_account_id = await ensure_consented_account(request, request.client.services.accounts)
    if uploader_account_id is None:
        return None

    if category != "Door" and (door_size is not None or door_type is not None):
        return error_node(
            tr(t"submit-inapplicable-options = Options do not apply"),
            tr(t"submit-door-options-only = Opening size and orientation can only be supplied for doors."),
        )
    if category not in {"Door", "Extender"} and patterns is not None:
        return error_node(
            tr(t"submit-inapplicable-options = Options do not apply"),
            tr(t"submit-pattern-options-only = Patterns can only be supplied for doors and extenders."),
        )
    category_code = category.lower()
    draft = BuildInput(
        ai_generated=False,
        category=BuildCategory(category),
        display_name=optional_text(name) if name is not None else None,
    )
    if door_size is not None:
        try:
            assert isinstance(draft.details, DoorDraftDetails)
            draft.details.door_dimensions = parse_hallway_dimensions(door_size)
        except ValueError:
            return error_node(
                tr(t"bot-submission-submit-check-door-size = Check the opening size"),
                md_tr(
                    t"submit-door-size-format = Write it as width x height x depth, such as `2x2x1` or `2x2`.",
                ),
            )
    if build_size is not None:
        try:
            draft.dimensions = parse_dimensions(build_size)
        except ValueError:
            return error_node(
                tr(t"bot-submission-submit-check-build-size = Check the build dimensions"),
                md_tr(
                    t"submit-build-size-format = Write it as width x height x depth, such as `5x7x4`.",
                ),
            )
    if door_type is not None:
        assert isinstance(draft.details, DoorDraftDetails)
        draft.details.door_orientation = door_type
    if patterns is not None:
        draft.patterns = split_values(patterns)
    if versions is not None:
        draft.version_spec = optional_text(versions)
    if creators is not None:
        draft.credits = parse_credit_lines("\n".join(split_values(creators)))
    if restrictions is not None:
        await request.client.services.builds.classify_restrictions(draft, split_values(restrictions))
    if description is not None and (parsed_description := optional_text(description)) is not None:
        draft.description = parsed_description

    services = request.client.services
    manifest = await services.submission_forms.manifest(locale=None)
    # Derive the draft ID from the interaction so a retried command reopens the same draft.
    draft_id = (
        uuid5(NAMESPACE_URL, f"discord-submission:{request.interaction.id}")
        if request.interaction is not None
        else uuid4()
    )
    stored = await services.submission_drafts.create(
        draft_id=draft_id,
        owner_account_id=uploader_account_id,
        category=category_code,
        origin=SubmissionOrigin.DISCORD,
        client_capabilities=frozenset(
            field.required_capability
            for field in manifest.fields_for(category_code)
            if field.required_capability is not None
        ),
        locale=None,
    )
    unresolved: tuple[str, ...] = ()
    actor = await request.client.actors.resolve(request, refresh=True)
    if stored.snapshot.revision == 0:
        prefill = await prefill_submission(draft, services.submission_forms, origin=SubmissionOrigin.DISCORD)
        unresolved = prefill.unresolved
        await services.submission_drafts.apply_change(
            draft_id,
            actor,
            DraftChange(
                0,
                "discord-intake",
                DraftChangeKey(f"initial:{draft_id}"),
                tuple(
                    FieldOperation(uuid4(), key, FieldOperationKind.SET, value)
                    for key, value in prefill.answers.items()
                ),
            ),
            locale=None,
        )
    await receive_attachments(
        services,
        draft_id,
        actor,
        tuple(attachment for attachment in (file, file_2, file_3, file_4) if attachment is not None),
    )
    return await DraftEditorScreen.load(
        services,
        draft_id,
        changes=request.client.topic_bus.topic("submission_draft", str(draft_id)),
        resolve_actor=partial(request.client.actors.resolve, request, refresh=True),
        render_card=lambda build: render_build_card(
            build,
            request.client.media_previews,
            schematic_summaries=request.client.services.schematic_summaries,
            published_media=request.client.services.published_build_media,
        ),
        notice=_unused_values_notice(unresolved, manifest, category_code),
    )


def _unused_values_notice(field_ids: Sequence[str], manifest: FormManifest, category: str) -> sl.TextLike:
    """Name the command options whose values the form could not accept, or return empty text.

    Prefill drops values the form rejects, such as a "5 wide" door whose height and depth are
    "not applicable", so the submitter can fill those fields in again.
    """
    if not field_ids:
        return ""
    fields = field_names(manifest, category, field_ids)
    return md_tr(
        t"draft-editor-unused-options = Some values from the command could not be used: {fields}. Fill them in below.",
    )
