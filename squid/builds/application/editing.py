"""Partial build edits shared by submission forms and editing interfaces."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Self, cast, override

from squid.builds.application.commands import Dimensions
from squid.builds.domain import DoorOrientationLiteral, Info, MediaTypeLiteral, ServerInfo
from squid.builds.errors import InvalidBuildError
from squid.builds.models import Build, DoorBuild


class _Unset:
    __slots__ = ()

    @override
    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = _Unset()
type PatchValue[T] = T | _Unset


def _latest[T](current: PatchValue[T], incoming: PatchValue[T]) -> PatchValue[T]:
    return current if isinstance(incoming, _Unset) else incoming


@dataclass(slots=True, frozen=True)
class BuildEditPatch:
    """A partial build update that distinguishes omitted fields from supplied values.

    UNSET leaves a field unchanged. None clears nullable fields, except that a
    None door orientation leaves the current orientation unchanged. Clearing a
    door width or height restores that category's default; empty lists remove
    all entries of that kind.
    """

    version_spec: PatchValue[str | None] = UNSET
    dimensions: PatchValue[Dimensions] = UNSET
    door_dimensions: PatchValue[Dimensions] = UNSET
    door_type: PatchValue[list[str]] = UNSET
    door_orientation_type: PatchValue[str | None] = UNSET
    wiring_placement_restrictions: PatchValue[list[str]] = UNSET
    animated_restrictions: PatchValue[list[str]] = UNSET
    component_restrictions: PatchValue[list[str]] = UNSET
    miscellaneous_restrictions: PatchValue[list[str]] = UNSET
    locationality: PatchValue[str] = UNSET
    directionality: PatchValue[str] = UNSET
    normal_closing_time: PatchValue[int | None] = UNSET
    normal_opening_time: PatchValue[int | None] = UNSET
    extra_user_info: PatchValue[str | None] = UNSET
    creators_ign: PatchValue[list[str]] = UNSET
    image_urls: PatchValue[list[str]] = UNSET
    video_urls: PatchValue[list[str]] = UNSET
    world_download_urls: PatchValue[list[str]] = UNSET
    schematic_urls: PatchValue[list[str]] = UNSET
    render_urls: PatchValue[list[str]] = UNSET
    server_ip: PatchValue[str | None] = UNSET
    coordinates: PatchValue[str | None] = UNSET
    command_to_get_to_build: PatchValue[str | None] = UNSET
    completion_time: PatchValue[str | None] = UNSET
    extra_info: PatchValue[Info] = UNSET
    ai_generated: PatchValue[bool] = UNSET

    @classmethod
    def combine(cls, patches: Iterable[BuildEditPatch]) -> Self:
        """Combine typed patch fragments, with later fragments taking precedence."""
        combined = cls()
        for patch in patches:
            combined = cls(
                version_spec=_latest(combined.version_spec, patch.version_spec),
                dimensions=_latest(combined.dimensions, patch.dimensions),
                door_dimensions=_latest(combined.door_dimensions, patch.door_dimensions),
                door_type=_latest(combined.door_type, patch.door_type),
                door_orientation_type=_latest(combined.door_orientation_type, patch.door_orientation_type),
                wiring_placement_restrictions=_latest(
                    combined.wiring_placement_restrictions,
                    patch.wiring_placement_restrictions,
                ),
                animated_restrictions=_latest(combined.animated_restrictions, patch.animated_restrictions),
                component_restrictions=_latest(combined.component_restrictions, patch.component_restrictions),
                miscellaneous_restrictions=_latest(
                    combined.miscellaneous_restrictions,
                    patch.miscellaneous_restrictions,
                ),
                locationality=_latest(combined.locationality, patch.locationality),
                directionality=_latest(combined.directionality, patch.directionality),
                normal_closing_time=_latest(combined.normal_closing_time, patch.normal_closing_time),
                normal_opening_time=_latest(combined.normal_opening_time, patch.normal_opening_time),
                extra_user_info=_latest(combined.extra_user_info, patch.extra_user_info),
                creators_ign=_latest(combined.creators_ign, patch.creators_ign),
                image_urls=_latest(combined.image_urls, patch.image_urls),
                video_urls=_latest(combined.video_urls, patch.video_urls),
                world_download_urls=_latest(combined.world_download_urls, patch.world_download_urls),
                schematic_urls=_latest(combined.schematic_urls, patch.schematic_urls),
                render_urls=_latest(combined.render_urls, patch.render_urls),
                server_ip=_latest(combined.server_ip, patch.server_ip),
                coordinates=_latest(combined.coordinates, patch.coordinates),
                command_to_get_to_build=_latest(combined.command_to_get_to_build, patch.command_to_get_to_build),
                completion_time=_latest(combined.completion_time, patch.completion_time),
                extra_info=_latest(combined.extra_info, patch.extra_info),
                ai_generated=_latest(combined.ai_generated, patch.ai_generated),
            )
        return combined

    @classmethod
    def from_attributes(cls, changes: Mapping[str, object]) -> Self:
        """Create a patch from attribute names supplied by the edit UI.

        Reject unknown field names. Values are not type-checked here; callers must
        validate them before applying the patch.
        """
        supported = cls.__dataclass_fields__.keys()
        unknown = changes.keys() - supported
        if unknown:
            msg = f"Unsupported build edit fields: {', '.join(sorted(unknown))}"
            raise InvalidBuildError(
                msg,
                context={"fields": sorted(unknown)},
                public_context={"fields": sorted(unknown)},
            )
        patch = cls()
        for name, value in changes.items():
            object.__setattr__(patch, name, value)
        return patch

    def apply(self, build: Build) -> None:
        """Mutate a loaded build or detached preview without saving it.

        Changes are applied in order, so an error can leave earlier fields changed.
        Use a disposable preview or roll back the owning transaction on failure.

        Raises:
            InvalidBuildError: If a door-specific field targets another category.
        """
        direct_fields = (
            "version_spec",
            "wiring_placement_restrictions",
            "animated_restrictions",
            "component_restrictions",
            "miscellaneous_restrictions",
            "creators_ign",
            "completion_time",
            "extra_info",
            "ai_generated",
        )
        for name in direct_fields:
            value = getattr(self, name)
            if value is not UNSET:
                setattr(build, name, value)

        if not isinstance(self.door_type, _Unset):
            build.patterns = self.door_type

        media_fields: tuple[tuple[str, MediaTypeLiteral], ...] = (
            ("image_urls", "image"),
            ("video_urls", "video"),
            ("world_download_urls", "world-download"),
            ("schematic_urls", "schematic"),
            ("render_urls", "render"),
        )
        for name, media_type in media_fields:
            value = getattr(self, name)
            if not isinstance(value, _Unset):
                build.replace_links(media_type, value)

        if not isinstance(self.dimensions, _Unset):
            build.dimensions = self.dimensions

        door_fields = ("door_dimensions", "door_orientation_type", "normal_closing_time", "normal_opening_time")
        patched_door_fields = [name for name in door_fields if not isinstance(getattr(self, name), _Unset)]
        if patched_door_fields:
            if not isinstance(build, DoorBuild):
                msg = "Door fields can only be edited on door builds."
                raise InvalidBuildError(
                    msg,
                    context={"build_id": build.id, "category": build.category, "fields": patched_door_fields},
                    public_context={"fields": patched_door_fields},
                )
            if not isinstance(self.door_dimensions, _Unset):
                width, height, depth = self.door_dimensions
                # Door width and height are required, so clearing restores their defaults.
                door_fields_by_name = type(build).__dataclass_fields__
                build.door_width = width if width is not None else cast(int, door_fields_by_name["door_width"].default)
                build.door_height = (
                    height if height is not None else cast(int, door_fields_by_name["door_height"].default)
                )
                build.door_depth = depth
            if not isinstance(self.door_orientation_type, _Unset) and self.door_orientation_type is not None:
                build.orientation = cast(DoorOrientationLiteral, self.door_orientation_type)
            if not isinstance(self.normal_closing_time, _Unset):
                build.normal_closing_time = self.normal_closing_time
            if not isinstance(self.normal_opening_time, _Unset):
                build.normal_opening_time = self.normal_opening_time

        if not isinstance(self.locationality, _Unset):
            self._replace_reliability(
                build.miscellaneous_restrictions,
                ("Locational", "Locational with fixes"),
                self.locationality,
                "Not locational",
            )
        if not isinstance(self.directionality, _Unset):
            self._replace_reliability(
                build.miscellaneous_restrictions,
                ("Directional", "Directional with fixes"),
                self.directionality,
                "Not directional",
            )

        if not isinstance(self.extra_user_info, _Unset):
            build.description = self.extra_user_info
            if self.extra_user_info is None:
                build.extra_info.pop("user", None)
            else:
                build.extra_info["user"] = self.extra_user_info

        server_info = ServerInfo(**build.extra_info.get("server_info", {}))
        server_changed = any(
            not isinstance(value, _Unset) for value in (self.server_ip, self.coordinates, self.command_to_get_to_build)
        )
        self._update_server_info(server_info)
        if server_changed:
            if server_info:
                build.extra_info["server_info"] = server_info
            else:
                build.extra_info.pop("server_info", None)

    @staticmethod
    def _replace_reliability(values: list[str], old: tuple[str, str], new: str, empty_value: str) -> None:
        values[:] = [value for value in values if value not in old]
        if new != empty_value:
            values.append(new)

    def _update_server_info(self, server_info: ServerInfo) -> None:
        self._update_server_value(server_info, "server_ip", self.server_ip)
        self._update_server_value(server_info, "coordinates", self.coordinates)
        self._update_server_value(server_info, "command_to_build", self.command_to_get_to_build)

    @staticmethod
    def _update_server_value(
        values: ServerInfo,
        key: Literal["server_ip", "coordinates", "command_to_build"],
        value: PatchValue[str | None],
    ) -> None:
        if isinstance(value, _Unset):
            return
        if value is None:
            if key == "server_ip":
                values.pop("server_ip", None)
            elif key == "coordinates":
                values.pop("coordinates", None)
            else:
                values.pop("command_to_build", None)
        else:
            if key == "server_ip":
                values["server_ip"] = value
            elif key == "coordinates":
                values["coordinates"] = value
            else:
                values["command_to_build"] = value
