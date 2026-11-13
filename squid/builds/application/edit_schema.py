"""Validation shared by build editors and the HTTP patch contract."""

from collections.abc import Mapping
from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from squid.builds.application.editing import BuildEditPatch

_INVALID_VALUE = "value_error"

type InputDimensions = tuple[int | None, int | None, int | None]


class DoorPatch(BaseModel):
    """A partial edit of the facts only a door has.

    Sending this for a build of another category is rejected: the category is
    structural, so there is no field to set.
    """

    model_config = ConfigDict(extra="forbid")

    door_dimensions: InputDimensions | None = None
    orientation: str | None = Field(default=None, max_length=100)
    patterns: list[str] | None = Field(default=None, max_length=50)
    opening_time: int | None = Field(default=None, ge=0)
    closing_time: int | None = Field(default=None, ge=0)
    visible_opening_time: int | None = Field(default=None, ge=0)
    visible_closing_time: int | None = Field(default=None, ge=0)

    # Wire name -> BuildEditPatch field name.
    _EDIT_FIELDS: ClassVar[Mapping[str, str]] = {
        "door_dimensions": "door_dimensions",
        "orientation": "door_orientation_type",
        "patterns": "door_type",
        "opening_time": "normal_opening_time",
        "closing_time": "normal_closing_time",
        "visible_opening_time": "visible_opening_time",
        "visible_closing_time": "visible_closing_time",
    }

    @model_validator(mode="after")
    def _reject_invalid_clears(self) -> Self:
        non_nullable = {"door_dimensions", "patterns"}
        invalid = sorted(name for name in self.model_fields_set & non_nullable if getattr(self, name) is None)
        if invalid:
            msg = "fields cannot be null: {fields}"
            raise PydanticCustomError(_INVALID_VALUE, msg, {"fields": ", ".join(invalid)})
        if self.door_dimensions is not None and any(value is not None and value <= 0 for value in self.door_dimensions):
            msg = "door_dimensions must be positive when supplied"
            raise PydanticCustomError(_INVALID_VALUE, msg)
        return self

    def edit_attributes(self) -> dict[str, object]:
        """Flatten the supplied fields onto the application patch's names."""
        supplied = self.model_dump(exclude_unset=True)
        return {self._EDIT_FIELDS[name]: value for name, value in supplied.items()}


class ExtenderPatch(BaseModel):
    """Partial changes to an extender's direction, reach, and timings in game ticks."""

    model_config = ConfigDict(extra="forbid")

    orientation: Literal["Horizontal", "Upward", "Downward"] | None = None
    extension_length: int | None = Field(default=None, ge=1, le=512)
    extension_time: int | None = Field(default=None, ge=0)
    retraction_time: int | None = Field(default=None, ge=0)

    # Wire name -> BuildEditPatch field name.
    _EDIT_FIELDS: ClassVar[Mapping[str, str]] = {
        "orientation": "extender_orientation",
        "extension_length": "extension_length",
        "extension_time": "extension_time",
        "retraction_time": "retraction_time",
    }

    def edit_attributes(self) -> dict[str, object]:
        """Flatten the supplied fields onto the application patch's names."""
        supplied = self.model_dump(exclude_unset=True)
        return {self._EDIT_FIELDS[name]: value for name, value in supplied.items()}


class BuildPatch(BaseModel):
    """A partial build edit which preserves omitted versus explicitly cleared fields."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=120)
    version_spec: str | None = Field(default=None, max_length=500)
    dimensions: InputDimensions | None = None
    door: DoorPatch | None = None
    extender: ExtenderPatch | None = None
    wiring_placement_restrictions: list[str] | None = Field(default=None, max_length=100)
    animated_restrictions: list[str] | None = Field(default=None, max_length=100)
    component_restrictions: list[str] | None = Field(default=None, max_length=100)
    miscellaneous_restrictions: list[str] | None = Field(default=None, max_length=100)
    locationality: str | None = Field(default=None, max_length=100)
    directionality: str | None = Field(default=None, max_length=100)
    extra_user_info: str | None = Field(default=None, max_length=10_000)
    creators_ign: list[str] | None = Field(default=None, max_length=100)
    image_urls: list[str] | None = Field(default=None, max_length=100)
    video_urls: list[str] | None = Field(default=None, max_length=100)
    world_download_urls: list[str] | None = Field(default=None, max_length=100)
    schematic_urls: list[str] | None = Field(default=None, max_length=100)
    render_urls: list[str] | None = Field(default=None, max_length=100)
    server_ip: str | None = Field(default=None, max_length=500)
    coordinates: str | None = Field(default=None, max_length=500)
    command_to_get_to_build: str | None = Field(default=None, max_length=2_000)
    completion_time: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _reject_invalid_clears(self) -> Self:
        non_nullable = {
            "dimensions",
            "door",
            "extender",
            "wiring_placement_restrictions",
            "animated_restrictions",
            "component_restrictions",
            "miscellaneous_restrictions",
            "locationality",
            "directionality",
            "creators_ign",
            "image_urls",
            "video_urls",
            "world_download_urls",
            "schematic_urls",
            "render_urls",
        }
        invalid = sorted(name for name in self.model_fields_set & non_nullable if getattr(self, name) is None)
        if invalid:
            msg = "fields cannot be null: {fields}"
            raise PydanticCustomError(_INVALID_VALUE, msg, {"fields": ", ".join(invalid)})
        if self.dimensions is not None and any(value is not None and value <= 0 for value in self.dimensions):
            msg = "dimensions must be positive when supplied"
            raise PydanticCustomError(_INVALID_VALUE, msg)
        return self

    def edit_attributes(self) -> dict[str, object]:
        """Flatten the supplied fields onto the application patch's names.

        ``door: null`` is rejected above, so a supplied ``door`` is always an
        object whose own set fields are the ones to apply.
        """
        attributes: dict[str, object] = self.model_dump(exclude_unset=True)
        attributes.pop("door", None)
        attributes.pop("extender", None)
        if self.door is not None:
            attributes.update(self.door.edit_attributes())
        if self.extender is not None:
            attributes.update(self.extender.edit_attributes())
        return attributes

    @classmethod
    def from_patch(cls, patch: BuildEditPatch) -> Self:
        """Validate a typed editor patch using the same rules as HTTP input."""
        attributes = patch.attributes()
        door = {wire: attributes.pop(name) for wire, name in DoorPatch._EDIT_FIELDS.items() if name in attributes}
        if door:
            attributes["door"] = door
        extender = {
            wire: attributes.pop(name) for wire, name in ExtenderPatch._EDIT_FIELDS.items() if name in attributes
        }
        if extender:
            attributes["extender"] = extender
        return cls.model_validate(attributes)
