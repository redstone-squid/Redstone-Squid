"""Build editing values and lease coordination."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Self, cast, override

from squid.builds.application.commands import Dimensions
from squid.builds.application.ports import BuildLockManager, BuildRepository
from squid.builds.domain import Build, DoorBuild, DoorOrientationLiteral, Info, MediaTypeLiteral, ServerInfo
from squid.builds.errors import BuildBusyError, BuildNotFoundError, BuildRevisionMismatchError, InvalidBuildError
from squid.core.errors import InvalidStateError


class _Unset:
    __slots__ = ()

    @override
    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = _Unset()
type PatchValue[T] = T | _Unset


@dataclass(slots=True, frozen=True)
class BuildEditPatch:
    """A partial build update which distinguishes omission from clearing."""

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
    def from_attributes(cls, changes: Mapping[str, object]) -> Self:
        """Create a patch from build attribute names used by the generic edit UI."""
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
        """Apply the patch after the caller has acquired the build lock.

        Raises:
            InvalidBuildError: If a door-specific field is patched onto a build
                of another category.
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
                # A cleared width or height falls back to the entity's declared
                # defaults, matching the save-time coercion this replaced.
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


class BuildEditLease:
    """Exception-safe lease for previewing and committing one build edit."""

    def __init__(
        self,
        repository: BuildRepository,
        locks: BuildLockManager,
        persist: Callable[[Build], Awaitable[None]],
        build_id: int,
        patch: BuildEditPatch,
        *,
        blocking: bool,
        timeout: float,
        expected_revision: int | None,
    ) -> None:
        self._repository = repository
        self._locks = locks
        self._persist = persist
        self._build_id = build_id
        self._patch = patch
        self._blocking = blocking
        self._timeout = timeout
        self._expected_revision = expected_revision
        self._build: Build | None = None
        self._committed = False

    @property
    def build(self) -> Build:
        if self._build is None:
            msg = "The edit lease has not been entered."
            raise InvalidStateError(msg)
        return self._build

    async def __aenter__(self) -> Self:
        acquired = await self._locks.acquire(
            self._build_id,
            blocking=self._blocking,
            timeout=self._timeout,
        )
        if not acquired:
            raise BuildBusyError(self._build_id)
        # Every exit path from here must release: the lease is held but __aexit__
        # will not run until __aenter__ returns, so a cancellation at any await
        # below would otherwise strand it for the lifetime of the process.
        try:
            build = await self._repository.get_by_id(self._build_id)
            if build is None:
                raise BuildNotFoundError(self._build_id)  # noqa: TRY301
            if self._expected_revision is not None and build.revision != self._expected_revision:
                raise BuildRevisionMismatchError(  # noqa: TRY301
                    self._build_id,
                    expected_revision=self._expected_revision,
                    current_revision=build.revision,
                )
            self._build = build
            self._patch.apply(build)
        except BaseException:
            self._build = None
            await self._locks.release(self._build_id)
            raise
        return self

    async def commit(self) -> Build:
        if self._committed:
            msg = "This build edit has already been committed."
            raise InvalidStateError(msg)
        build = self.build
        await self._persist(build)
        self._committed = True
        return build

    async def __aexit__(self, *_exc: object) -> None:
        if self._build is not None:
            await self._locks.release(self._build_id)
