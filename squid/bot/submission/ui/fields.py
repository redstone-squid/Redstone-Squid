"""Portable field specifications for build submission and editing."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from beartype.door import is_bearable

import squid_ui as sl
from squid.bot.submission.parse import get_formatter_and_parser_for_type
from squid.builds.domain import Build, BuildCategory, BuildDraft, DoorBuild
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
class BuildFieldSpec:
    """Describe one typed patch field independently of a Discord form implementation."""

    patch_key: str
    label: str
    parser: Callable[[str], object]
    formatter: Callable[[object], str]
    placeholder: str
    required: bool = False
    minimum: int | None = None
    maximum: int | None = None
    display: FieldDisplay = FieldDisplay.TEXT
    categories: frozenset[BuildCategory] | None = None

    @classmethod
    def typed(
        cls,
        patch_key: str,
        value_type: type[Any],
        placeholder: str,
        *,
        label: str | None = None,
        required: bool | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
        display: FieldDisplay = FieldDisplay.TEXT,
        categories: frozenset[BuildCategory] | None = None,
        parser: Callable[[str], object] | None = None,
    ) -> BuildFieldSpec:
        """Build a specification from the shared formatter/parser registry."""
        formatter, default_parser = get_formatter_and_parser_for_type(value_type)
        return cls(
            patch_key,
            label or patch_key.replace("_", " ").title(),
            default_parser if parser is None else parser,
            formatter,
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

    def bind(self, build: Build) -> BoundBuildField:
        """Read this field from a build into a mutable editor value."""
        value = _read_edit_value(build, self.patch_key)
        if not is_bearable(value, _field_type(build, self.patch_key)):
            logger.error("Invalid hint for %s: %s", self.patch_key, type(value))
        text = "" if value is None else self.formatter(value)
        return BoundBuildField(self, value, text)


@dataclass(slots=True)
class BoundBuildField:
    """One screen-local value bound from a portable build field specification."""

    spec: BuildFieldSpec
    actual_value: object
    current_text: str
    modified: bool = False
    validation_error: str | None = None

    @property
    def attribute(self) -> str:
        return self.spec.patch_key

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
            self.validation_error = str(error) or tr("Invalid value")
            return
        self.actual_value = value
        self.current_text = text
        self.modified = True


_EDIT_FIELD_READERS: dict[str, tuple[Callable[[Build], object], type[Any]]] = {
    "door_type": (lambda build: list(build.patterns), list[str]),
    "door_orientation_type": (
        lambda build: build.orientation if isinstance(build, DoorBuild) else None,
        str | None,
    ),
    "door_dimensions": (
        lambda build: build.door_dimensions if isinstance(build, DoorBuild) else None,
        tuple[int | None, int | None, int | None] | None,
    ),
    "normal_opening_time": (
        lambda build: build.normal_opening_time if isinstance(build, DoorBuild) else None,
        int | None,
    ),
    "normal_closing_time": (
        lambda build: build.normal_closing_time if isinstance(build, DoorBuild) else None,
        int | None,
    ),
    "extra_user_info": (lambda build: build.description, str | None),
    "server_ip": (lambda build: _server_info(build).get("server_ip"), str | None),
    "coordinates": (lambda build: _server_info(build).get("coordinates"), str | None),
    "command_to_get_to_build": (lambda build: _server_info(build).get("command_to_build"), str | None),
    "image_urls": (lambda build: list(build.image_urls), list[str]),
    "video_urls": (lambda build: list(build.video_urls), list[str]),
    "world_download_urls": (lambda build: list(build.world_download_urls), list[str]),
    "schematic_urls": (lambda build: list(build.schematic_urls), list[str]),
    "render_urls": (lambda build: list(build.render_urls), list[str]),
}

_FIELD_TYPES: dict[str, type[Any]] = {
    "version_spec": str | None,
    "dimensions": tuple[int | None, int | None, int | None],
    "wiring_placement_restrictions": list[str],
    "animated_restrictions": list[str],
    "component_restrictions": list[str],
    "miscellaneous_restrictions": list[str],
    "creators_ign": list[str],
    "completion_time": str | None,
}


def _server_info(build: Build) -> dict[str, Any]:
    return dict(build.extra_info.get("server_info", {}))


def _field_type(build: Build, patch_key: str) -> type[Any]:
    reader = _EDIT_FIELD_READERS.get(patch_key)
    return reader[1] if reader is not None else build.get_attr_type(patch_key)


def _read_edit_value(build: Build, patch_key: str) -> object:
    reader = _EDIT_FIELD_READERS.get(patch_key)
    if reader is not None:
        return reader[0](build)
    try:
        return getattr(build, patch_key)
    except AttributeError as error:
        message = f"Invalid build patch key {patch_key}"
        raise ValueError(message) from error


def field_spec(
    patch_key: str,
    placeholder: str,
    *,
    label: str | None = None,
    required: bool | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    display: FieldDisplay = FieldDisplay.TEXT,
    categories: frozenset[BuildCategory] | None = None,
    parser: Callable[[str], object] | None = None,
) -> BuildFieldSpec:
    """Describe a patch field using the domain model's declared value type."""
    value_type = _EDIT_FIELD_READERS.get(patch_key, (None, None))[1]
    if value_type is None:
        try:
            value_type = _FIELD_TYPES[patch_key]
        except KeyError as error:
            message = f"No portable field type for {patch_key}"
            raise ValueError(message) from error
    return BuildFieldSpec.typed(
        patch_key,
        value_type,
        placeholder,
        label=label,
        required=required,
        minimum=minimum,
        maximum=maximum,
        display=display,
        categories=categories,
        parser=parser,
    )


__all__ = ["BoundBuildField", "BuildFieldSpec", "CreationFieldSpec", "FieldDisplay", "field_spec"]
