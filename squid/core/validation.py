"""Public, transport-independent issues for user-correctable validation failures."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class FieldErrorCode(StrEnum):
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
    model_config = ConfigDict(frozen=True, extra="forbid")


class EmptyParams(IssueModel):
    pass


class RuleParams(IssueModel):
    rule: str
    depends_on: tuple[str, ...]


class FieldIssue(IssueModel):
    field: str


class SimpleIssue(FieldIssue):
    code: Literal[
        FieldErrorCode.INVALID_VALUE,
        FieldErrorCode.REQUIRED,
        FieldErrorCode.WRONG_TYPE,
        FieldErrorCode.UNKNOWN_FIELD,
        FieldErrorCode.REMOVED_FIELD,
        FieldErrorCode.UNKNOWN_OPTION,
        FieldErrorCode.REQUIRED_VALUE,
        FieldErrorCode.INCOMPATIBLE_FIELDS,
    ]
    params: EmptyParams | RuleParams = EmptyParams()


class BelowMinimumParams(IssueModel):
    minimum: int | float


class BelowMinimum(FieldIssue):
    code: Literal[FieldErrorCode.BELOW_MINIMUM] = FieldErrorCode.BELOW_MINIMUM
    params: BelowMinimumParams | RuleParams


class AboveMaximumParams(IssueModel):
    maximum: int | float


class AboveMaximum(FieldIssue):
    code: Literal[FieldErrorCode.ABOVE_MAXIMUM] = FieldErrorCode.ABOVE_MAXIMUM
    params: AboveMaximumParams | RuleParams


class TooShortParams(IssueModel):
    min_length: int


class TooShort(FieldIssue):
    code: Literal[FieldErrorCode.TOO_SHORT] = FieldErrorCode.TOO_SHORT
    params: TooShortParams | RuleParams


class TooLongParams(IssueModel):
    max_length: int


class TooLong(FieldIssue):
    code: Literal[FieldErrorCode.TOO_LONG] = FieldErrorCode.TOO_LONG
    params: TooLongParams | RuleParams


class TooFewItemsParams(IssueModel):
    min_items: int


class TooFewItems(FieldIssue):
    code: Literal[FieldErrorCode.TOO_FEW_ITEMS] = FieldErrorCode.TOO_FEW_ITEMS
    params: TooFewItemsParams | RuleParams


class TooManyItemsParams(IssueModel):
    max_items: int


class TooManyItems(FieldIssue):
    code: Literal[FieldErrorCode.TOO_MANY_ITEMS] = FieldErrorCode.TOO_MANY_ITEMS
    params: TooManyItemsParams | RuleParams


type ValidationIssue = Annotated[
    SimpleIssue | BelowMinimum | AboveMaximum | TooShort | TooLong | TooFewItems | TooManyItems,
    Field(discriminator="code"),
]
