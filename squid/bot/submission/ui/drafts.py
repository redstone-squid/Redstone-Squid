"""Private manifest-driven editing of persisted submission drafts."""

from typing import Any, cast
from uuid import UUID, uuid4

import squid_ui as sl
import squid_ui_discord as sd
from squid.core.errors import JSONValue, SquidError
from squid.runtime import BotServices
from squid.submissions.application import FinalizationJobSnapshot, StoredDraft
from squid.submissions.domain import (
    ChoiceOption,
    DraftChange,
    DraftChangeKey,
    DraftStatus,
    FieldOperation,
    FieldOperationKind,
    FormField,
    FormManifest,
    ValueKind,
)


class DraftEditorScreen(sd.Screen):
    """A private editor that reloads persisted facts and reauthorizes each write."""

    audience = "personal"
    timeout = 600
    page: int = sl.state(0)
    selected: str | None = sl.state(None)
    option_page: int = sl.state(0)
    notice: str = sl.state("")
    draft: StoredDraft = sl.state(opaque=True)
    options: tuple[ChoiceOption, ...] = sl.state((), opaque=True)
    result: FinalizationJobSnapshot | None = sl.state(None, opaque=True)

    def __init__(self, services: BotServices, actor_id: int, draft: StoredDraft, manifest: FormManifest) -> None:
        self.services = services
        self.actor_id = actor_id
        self.draft = draft
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

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        draft = self.draft.snapshot
        status = draft.status.value.replace("_", " ")
        if self.result is not None and self.result.result is not None:
            status = f"Saved as build #{self.result.result.build_id}."
        issues = "\n".join(
            f"{issue.field_id}: {issue.reason.value.replace('_', ' ')}" for issue in self.draft.preparation_issues
        )
        editable = draft.status in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}
        fields = self.fields[self.page * 20 : (self.page + 1) * 20]
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.section(
                sl.heading("Submission draft"),
                sl.paragraph(status),
                sl.note(str(draft.id)),
                sl.paragraph(self.notice or issues or "Changes are saved to this draft."),
            ),
        ]
        if editable and fields:
            nodes.append(
                sl.choices(
                    *(sl.choice(field.label[:100], key=field.id) for field in fields),
                    key="field",
                    selection=sl.controlled(
                        (self.selected,)
                        if self.selected is not None and self.selected in {f.id for f in fields}
                        else (),
                        self._select_field,
                    ),
                )
            )
            if (field := self.field) is not None:
                value = draft.answers.get(field.id, field.default)
                nodes.append(
                    sl.section(sl.heading(field.label), sl.paragraph(_display(value)[:1500] or "Not supplied"))
                )
                if self.options:
                    page = self.options[self.option_page * 25 : (self.option_page + 1) * 25]
                    selected = (
                        value
                        if isinstance(value, list)
                        else ([str(value).lower()] if isinstance(value, bool) else [value])
                    )
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
                else:
                    nodes.append(
                        sl.action_controls(
                            sl.action_control("Edit value", self._edit_value, key="edit_value"), key="edit"
                        )
                    )
            nodes.append(
                sl.action_controls(
                    sl.action_control("Previous fields", self._previous, key="previous"),
                    sl.action_control("More fields", self._next, key="next"),
                    sl.action_control("Submit for review", self._submit, key="submit"),
                    key="draft_actions",
                )
            )
        nodes.append(
            sl.action_controls(sl.action_control("Refresh status", self._refresh, key="refresh"), key="status")
        )
        return tuple(nodes)

    async def _select_field(self, event: sl.ChoiceEvent) -> None:
        self.selected = event.selected[0]
        self.option_page = 0
        field = self.field
        if field is None:
            return
        if field.value_kind is ValueKind.BOOLEAN:
            self.options = (ChoiceOption("true", "Yes"), ChoiceOption("false", "No"))
        elif field.option_source is not None:
            self.options = (
                await self.services.submission_forms.options(
                    field.option_source, self.draft.snapshot.category, locale=None
                )
            ).options
        else:
            self.options = field.options

    async def _select_value(self, event: sl.ChoiceEvent) -> None:
        field = self.field
        if field is None:
            return
        value: JSONValue
        if field.value_kind is ValueKind.STRING_LIST:
            current = self.draft.snapshot.answers.get(field.id, [])
            visible = {option.value for option in self.options[self.option_page * 25 : (self.option_page + 1) * 25]}
            retained = [item for item in current if item not in visible] if isinstance(current, list) else []
            value = [*retained, *event.selected]
        elif field.value_kind is ValueKind.BOOLEAN:
            value = event.selected[0] == "true" if event.selected else None
        else:
            value = event.selected[0] if event.selected else None
        await self._save(field.id, value)

    async def _edit_value(self, event: sl.PressEvent) -> None:
        field = self.field
        if field is None:
            return
        field_id = field.id
        base_revision = self.draft.snapshot.revision
        value = self.draft.snapshot.answers.get(field_id, field.default)

        async def submitted(event: sl.SubmitEvent) -> None:
            raw = event.values.get("value")
            if field.value_kind is ValueKind.STRING_LIST:
                parsed: JSONValue = [line.strip() for line in str(raw or "").splitlines() if line.strip()]
            else:
                parsed = cast(JSONValue, raw)
            await self._save(field_id, parsed, base_revision=base_revision)

        entry: sl.forms.FormField[Any]
        if field.value_kind in {ValueKind.INTEGER, ValueKind.GAME_TICKS}:
            entry = sl.forms.IntField(
                key="value", label=field.label[:45], required=False, default=cast(int | None, value)
            )
        elif field.value_kind is ValueKind.NUMBER:
            entry = sl.forms.FloatField(
                key="value", label=field.label[:45], required=False, default=cast(float | None, value)
            )
        else:
            entry = sl.forms.TextAreaField(
                key="value",
                label=field.label[:45],
                required=False,
                maximum=4000,
                default=_display(value),
                placeholder="One value per line" if field.repeatable else None,
            )
        await event.present_form(
            sl.forms.FormSpec(field.label[:45], (entry,)), key=f"draft-{field_id}", on_submit=submitted
        )

    async def _save(self, field_id: str, value: JSONValue, *, base_revision: int | None = None) -> None:
        change = DraftChange(
            self.draft.snapshot.revision if base_revision is None else base_revision,
            "discord-editor",
            DraftChangeKey(str(uuid4())),
            (
                FieldOperation(
                    uuid4(), field_id, FieldOperationKind.UNSET if value is None else FieldOperationKind.SET, value
                ),
            ),
        )
        try:
            await self.services.submission_drafts.apply_change(
                self.draft.snapshot.id, self.actor_id, change, locale=None
            )
        except SquidError as error:
            self.notice = error.public_detail()
        else:
            self.notice = "Saved."
        await self._reload()

    async def _reload(self) -> None:
        self.draft = await self.services.submission_drafts.get_accessible(self.draft.snapshot.id, self.actor_id)
        result = await self.services.submission_finalization.status(self.draft.snapshot.id, self.actor_id)
        self.result = result if isinstance(result, FinalizationJobSnapshot) else None

    async def _refresh(self, event: sl.PressEvent) -> None:
        await self._reload()

    async def _submit(self, event: sl.PressEvent) -> None:
        await event.acknowledge()
        await self.services.submission_finalization.submit(self.draft.snapshot.id, self.actor_id, locale=None)
        self.notice = ""
        await self._reload()

    async def _previous(self, event: sl.PressEvent) -> None:
        self.page = max(0, self.page - 1)

    async def _next(self, event: sl.PressEvent) -> None:
        self.page = min((len(self.fields) - 1) // 20, self.page + 1)

    async def _previous_options(self, event: sl.PressEvent) -> None:
        self.option_page = max(0, self.option_page - 1)

    async def _next_options(self, event: sl.PressEvent) -> None:
        self.option_page = min((len(self.options) - 1) // 25, self.option_page + 1)


def _display(value: JSONValue) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


async def draft_editor(services: BotServices, actor_id: int, draft_id: UUID) -> DraftEditorScreen:
    """Reopen the pinned manifest and current draft after a process or session restart."""
    draft = await services.submission_drafts.get_accessible(draft_id, actor_id)
    manifest = await services.submission_forms.manifest_revision(
        draft.snapshot.schema_id, draft.snapshot.schema_revision, locale=None
    )
    if manifest is None:
        message = "This draft's form revision is unavailable."
        raise ValueError(message)
    screen = DraftEditorScreen(services, actor_id, draft, manifest)
    await screen._reload()
    return screen
