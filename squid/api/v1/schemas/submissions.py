"""Submission form and synchronized-draft schemas.

The form describes fields and constraints; how they are drawn is the client's
decision.
"""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from squid.core.errors import JSONValue
from squid.submissions.application import FinalizationJobSnapshot, FormOptionSet, StoredDraft
from squid.submissions.domain import (
    CategoryForm,
    ChoiceOption,
    ControlKind,
    DraftChange,
    DraftStatus,
    FieldConstraints,
    FieldOperation,
    FieldOperationKind,
    FinalizationJobStatus,
    FormField,
    FormManifest,
    FormSection,
    SubmissionAttentionReason,
    SubmissionOrigin,
    ValueKind,
    VisibilityOperator,
    VisibilityRule,
)

StableIdentifier = Annotated[
    str,
    Field(
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
        description="Lowercase snake_case identifier of at most 64 characters, stable across form revisions.",
    ),
]
ClientInstanceIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
        description="Names one running client, so a draft edited from two of them can be attributed. Chosen by the "
        "client and stable for its lifetime.",
    ),
]
IdempotencyKey = Annotated[
    str,
    Field(
        min_length=8,
        max_length=255,
        pattern=r"^[\x21-\x7e]+$",
        description="Caller-chosen visible-ASCII key. Resending a change under a key already applied returns the "
        "earlier outcome instead of applying it twice.",
    ),
]
_JSON_VALUE = TypeAdapter(JsonValue)


class StrictSchema(BaseModel):
    """Base model which rejects contract fields unknown to this server."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ChoiceOptionResponse(StrictSchema):
    """One stable selectable value and its localized display label."""

    value: StableIdentifier
    label: str

    @classmethod
    def from_domain(cls, option: ChoiceOption) -> ChoiceOptionResponse:
        return cls(value=option.value, label=option.label)


class VisibilityRuleResponse(StrictSchema):
    """A condition controlling whether a field is shown, evaluated by any client.

    A field whose rule does not match is hidden and not validated, so leaving it unanswered is fine.
    """

    field_id: StableIdentifier = Field(description="The field whose answer is tested.")
    operator: VisibilityOperator = Field(
        description="`equals` and `not_equals` compare the answer to `value`; `in` tests membership and requires "
        "`value` to be an array."
    )
    value: JsonValue

    @classmethod
    def from_domain(cls, rule: VisibilityRule) -> VisibilityRuleResponse:
        return cls(field_id=rule.field_id, operator=rule.operator, value=_json_value(rule.value))


class FieldConstraintsResponse(StrictSchema):
    """Validation bounds interpreted the same way by clients and the server.

    Every bound is null when the field is unconstrained on that axis.
    """

    minimum: int | float | None = Field(description="Inclusive numeric lower bound.")
    maximum: int | float | None = Field(description="Inclusive numeric upper bound.")
    min_length: int | None = Field(description="Shortest accepted string.")
    max_length: int | None = Field(description="Longest accepted string.")
    min_items: int | None = Field(description="Fewest accepted list entries.")
    max_items: int | None = Field(description="Most accepted list entries.")
    must_equal: JsonValue = Field(description="The one accepted value, when the field admits only one.")

    @classmethod
    def from_domain(cls, constraints: FieldConstraints) -> FieldConstraintsResponse:
        return cls(
            minimum=constraints.minimum,
            maximum=constraints.maximum,
            min_length=constraints.min_length,
            max_length=constraints.max_length,
            min_items=constraints.min_items,
            max_items=constraints.max_items,
            must_equal=_json_value(constraints.must_equal),
        )


class FormFieldResponse(StrictSchema):
    """One field that any supported submission renderer can present.

    `options` and `option_source` are mutually exclusive: a choice field carries inline options or
    names a dynamic source, never both.
    """

    id: StableIdentifier = Field(description="The `field_id` used in draft operations.")
    label: str
    control: ControlKind = Field(description="How to draw the field; every client draws all of them.")
    value_kind: ValueKind = Field(
        description="JSON shape of the answer. `game_ticks` is an integer counted in Minecraft ticks, and "
        "`string_list` is an array of strings."
    )
    required: bool = Field(description="Enforced only at finalization; a draft may be saved without it.")
    help_text: str | None
    constraints: FieldConstraintsResponse
    options: list[ChoiceOptionResponse] = Field(description="Inline options, empty when `option_source` is set.")
    option_source: str | None = Field(
        description="Names a dynamic option set to fetch for this category; null when `options` carries them inline."
    )
    visible_when: VisibilityRuleResponse | None = Field(description="Null when the field is always visible.")
    default: JsonValue = Field(description="Value to prefill, null when the field has none.")
    repeatable: bool = Field(
        description="True only on `string_list` fields, whose entries a client may add and remove."
    )
    required_capability: str | None = Field(
        description="A renderer capability the field needs, drawn from `renderer.capability_identifiers` in "
        "`/v1/capabilities`. A client lacking it cannot draw this field."
    )
    origins: list[SubmissionOrigin] = Field(
        description="Transports the field applies to. A draft created with another origin neither shows nor validates "
        "it."
    )

    @classmethod
    def from_domain(cls, form_field: FormField) -> FormFieldResponse:
        return cls(
            id=form_field.id,
            label=form_field.label,
            control=form_field.control,
            value_kind=form_field.value_kind,
            required=form_field.required,
            help_text=form_field.help_text,
            constraints=FieldConstraintsResponse.from_domain(form_field.constraints),
            options=[ChoiceOptionResponse.from_domain(option) for option in form_field.options],
            option_source=form_field.option_source,
            visible_when=(
                None if form_field.visible_when is None else VisibilityRuleResponse.from_domain(form_field.visible_when)
            ),
            default=_json_value(form_field.default),
            repeatable=form_field.repeatable,
            required_capability=form_field.required_capability,
            origins=sorted(form_field.origins, key=lambda origin: origin.value),
        )


class FormSectionResponse(StrictSchema):
    """An ordered group of related form fields."""

    id: StableIdentifier
    title: str
    fields: list[FormFieldResponse]

    @classmethod
    def from_domain(cls, section: FormSection) -> FormSectionResponse:
        return cls(
            id=section.id,
            title=section.title,
            fields=[FormFieldResponse.from_domain(form_field) for form_field in section.fields],
        )


class CategoryFormResponse(StrictSchema):
    """A stable build category and its category-specific form sections."""

    code: StableIdentifier
    label: str
    sections: list[FormSectionResponse]

    @classmethod
    def from_domain(cls, category: CategoryForm) -> CategoryFormResponse:
        return cls(
            code=category.code,
            label=category.label,
            sections=[FormSectionResponse.from_domain(section) for section in category.sections],
        )


class FormManifestResponse(StrictSchema):
    """One immutable submission form revision, drawn however the client chooses."""

    schema_id: str
    revision: int = Field(
        description="Rises when the form changes. A draft stays pinned to the revision it was created under."
    )
    minimum_protocol: int = Field(
        description="Lowest submission protocol version that can render this manifest. Compare against "
        "`protocols.submission` in `/v1/capabilities` and refuse a manifest outside the overlap."
    )
    maximum_protocol: int = Field(description="Highest submission protocol version that can render this manifest.")
    common_sections: list[FormSectionResponse] = Field(description="Sections presented whatever the category.")
    categories: list[CategoryFormResponse] = Field(description="Every category and the sections specific to it.")

    @classmethod
    def from_domain(cls, manifest: FormManifest) -> FormManifestResponse:
        return cls(
            schema_id=manifest.schema_id,
            revision=manifest.revision,
            minimum_protocol=manifest.minimum_protocol,
            maximum_protocol=manifest.maximum_protocol,
            common_sections=[FormSectionResponse.from_domain(section) for section in manifest.common_sections],
            categories=[CategoryFormResponse.from_domain(category) for category in manifest.categories],
        )


class FormOptionSetResponse(StrictSchema):
    """One revision of a category-aware dynamic option source."""

    source: StableIdentifier = Field(description="The `option_source` a form field named.")
    category: StableIdentifier = Field(description="Build category these options apply to.")
    revision: int = Field(description="Rises when the option set changes; starts at 1.")
    options: list[ChoiceOptionResponse]

    @classmethod
    def from_domain(cls, option_set: FormOptionSet) -> FormOptionSetResponse:
        return cls(
            source=option_set.source,
            category=option_set.category,
            revision=option_set.revision,
            options=[ChoiceOptionResponse.from_domain(option) for option in option_set.options],
        )


class DraftCreateRequest(StrictSchema):
    """Request an empty account-owned draft pinned to the current form revision."""

    category: StableIdentifier = Field(description="A `code` from the manifest's `categories`.")
    origin: SubmissionOrigin = Field(description="The transport that owns the draft, deciding which fields apply.")
    client_capabilities: set[StableIdentifier] = Field(
        default_factory=set,
        max_length=64,
        description="Renderer capabilities this client has. Creation is refused with 400 when a required field of "
        "`category` names one that is missing.",
    )


class FieldOperationRequest(StrictSchema):
    """Set or unset exactly one stable form field."""

    operation_id: UUID = Field(description="Unique within the change; identifies this operation on replay.")
    field_id: StableIdentifier = Field(description="A field `id` from the manifest.")
    kind: FieldOperationKind = Field(description="`set` writes `value`; `unset` clears the answer.")
    value: JsonValue = Field(
        default=None,
        description="The new answer, in the field's `value_kind` shape and at most 16 KiB encoded. Must be null when "
        "`kind` is `unset`.",
    )

    @model_validator(mode="after")
    def validate_operation(self) -> Self:
        FieldOperation(
            operation_id=self.operation_id,
            field_id=self.field_id,
            kind=self.kind,
            value=self.value,
        )
        return self

    def to_domain(self) -> FieldOperation:
        """Convert this JSON-safe operation to its domain value object."""
        return FieldOperation(
            operation_id=self.operation_id,
            field_id=self.field_id,
            kind=self.kind,
            value=self.value,
        )


class DraftChangeRequest(StrictSchema):
    """An atomic optimistic draft edit with retry-safe identity.

    Every operation applies or none does. Operation ids must be unique within the change, and a change
    may touch each field at most once.
    """

    base_revision: int = Field(
        ge=0, description="The draft `revision` this edit was composed against. A stale value is refused with 409."
    )
    client_instance_id: ClientInstanceIdentifier
    idempotency_key: IdempotencyKey
    operations: list[FieldOperationRequest] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_change(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> DraftChange:
        """Convert this request to the atomic change the application layer applies."""
        return DraftChange(
            base_revision=self.base_revision,
            client_instance_id=self.client_instance_id,
            idempotency_key=self.idempotency_key,
            operations=tuple(operation.to_domain() for operation in self.operations),
        )


class StoredDraftResponse(StrictSchema):
    """The compacted current state of one caller-owned synchronized draft."""

    id: UUID
    schema_id: str
    schema_revision: int = Field(description="Form revision this draft is pinned to.")
    category: StableIdentifier
    revision: int = Field(description="Rises by one per applied change; send it as the next change's `base_revision`.")
    status: DraftStatus = Field(
        description="`editing` accepts changes; `processing` is being finalized; `needs_attention` has repairable "
        "issues; `submitted` produced a build; `expired` passed `expires_at`."
    )
    answers: dict[str, JsonValue] = Field(description="Current answers by field id. An unanswered field is absent.")
    origin: SubmissionOrigin
    created_at: datetime
    updated_at: datetime
    expires_at: datetime = Field(description="When the draft is discarded if it is not finalized first.")
    source_installation_id: UUID | None = Field(
        default=None, description="The Paper installation the draft came from. Null for every other origin."
    )

    @classmethod
    def from_domain(cls, draft: StoredDraft) -> StoredDraftResponse:
        return cls(
            id=draft.snapshot.id,
            schema_id=draft.snapshot.schema_id,
            schema_revision=draft.snapshot.schema_revision,
            category=draft.snapshot.category,
            revision=draft.snapshot.revision,
            status=draft.snapshot.status,
            answers={field_id: _json_value(value) for field_id, value in draft.snapshot.answers.items()},
            origin=draft.origin,
            created_at=draft.created_at.to_stdlib(),
            updated_at=draft.updated_at.to_stdlib(),
            expires_at=draft.expires_at.to_stdlib(),
            source_installation_id=draft.source_installation_id,
        )


class DraftSummaryResponse(StrictSchema):
    """Compact active-draft metadata safe for cross-client discovery."""

    id: UUID
    schema_id: str
    schema_revision: int
    category: StableIdentifier
    revision: int
    status: DraftStatus
    origin: SubmissionOrigin
    display_name: str | None = Field(
        description="The draft's `display_name` answer, for listing it. Null while it is unanswered."
    )
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    @classmethod
    def from_domain(cls, draft: StoredDraft) -> DraftSummaryResponse:
        display_name = draft.snapshot.answers.get("display_name")
        return cls(
            id=draft.snapshot.id,
            schema_id=draft.snapshot.schema_id,
            schema_revision=draft.snapshot.schema_revision,
            category=draft.snapshot.category,
            revision=draft.snapshot.revision,
            status=draft.snapshot.status,
            origin=draft.origin,
            display_name=display_name if isinstance(display_name, str) else None,
            created_at=draft.created_at.to_stdlib(),
            updated_at=draft.updated_at.to_stdlib(),
            expires_at=draft.expires_at.to_stdlib(),
        )


class DraftListResponse(StrictSchema):
    """Bounded active drafts owned by one authenticated account."""

    drafts: list[DraftSummaryResponse] = Field(
        max_length=10, description="An account holds at most ten active drafts, so this is the whole collection."
    )

    @classmethod
    def from_domain(cls, drafts: tuple[StoredDraft, ...]) -> DraftListResponse:
        return cls(drafts=[DraftSummaryResponse.from_domain(draft) for draft in drafts])


class DraftChangeResponse(StrictSchema):
    """The state produced by a draft change and whether it was a replay."""

    draft: StoredDraftResponse
    replayed: bool = Field(
        description="True when this `idempotency_key` had already been applied, so `draft` is the earlier outcome and "
        "nothing changed."
    )


class SubmissionAttentionIssueResponse(StrictSchema):
    """One stable field-level reason that a submitter can act on."""

    field_id: StableIdentifier = Field(description="The field to repair.")
    reason: SubmissionAttentionReason = Field(description="What is wrong with it, as a stable code to branch on.")


class SubmissionFinalizationResponse(StrictSchema):
    """Owner-visible state of durable draft finalization."""

    draft_id: UUID
    draft_revision: int = Field(description="The draft revision this job was started from.")
    status: FinalizationJobStatus = Field(
        description="`pending` and `claimed` are still running; `needs_attention` lists repairable `issues`; "
        "`completed` carries `build_id`; `dead` failed for good."
    )
    issues: list[SubmissionAttentionIssueResponse] = Field(
        description="Non-empty only when `status` is `needs_attention`."
    )
    build_id: int | None = Field(description="The build finalization produced. Null until `status` is `completed`.")

    @classmethod
    def from_domain(cls, snapshot: FinalizationJobSnapshot) -> SubmissionFinalizationResponse:
        return cls(
            draft_id=snapshot.draft_id,
            draft_revision=snapshot.draft_revision,
            status=snapshot.status,
            issues=[
                SubmissionAttentionIssueResponse(field_id=issue.field_id, reason=issue.reason)
                for issue in snapshot.issues
            ],
            build_id=snapshot.result.build_id if snapshot.result is not None else None,
        )


def _json_value(value: JSONValue) -> JsonValue:
    """Normalize domain mappings and sequences to concrete JSON containers."""
    return _JSON_VALUE.validate_python(value)
