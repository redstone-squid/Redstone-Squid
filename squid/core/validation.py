"""Public, transport-independent issues for user-correctable validation failures."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic import ValidationError as PydanticValidationError


class FieldErrorCode(StrEnum):
    RULE_FAILED = "rule_failed"
    INVALID_VALUE = "invalid_value"
    REQUIRED = "required"
    WRONG_TYPE = "wrong_type"
    UNKNOWN_FIELD = "unknown_field"
    REMOVED_FIELD = "removed_field"
    UNKNOWN_OPTION = "unknown_option"
    REQUIRED_VALUE = "required_value"
    INCOMPATIBLE_FIELDS = "incompatible_fields"
    BELOW_MINIMUM = "below_minimum"
    ABOVE_MAXIMUM = "above_maximum"
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    TOO_FEW_ITEMS = "too_few_items"
    TOO_MANY_ITEMS = "too_many_items"


class IssueModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", json_schema_serialization_defaults_required=True)


class EmptyParams(IssueModel):
    pass


class RuleParams(IssueModel):
    rule: str
    reason: str
    depends_on: tuple[str, ...]


class FieldIssue(IssueModel):
    field: str


class RuleIssue(FieldIssue):
    code: Literal[FieldErrorCode.RULE_FAILED] = FieldErrorCode.RULE_FAILED
    params: RuleParams


class SimpleIssue(FieldIssue):
    code: Literal[
        FieldErrorCode.INVALID_VALUE,
        FieldErrorCode.REQUIRED,
        FieldErrorCode.WRONG_TYPE,
        FieldErrorCode.UNKNOWN_FIELD,
        FieldErrorCode.REMOVED_FIELD,
        FieldErrorCode.UNKNOWN_OPTION,
        FieldErrorCode.INCOMPATIBLE_FIELDS,
    ]
    params: EmptyParams = EmptyParams()


class RequiredValueParams(IssueModel):
    expected: JsonValue


class RequiredValue(FieldIssue):
    code: Literal[FieldErrorCode.REQUIRED_VALUE] = FieldErrorCode.REQUIRED_VALUE
    params: RequiredValueParams


class BelowMinimumParams(IssueModel):
    minimum: int | float
    exclusive: bool = False


class BelowMinimum(FieldIssue):
    code: Literal[FieldErrorCode.BELOW_MINIMUM] = FieldErrorCode.BELOW_MINIMUM
    params: BelowMinimumParams


class AboveMaximumParams(IssueModel):
    maximum: int | float
    exclusive: bool = False


class AboveMaximum(FieldIssue):
    code: Literal[FieldErrorCode.ABOVE_MAXIMUM] = FieldErrorCode.ABOVE_MAXIMUM
    params: AboveMaximumParams


class TooShortParams(IssueModel):
    min_length: int


class TooShort(FieldIssue):
    code: Literal[FieldErrorCode.TOO_SHORT] = FieldErrorCode.TOO_SHORT
    params: TooShortParams


class TooLongParams(IssueModel):
    max_length: int


class TooLong(FieldIssue):
    code: Literal[FieldErrorCode.TOO_LONG] = FieldErrorCode.TOO_LONG
    params: TooLongParams


class TooFewItemsParams(IssueModel):
    min_items: int


class TooFewItems(FieldIssue):
    code: Literal[FieldErrorCode.TOO_FEW_ITEMS] = FieldErrorCode.TOO_FEW_ITEMS
    params: TooFewItemsParams


class TooManyItemsParams(IssueModel):
    max_items: int


class TooManyItems(FieldIssue):
    code: Literal[FieldErrorCode.TOO_MANY_ITEMS] = FieldErrorCode.TOO_MANY_ITEMS
    params: TooManyItemsParams


type ValidationIssue = Annotated[
    RuleIssue
    | SimpleIssue
    | RequiredValue
    | BelowMinimum
    | AboveMaximum
    | TooShort
    | TooLong
    | TooFewItems
    | TooManyItems,
    Field(discriminator="code"),
]


def schema_issues(error: PydanticValidationError, *, prefix: str = "") -> tuple[ValidationIssue, ...]:
    """Keep structured locations and allowlisted parameters from a failed schema check."""
    issues = []
    for entry in error.errors(include_input=False, include_url=False):
        path = "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in entry["loc"])
        if path:
            issues.append(_decoder_issue(prefix + path, entry["type"], entry.get("ctx", {})))
    return tuple(issues)


def _decoder_issue(field: str, kind: str, ctx: dict[str, Any]) -> ValidationIssue:
    match kind:
        case "greater_than":
            return BelowMinimum(field=field, params=BelowMinimumParams(minimum=ctx["gt"], exclusive=True))
        case "less_than":
            return AboveMaximum(field=field, params=AboveMaximumParams(maximum=ctx["lt"], exclusive=True))
        case "greater_than_equal":
            return BelowMinimum(field=field, params=BelowMinimumParams(minimum=ctx["ge"]))
        case "less_than_equal":
            return AboveMaximum(field=field, params=AboveMaximumParams(maximum=ctx["le"]))
        case "string_too_short":
            return TooShort(field=field, params=TooShortParams(min_length=ctx["min_length"]))
        case "string_too_long":
            return TooLong(field=field, params=TooLongParams(max_length=ctx["max_length"]))
        case "too_short":
            return TooFewItems(field=field, params=TooFewItemsParams(min_items=ctx["min_length"]))
        case "too_long":
            return TooManyItems(field=field, params=TooManyItemsParams(max_items=ctx["max_length"]))
        case "missing":
            code = FieldErrorCode.REQUIRED
        case "extra_forbidden":
            code = FieldErrorCode.UNKNOWN_FIELD
        case "literal_error" | "enum":
            code = FieldErrorCode.UNKNOWN_OPTION
        case _:
            code = FieldErrorCode.WRONG_TYPE if kind.endswith(("_type", "_parsing")) else FieldErrorCode.INVALID_VALUE
    return SimpleIssue(field=field, code=code)
