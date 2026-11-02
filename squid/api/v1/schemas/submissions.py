"""Renderer-neutral submission form and synchronized-draft schemas."""

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

StableIdentifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ClientInstanceIdentifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
IdempotencyKey = Annotated[str, Field(min_length=8, max_length=255, pattern=r"^[\x21-\x7e]+$")]
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
    """A renderer-neutral condition controlling whether a field is shown."""

    field_id: StableIdentifier
    operator: VisibilityOperator
    value: JsonValue

    @classmethod
    def from_domain(cls, rule: VisibilityRule) -> VisibilityRuleResponse:
        return cls(field_id=rule.field_id, operator=rule.operator, value=_json_value(rule.value))


class FieldConstraintsResponse(StrictSchema):
    """Validation bounds interpreted the same way by clients and the server."""

    minimum: int | float | None
    maximum: int | float | None
    min_length: int | None
    max_length: int | None
    min_items: int | None
    max_items: int | None
    must_equal: JsonValue

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
    """One field that any supported submission renderer can present."""

    id: StableIdentifier
    label: str
    control: ControlKind
    value_kind: ValueKind
    required: bool
    help_text: str | None
    constraints: FieldConstraintsResponse
    options: list[ChoiceOptionResponse]
    option_source: str | None
    visible_when: VisibilityRuleResponse | None
    default: JsonValue
    repeatable: bool
    required_capability: str | None
    origins: list[SubmissionOrigin]

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
    """One immutable renderer-neutral submission form revision."""

    schema_id: str
    revision: int
    minimum_protocol: int
    maximum_protocol: int
    common_sections: list[FormSectionResponse]
    categories: list[CategoryFormResponse]

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

    source: StableIdentifier
    category: StableIdentifier
    revision: int
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

    category: StableIdentifier
    origin: SubmissionOrigin
    client_capabilities: set[StableIdentifier] = Field(default_factory=set, max_length=64)


class FieldOperationRequest(StrictSchema):
    """Set or unset exactly one stable form field."""

    operation_id: UUID
    field_id: StableIdentifier
    kind: FieldOperationKind
    value: JsonValue = None

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
    """An atomic optimistic draft edit with retry-safe identity."""

    base_revision: int = Field(ge=0)
    client_instance_id: ClientInstanceIdentifier
    idempotency_key: IdempotencyKey
    operations: list[FieldOperationRequest] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_change(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> DraftChange:
        """Convert this request to the transport-neutral atomic change."""
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
    schema_revision: int
    category: StableIdentifier
    revision: int
    status: DraftStatus
    answers: dict[str, JsonValue]
    origin: SubmissionOrigin
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    source_installation_id: UUID | None = None

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
    display_name: str | None
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

    drafts: list[DraftSummaryResponse] = Field(max_length=10)

    @classmethod
    def from_domain(cls, drafts: tuple[StoredDraft, ...]) -> DraftListResponse:
        return cls(drafts=[DraftSummaryResponse.from_domain(draft) for draft in drafts])


class DraftChangeResponse(StrictSchema):
    """The state produced by a draft change and whether it was a replay."""

    draft: StoredDraftResponse
    replayed: bool


class SubmissionAttentionIssueResponse(StrictSchema):
    """One stable field-level reason that a submitter can act on."""

    field_id: StableIdentifier
    reason: SubmissionAttentionReason


class SubmissionFinalizationResponse(StrictSchema):
    """Owner-visible state of durable draft finalization."""

    draft_id: UUID
    draft_revision: int
    status: FinalizationJobStatus
    issues: list[SubmissionAttentionIssueResponse]
    build_id: int | None

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
