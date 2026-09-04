"""Portable field specifications for build submission and editing."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from beartype.door import is_bearable

import squid_ui as sl
from squid.bot.submission.parse import get_formatter_and_parser_for_type
from squid.builds.application.editing import BuildEditPatch
from squid.builds.domain import Build, BuildCategory, BuildDraft
from squid.core.i18n import tr

if TYPE_CHECKING:
    from squid.builds.application import BuildService

logger = logging.getLogger(__name__)


class FieldDisplay(StrEnum):
    """Portable preference for a single-line or long-text form control."""

    TEXT = "text"
    PARAGRAPH = "paragraph"


@dataclass(frozen=True, slots=True)
class CreationFieldSpec[ValueT]:
    """One typed creation input and its complete portable presentation metadata."""

    key: str
    label: str
    placeholder: str
    parser: Callable[[str], ValueT]
    formatter: Callable[[ValueT], str]
    draft_value: Callable[[BuildDraft], ValueT]
    target: Callable[[BuildDraft, ValueT, BuildService], Awaitable[None]]
    required: bool = False
    minimum: int | None = None
    maximum: int | None = None
    display: FieldDisplay = FieldDisplay.TEXT

    def parse(self, raw: object) -> ValueT:
        """Parse the adapter value through this field's one declared parser."""
        return self.parser(str(raw or ""))

    def form_field(self, draft: BuildDraft) -> sl.forms.FormField[str]:
        """Build the portable control from the same metadata that parses its value."""
        field_type = sl.forms.TextAreaField if self.display is FieldDisplay.PARAGRAPH else sl.forms.TextField
        return field_type(
            key=self.key,
            label=tr(self.label),
            placeholder=tr(self.placeholder),
            default=self.formatter(self.draft_value(draft)),
            required=self.required,
            minimum=self.minimum,
            maximum=self.maximum,
        )

    def prepare(self, raw: object) -> Callable[[BuildDraft, BuildService], Awaitable[None]]:
        """Parse one value into a type-safe target application without mutating the draft."""
        value = self.parse(raw)

        async def apply(draft: BuildDraft, builds: BuildService) -> None:
            await self.target(draft, value, builds)

        return apply


@dataclass(frozen=True, slots=True)
class BuildFieldSpec[ValueT]:
    """Describe one typed patch field independently of a Discord form implementation."""

    key: str
    label: str
    parser: Callable[[str], ValueT]
    formatter: Callable[[ValueT], str]
    reader: Callable[[Build], ValueT]
    patch: Callable[[ValueT], BuildEditPatch]
    value_type: type[ValueT]
    placeholder: str
    required: bool = False
    minimum: int | None = None
    maximum: int | None = None
    display: FieldDisplay = FieldDisplay.TEXT
    categories: frozenset[BuildCategory] | None = None

    @classmethod
    def typed(
        cls,
        key: str,
        value_type: type[ValueT],
        placeholder: str,
        *,
        reader: Callable[[Build], ValueT],
        patch: Callable[[ValueT], BuildEditPatch],
        label: str | None = None,
        required: bool | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
        display: FieldDisplay = FieldDisplay.TEXT,
        categories: frozenset[BuildCategory] | None = None,
        parser: Callable[[str], ValueT] | None = None,
    ) -> BuildFieldSpec[ValueT]:
        """Build a specification from the shared formatter/parser registry."""
        formatter, default_parser = get_formatter_and_parser_for_type(value_type)
        return cls(
            key,
            label or key.replace("_", " ").title(),
            default_parser if parser is None else parser,
            formatter,
            reader,
            patch,
            value_type,
            placeholder,
            required=not is_bearable(None, value_type) if required is None else required,
            minimum=minimum,
            maximum=maximum,
            display=display,
            categories=categories,
        )

    def applies_to(self, build: Build) -> bool:
        """Whether this field belongs to the build's category."""
        return self.categories is None or build.category in self.categories

    def bind(self, build: Build) -> BoundBuildField[ValueT]:
        """Read this field from a build into a mutable editor value."""
        value = self.reader(build)
        if not is_bearable(value, self.value_type):
            logger.error("Invalid hint for %s: %s", self.key, type(value))
        text = "" if value is None else self.formatter(value)
        return BoundBuildField(self, value, text)


@dataclass(slots=True)
class BoundBuildField[ValueT]:
    """One screen-local value bound from a portable build field specification."""

    spec: BuildFieldSpec[ValueT]
    value: ValueT
    current_text: str
    modified: bool = False
    validation_error: str | None = None

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def summary(self) -> str:
        return f"{self.spec.label}: {self.current_text}"

    def stage(self, text: str) -> None:
        """Parse and retain a proposed form value without mutating the build."""
        self.validation_error = None
        if text == self.current_text:
            return
        try:
            value = self.spec.parser(text)
        except ValueError as error:
            self.validation_error = str(error) or tr(tr(t"Invalid value"))
            return
        self.value = value
        self.current_text = text
        self.modified = True

    def to_patch(self) -> BuildEditPatch:
        """Return this parsed value as its typed patch fragment."""
        return self.spec.patch(self.value)


def field_spec[ValueT](
    key: str,
    value_type: type[ValueT],
    placeholder: str,
    *,
    reader: Callable[[Build], ValueT],
    patch: Callable[[ValueT], BuildEditPatch],
    label: str | None = None,
    required: bool | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    display: FieldDisplay = FieldDisplay.TEXT,
    categories: frozenset[BuildCategory] | None = None,
    parser: Callable[[str], ValueT] | None = None,
) -> BuildFieldSpec[ValueT]:
    """Describe a patch field with explicit typed read and patch operations."""
    return BuildFieldSpec.typed(
        key,
        value_type,
        placeholder,
        reader=reader,
        patch=patch,
        label=label,
        required=required,
        minimum=minimum,
        maximum=maximum,
        display=display,
        categories=categories,
        parser=parser,
    )


__all__ = ["BoundBuildField", "BuildFieldSpec", "CreationFieldSpec", "FieldDisplay", "field_spec"]
