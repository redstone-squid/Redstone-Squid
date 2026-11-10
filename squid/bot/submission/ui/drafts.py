"""Private manifest-driven editing of persisted submission drafts."""

from collections.abc import Mapping
from functools import partial
from typing import Any, cast, override
from uuid import UUID, uuid4

import squid_ui as sl
import squid_ui_discord as sd
from squid.core.errors import JSONValue, SquidError
from squid.runtime import BotServices
from squid.submissions.application import FinalizationJobSnapshot, StoredDraft
from squid.submissions.application.intake import SuppliedAttachment
from squid.submissions.domain import (
    ChoiceOption,
    DraftChange,
    DraftChangeKey,
    DraftStatus,
    FieldOperation,
    FieldOperationKind,
    FormField,
    FormManifest,
    FormSection,
    ValueKind,
)
from squid.submissions.domain.intake import IntakeStatus
from squid_reactivity import TopicHandle


class DraftEditorScreen(sd.Screen):
    """Edit a saved draft using its pinned form manifest.

    Each form saves its fields together through the draft service, which
    checks access and revision. Submission requests finalization; the same screen
    reloads attachment and job state to show progress or issues needing correction.
    """

    policy = sd.ResponseSpec(audience="personal", timeout=600)
    selected: str | None = sl.state(None)
    option_page: int = sl.state(0)
    notice: sl.TextLike = sl.state("")
    options: tuple[ChoiceOption, ...] = sl.state((), opaque=True)
    selected_attachment: str | None = sl.state(None)

    def __init__(
        self, services: BotServices, actor_id: int, draft: StoredDraft, manifest: FormManifest, *, changes: TopicHandle
    ) -> None:
        self.services = services
        self.actor_id = actor_id
        self._draft_id = draft.snapshot.id
        self._changes = changes
        self._seed: tuple[StoredDraft, tuple[SuppliedAttachment, ...], FinalizationJobSnapshot | None] | None = (
            draft,
            (),
            None,
        )
        self.manifest = manifest

    @property
    def fields(self) -> tuple[FormField, ...]:
        answers = self.manifest.apply_defaults(
            self.draft.snapshot.category, self.draft.snapshot.answers, origin=self.draft.origin
        )
        return tuple(
            field
            for field in self.manifest.fields_for(self.draft.snapshot.category)
            if field.is_visible(answers, self.draft.origin)
        )

    @property
    def field(self) -> FormField | None:
        return next((field for field in self.fields if field.id == self.selected), None)

    @property
    def groups(self) -> tuple[FormSection, ...]:
        """Group visible fields into modals; keep dynamic catalogues in paged pickers."""
        visible = {field.id for field in self.fields}
        sections = (*self.manifest.common_sections, *self.manifest.category(self.draft.snapshot.category).sections)
        groups: list[FormSection] = []
        for section in sections:
            fields = tuple(field for field in section.fields if field.id in visible and field.option_source is None)
            for offset in range(0, len(fields), 5):
                title = section.title if len(fields) <= 5 else f"{section.title} ({offset // 5 + 1})"
                groups.append(FormSection(f"{section.id}_{offset}", title, fields[offset : offset + 5]))
            groups.extend(
                FormSection(field.id, field.label, (field,))
                for field in section.fields
                if field.id in visible and field.option_source is not None
            )
        return tuple(groups)

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        state = self.projection.status
        if self._seed is None and not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status("Loading submission draft."),)
        draft = self.draft.snapshot
        status = draft.status.value.replace("_", " ")
        if self.result is not None and self.result.result is not None:
            status = f"Saved as build #{self.result.result.build_id}."
        field_labels = {field.id: field.label for field in self.manifest.fields_for(draft.category)}
        issues = "\n".join(
            f"{field_labels.get(issue.field_id, issue.field_id)}: {issue.reason.value.replace('_', ' ')}"
            for issue in self.draft.preparation_issues
        )
        editable = draft.status in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}
        errors = self.manifest.validate_answers(draft.category, draft.answers, origin=self.draft.origin)
        required = tuple(field for field in self.fields if field.required)
        missing = tuple(field for field in required if field.id in errors)
        progress = (
            "Required fields still needed: " + ", ".join(field.label for field in missing)
            if missing
            else "Required fields complete. Submit when you are ready; files and build details will be checked."
        )
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
            sl.paragraph(progress if editable else status),
        ]
        if self.notice:
            nodes.append(sl.paragraph(self.notice))
        if issues:
            nodes.append(sl.paragraph(issues))
        if editable:
            groups = self.groups
            answers = self.manifest.apply_defaults(draft.category, draft.answers, origin=self.draft.origin)
            summary: list[str] = []
            for group in groups:
                needed = [field.label for field in group.fields if field.required and field.id in errors]
                supplied = [
                    f"{field.label}: {_display(answers.get(field.id)).replace(chr(10), ', ')[:60]}"
                    for field in group.fields
                    if answers.get(field.id) is not None and answers.get(field.id) != []
                ]
                detail = "Needs " + ", ".join(needed) if needed else "; ".join(supplied) or "Optional"
                summary.append(f"**{group.title}** — {detail}")
            nodes.append(sl.paragraph("\n".join(summary)))
            nodes.extend(
                sl.action_controls(
                    *(
                        sl.action_control(group.title[:80], partial(self._edit_group, group.id), key=f"edit_{group.id}")
                        for group in groups[offset : offset + 5]
                    ),
                    key=f"groups_{offset}",
                )
                for offset in range(0, len(groups), 5)
            )
            if (field := self.field) is not None and self.options:
                value = answers.get(field.id)
                page = self.options[self.option_page * 25 : (self.option_page + 1) * 25]
                selected = value if isinstance(value, list) else [value]
                nodes.append(sl.paragraph(f"**{field.label}** ({'required' if field.required else 'optional'})"))
                nodes.append(
                    sl.choices(
                        *(sl.choice(option.label[:100], key=option.value) for option in page),
                        key="value",
                        selection=sl.controlled(
                            tuple(option.value for option in page if option.value in selected), self._select_value
                        ),
                        minimum=0 if not field.required or field.value_kind is ValueKind.STRING_LIST else 1,
                        maximum=min(25, len(page)) if field.value_kind is ValueKind.STRING_LIST else 1,
                    )
                )
                if len(self.options) > 25:
                    nodes.append(
                        sl.action_controls(
                            sl.action_control("Previous choices", self._previous_options, key="previous_options"),
                            sl.action_control("More choices", self._next_options, key="next_options"),
                            key="option_pages",
                        )
                    )
            nodes.append(
                sl.action_controls(
                    sl.action_control("Submit for review", self._submit, key="submit"),
                    sl.action_control("Refresh status", self._refresh, key="refresh"),
                    key="draft_actions",
                )
            )
        files = tuple(item for item in self.attachments if item.status is not IntakeStatus.DISCARDED)
        if editable and files:
            nodes.append(
                sl.paragraph(
                    "**Supplied files**\n" + "\n".join(f"{item.filename}: {item.status.value}" for item in files)
                )
            )
            nodes.append(
                sl.choices(
                    *(sl.choice(item.filename[:100], key=str(item.id)) for item in files),
                    key="attachment",
                    selection=sl.controlled(
                        (self.selected_attachment,) if self.selected_attachment is not None else (),
                        self._select_attachment,
                    ),
                )
            )
            if self.selected_attachment is not None:
                nodes.append(
                    sl.action_controls(
                        *(
                            [sl.action_control("Use supplied file", self._use_attachment, key="use_attachment")]
                            if any(
                                str(item.id) == self.selected_attachment
                                and item.source is not None
                                and item.status is IntakeStatus.PENDING
                                for item in files
                            )
                            else []
                        ),
                        sl.action_control("Retry with file", self._retry_attachment, key="retry_attachment"),
                        sl.action_control("Discard file", self._discard_attachment, key="discard_attachment"),
                        *(
                            [
                                sl.action_control(
                                    "Make primary schematic", self._primary_attachment, key="primary_attachment"
                                )
                            ]
                            if any(
                                str(item.id) == self.selected_attachment and item.kind == "schematic" for item in files
                            )
                            else []
                        ),
                        key="attachment_actions",
                    )
                )
        if not editable:
            nodes.append(
                sl.action_controls(sl.action_control("Refresh status", self._refresh, key="refresh"), key="status")
            )
        return (sl.section(sl.heading("Submit a build"), *nodes),)

    async def _edit_group(self, group_id: str, event: sl.PressEvent) -> None:
        group = next(group for group in self.groups if group.id == group_id)
        field = group.fields[0]
        if field.option_source is not None:
            self.selected = field.id
            self.option_page = 0
            self.options = (
                await self.services.submission_forms.options(
                    field.option_source, self.draft.snapshot.category, locale=None
                )
            ).options
            if not self.options:
                self.notice = f"No choices are currently available for {field.label}."
            return
        self.selected = None
        self.options = ()
        base_revision = self.draft.snapshot.revision
        answers = self.manifest.apply_defaults(
            self.draft.snapshot.category, self.draft.snapshot.answers, origin=self.draft.origin
        )

        async def submitted(event: sl.SubmitEvent) -> None:
            values: dict[str, JSONValue] = {}
            for field in group.fields:
                raw = event.values.get(field.id)
                if field.value_kind is ValueKind.STRING_LIST:
                    values[field.id] = [line.strip() for line in str(raw or "").splitlines() if line.strip()]
                else:
                    values[field.id] = cast(JSONValue, raw)
            await self._save_values(values, base_revision=base_revision)

        await event.present_form(
            sl.forms.FormSpec(
                group.title[:45], tuple(_form_entry(field, answers.get(field.id)) for field in group.fields)
            ),
            key=f"draft-{group.id}",
            on_submit=submitted,
        )

    async def _select_value(self, event: sl.ChoiceEvent) -> None:
        field = self.field
        if field is None:
            return
        value: JSONValue
        if field.value_kind is ValueKind.STRING_LIST:
            current = self.draft.snapshot.answers.get(field.id, [])
            visible = {option.value for option in self.options[self.option_page * 25 : (self.option_page + 1) * 25]}
            # A choice event replaces only this page; keep selections from the other pages.
            retained = [item for item in current if item not in visible] if isinstance(current, list) else []
            value = [*retained, *event.selected]
        elif field.value_kind is ValueKind.BOOLEAN:
            value = event.selected[0] == "true" if event.selected else None
        else:
            value = event.selected[0] if event.selected else None
        await self._save(field.id, value)

    async def _save(self, field_id: str, value: JSONValue, *, base_revision: int | None = None) -> None:
        await self._save_values({field_id: value}, base_revision=base_revision)

    async def _save_values(self, values: Mapping[str, JSONValue], *, base_revision: int | None = None) -> None:
        # Save a whole modal against the revision it opened with, without partial writes.
        change = DraftChange(
            self.draft.snapshot.revision if base_revision is None else base_revision,
            "discord-editor",
            DraftChangeKey(str(uuid4())),
            tuple(
                FieldOperation(
                    uuid4(), field_id, FieldOperationKind.UNSET if value is None else FieldOperationKind.SET, value
                )
                for field_id, value in values.items()
            ),
        )
        try:
            await self.services.submission_drafts.apply_change(
                self.draft.snapshot.id, self.actor_id, change, locale=None
            )
        except SquidError as error:
            self.notice = error.public_text()
        else:
            self.notice = "Saved."
        await self._reload()

    async def _select_attachment(self, event: sl.ChoiceEvent) -> None:
        self.selected_attachment = event.selected[0]

    async def _discard_attachment(self, event: sl.PressEvent) -> None:
        if self.selected_attachment is not None:
            await self.services.submission_intake.discard(
                self.draft.snapshot.id, self.actor_id, UUID(self.selected_attachment)
            )
            self.selected_attachment = None
            await self._reload()

    async def _use_attachment(self, event: sl.PressEvent) -> None:
        from squid.bot.submission.draft_intake import receive_retained_file

        if self.selected_attachment is None:
            return
        await event.acknowledge()
        try:
            await receive_retained_file(
                self.services, self.draft.snapshot.id, self.actor_id, UUID(self.selected_attachment)
            )
        except Exception:
            self.notice = "The supplied file could not be downloaded. Upload a replacement or discard it."
        await self._reload()

    async def _primary_attachment(self, event: sl.PressEvent) -> None:
        if self.selected_attachment is not None:
            await self.services.submission_schematics.select_primary(
                self.draft.snapshot.id, self.actor_id, UUID(self.selected_attachment)
            )
            await self._reload()

    async def _retry_attachment(self, event: sl.PressEvent) -> None:
        from squid_ui_discord.modal import FileField

        if self.selected_attachment is None:
            return
        original_id = UUID(self.selected_attachment)

        async def supplied(event: sl.SubmitEvent) -> None:
            from squid.bot.submission.draft_intake import retry_uploaded_file

            source = event.values.get("file")
            if not isinstance(source, sl.forms.UploadedFile):
                return
            try:
                new_id = await retry_uploaded_file(
                    self.services, self.draft.snapshot.id, self.actor_id, original_id, source
                )
            except Exception:
                self.notice = "The replacement file could not be registered. Retry or discard it."
            else:
                self.selected_attachment = str(new_id)
                self.notice = "Replacement file registered."
            await self._reload()

        await event.present_form(
            sl.forms.FormSpec(
                "Retry attachment", (FileField(key="file", label="Replacement file", minimum=1, maximum=1),)
            ),
            key="retry-file",
            on_submit=supplied,
        )

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def projection(self) -> tuple[StoredDraft, tuple[SuppliedAttachment, ...], FinalizationJobSnapshot | None]:
        """Refresh the private editor when durable submission reconciliation publishes a change."""
        self._changes.watch()
        seed, self._seed = self._seed, None
        return seed if seed is not None else await self._read()

    def _current(self) -> tuple[StoredDraft, tuple[SuppliedAttachment, ...], FinalizationJobSnapshot | None]:
        if self._seed is not None:
            return self._seed
        state = self.projection.status
        if isinstance(state, sl.resources.Ready):
            return state.value
        if state.previous is not None:
            return state.previous.value
        message = "This draft has not loaded yet."
        raise sl.resources.ResourceNotReadyError(message)

    @property
    def draft(self) -> StoredDraft:
        return self._current()[0]

    @property
    def attachments(self) -> tuple[SuppliedAttachment, ...]:
        return self._current()[1]

    @property
    def result(self) -> FinalizationJobSnapshot | None:
        return self._current()[2]

    async def _read(self) -> tuple[StoredDraft, tuple[SuppliedAttachment, ...], FinalizationJobSnapshot | None]:
        draft = await self.services.submission_drafts.get_accessible(self._draft_id, self.actor_id)
        attachments = await self.services.submission_intake.list(self._draft_id, self.actor_id)
        result = await self.services.submission_finalization.status(self._draft_id, self.actor_id)
        return draft, attachments, result if isinstance(result, FinalizationJobSnapshot) else None

    async def _reload(self) -> None:
        current = await self._read()
        if self._seed is not None:
            self._seed = current
        else:
            self.projection.replace(current)
        if not any(
            str(item.id) == self.selected_attachment and item.status is not IntakeStatus.DISCARDED
            for item in self.attachments
        ):
            self.selected_attachment = None

    async def _refresh(self, event: sl.PressEvent) -> None:
        await self._reload()

    async def _submit(self, event: sl.PressEvent) -> None:
        await event.acknowledge()
        await self.services.submission_finalization.submit(self.draft.snapshot.id, self.actor_id, locale=None)
        self.notice = ""
        await self._reload()

    async def _previous_options(self, event: sl.PressEvent) -> None:
        self.option_page = max(0, self.option_page - 1)

    async def _next_options(self, event: sl.PressEvent) -> None:
        self.option_page = min((len(self.options) - 1) // 25, self.option_page + 1)


def _form_entry(field: FormField, value: JSONValue) -> sl.forms.FormField[Any]:
    label = f"{field.label[:34]} ({'required' if field.required else 'optional'})"
    help_text = field.help_text or (field.label if len(field.label) > 34 else None)
    description = help_text[:100] if help_text else None
    if field.options or field.value_kind is ValueKind.BOOLEAN:
        options = (
            tuple(sl.forms.ChoiceOption(option.value, option.label[:100], option.value) for option in field.options)
            if field.options
            else (sl.forms.ChoiceOption("true", "Yes", value=True), sl.forms.ChoiceOption("false", "No", value=False))
        )
        return sl.forms.ChoiceField[JSONValue, Any](
            key=field.id, label=label, description=description, required=field.required, default=value, options=options
        )
    if field.value_kind in {ValueKind.INTEGER, ValueKind.GAME_TICKS}:
        return sl.forms.IntField[Any](
            key=field.id,
            label=label,
            description=description,
            required=field.required,
            default=cast(int | None, value),
            minimum=cast(int | None, field.constraints.minimum),
            maximum=cast(int | None, field.constraints.maximum),
        )
    if field.value_kind is ValueKind.NUMBER:
        return sl.forms.FloatField[Any](
            key=field.id,
            label=label,
            description=description,
            required=field.required,
            default=cast(float | None, value),
            minimum=field.constraints.minimum,
            maximum=field.constraints.maximum,
        )
    return sl.forms.TextAreaField[Any](
        key=field.id,
        label=label,
        description=description,
        required=field.required,
        maximum=4000 if field.repeatable else min(4000, field.constraints.max_length or 4000),
        default=_display(value),
        placeholder="One value per line" if field.repeatable else None,
    )


def _display(value: JSONValue) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


async def draft_editor(
    services: BotServices, actor_id: int, draft_id: UUID, *, changes: TopicHandle
) -> DraftEditorScreen:
    """Reopen the pinned manifest and current draft after a process or session restart."""
    draft = await services.submission_drafts.get_accessible(draft_id, actor_id)
    manifest = await services.submission_forms.manifest_revision(
        draft.snapshot.schema_id, draft.snapshot.schema_revision, locale=None
    )
    if manifest is None:
        message = "This draft's form revision is unavailable."
        raise ValueError(message)
    screen = DraftEditorScreen(services, actor_id, draft, manifest, changes=changes)
    await screen._reload()
    return screen
