"""Application services for build submission and editing."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol, Self, override

from squid.db.builds import Build
from squid.db.schema import BuildCategory, Info, RestrictionTypeLiteral, ServerInfo, Status


class BuildNotFoundError(LookupError):
    """Raised when a requested build does not exist."""


class BuildBusyError(RuntimeError):
    """Raised when a build is already being edited."""


class _Unset:
    __slots__ = ()

    @override
    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = _Unset()
type PatchValue[T] = T | _Unset
type Dimensions = tuple[int | None, int | None, int | None]
type Reliability = Literal[
    "Locational",
    "Locational with fixes",
    "Not locational",
    "Directional",
    "Directional with fixes",
    "Not directional",
]


class BuildRepository(Protocol):
    """Persistence operations required by the build application service."""

    async def get_by_id(self, build_id: int) -> Build | None: ...

    async def save(self, build: Build) -> None: ...

    async def confirm(self, build: Build) -> None: ...

    async def deny(self, build: Build) -> None: ...

    async def acquire_lock(self, build_id: int, *, blocking: bool, timeout: float) -> bool: ...

    async def release_lock(self, build_id: int) -> None: ...

    async def update_smallest_door_records_without_title(self) -> None: ...


class BuildEmbeddingCoordinator(Protocol):
    """Prepare and index build embeddings around relational persistence."""

    async def prepare(self, build: Build) -> None: ...

    async def index(self, build: Build) -> None: ...


class DefaultVersionResolver(Protocol):
    """Resolve the default version used when a build omits compatibility."""

    async def newest(self, edition: Literal["Java", "Bedrock"]) -> str: ...


@dataclass(frozen=True, slots=True)
class RestrictionDefinition:
    """Restriction fields needed when classifying submission input."""

    name: str
    type: RestrictionTypeLiteral | None


class RestrictionRepository(Protocol):
    """Restriction metadata needed by build submission."""

    async def fetch_all_restrictions(self) -> Sequence[RestrictionDefinition]: ...

    async def add_alias(self, restriction: str, alias: str) -> None: ...


class RestrictionService:
    """Application operations for restriction names and aliases."""

    def __init__(self, repository: RestrictionRepository):
        self._repository = repository

    async def add_alias(self, restriction: str, alias: str) -> None:
        await self._repository.add_alias(restriction, alias)

    async def names(self) -> list[str]:
        return [restriction.name for restriction in await self._repository.fetch_all_restrictions()]


@dataclass(slots=True, frozen=True)
class DoorSubmissionInput:
    """Framework-neutral input for a door submission."""

    submitter_id: int
    door_size: Dimensions
    record_category: str | None = None
    pattern: tuple[str, ...] = ("Regular",)
    door_type: str = "Door"
    build_size: Dimensions = (None, None, None)
    works_in: str | None = None
    restrictions: tuple[str, ...] = ()
    information_about_build: str | None = None
    normal_closing_time: int | None = None
    normal_opening_time: int | None = None
    date_of_creation: str | None = None
    creators: tuple[str, ...] = ()
    locationality: str | None = None
    directionality: str | None = None
    image_urls: tuple[str, ...] = ()
    video_urls: tuple[str, ...] = ()
    world_download_urls: tuple[str, ...] = ()
    ai_generated: bool = False


@dataclass(slots=True, frozen=True)
class BuildEditPatch:
    """A partial build update which distinguishes omission from clearing."""

    version_spec: PatchValue[str | None] = UNSET
    dimensions: PatchValue[Dimensions] = UNSET
    door_dimensions: PatchValue[Dimensions] = UNSET
    door_type: PatchValue[list[str]] = UNSET
    door_orientation_type: PatchValue[str | None] = UNSET
    wiring_placement_restrictions: PatchValue[list[str]] = UNSET
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
            raise ValueError(msg)
        return cls(**changes)  # pyright: ignore[reportArgumentType]

    def apply(self, build: Build) -> None:
        """Apply the patch after the caller has acquired the build lock."""
        direct_fields = (
            "version_spec",
            "door_type",
            "door_orientation_type",
            "wiring_placement_restrictions",
            "component_restrictions",
            "miscellaneous_restrictions",
            "normal_closing_time",
            "normal_opening_time",
            "creators_ign",
            "image_urls",
            "video_urls",
            "world_download_urls",
            "completion_time",
            "extra_info",
            "ai_generated",
        )
        for name in direct_fields:
            value = getattr(self, name)
            if value is not UNSET:
                setattr(build, name, value)

        if not isinstance(self.dimensions, _Unset):
            build.dimensions = self.dimensions
        if not isinstance(self.door_dimensions, _Unset):
            build.door_dimensions = self.door_dimensions

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
        persist: Callable[[Build], Awaitable[None]],
        build_id: int,
        patch: BuildEditPatch,
        *,
        blocking: bool,
        timeout: float,
    ) -> None:
        self._repository = repository
        self._persist = persist
        self._build_id = build_id
        self._patch = patch
        self._blocking = blocking
        self._timeout = timeout
        self._build: Build | None = None
        self._committed = False

    @property
    def build(self) -> Build:
        if self._build is None:
            msg = "The edit lease has not been entered."
            raise RuntimeError(msg)
        return self._build

    async def __aenter__(self) -> Self:
        build = await self._repository.get_by_id(self._build_id)
        if build is None:
            raise BuildNotFoundError(self._build_id)
        acquired = await self._repository.acquire_lock(
            self._build_id,
            blocking=self._blocking,
            timeout=self._timeout,
        )
        if not acquired:
            raise BuildBusyError(self._build_id)
        self._build = build
        try:
            self._patch.apply(build)
        except BaseException:
            await self._repository.release_lock(self._build_id)
            self._build = None
            raise
        return self

    async def commit(self) -> Build:
        if self._committed:
            msg = "This build edit has already been committed."
            raise RuntimeError(msg)
        build = self.build
        await self._persist(build)
        self._committed = True
        return build

    async def __aexit__(self, *_exc: object) -> None:
        if self._build is not None:
            await self._repository.release_lock(self._build_id)


class BuildService:
    """Framework-free application operations for builds."""

    def __init__(
        self,
        repository: BuildRepository,
        restrictions: RestrictionRepository,
        versions: DefaultVersionResolver,
        embeddings: BuildEmbeddingCoordinator,
    ) -> None:
        self._repository = repository
        self._restrictions = restrictions
        self._versions = versions
        self._embeddings = embeddings

    async def get(self, build_id: int) -> Build | None:
        return await self._repository.get_by_id(build_id)

    async def submit_door(self, submission: DoorSubmissionInput) -> Build:
        build = Build(
            submitter_id=submission.submitter_id,
            ai_generated=submission.ai_generated,
            category=BuildCategory.DOOR,
            submission_status=Status.PENDING,
            record_category=submission.record_category,  # pyright: ignore[reportArgumentType]
            version_spec=submission.works_in,
            width=submission.build_size[0],
            height=submission.build_size[1],
            depth=submission.build_size[2],
            door_width=submission.door_size[0],
            door_height=submission.door_size[1],
            door_depth=submission.door_size[2],
            door_type=list(submission.pattern),
            door_orientation_type=submission.door_type,  # pyright: ignore[reportArgumentType]
            normal_closing_time=submission.normal_closing_time,
            normal_opening_time=submission.normal_opening_time,
            creators_ign=list(submission.creators),
            image_urls=list(submission.image_urls),
            video_urls=list(submission.video_urls),
            world_download_urls=list(submission.world_download_urls),
            completion_time=submission.date_of_creation,
        )
        await self._set_restrictions(build, submission.restrictions)
        for value, empty_value in (
            (submission.locationality, "Not locational"),
            (submission.directionality, "Not directional"),
        ):
            if value is not None and value != empty_value:
                build.miscellaneous_restrictions.append(value)
        if submission.information_about_build is not None:
            build.extra_info["user"] = submission.information_about_build
        await self._persist(build)
        return build

    async def save(self, build: Build) -> Build:
        await self._persist(build)
        return build

    async def classify_restrictions(self, build: Build, restrictions: Sequence[str]) -> Build:
        """Replace a build's restrictions using repository-owned metadata."""
        definitions = await self._restrictions.fetch_all_restrictions()
        build.classify_restrictions(restrictions, {definition.name: definition.type for definition in definitions})
        return build

    async def submit(
        self,
        build: Build,
        *,
        submitter_id: int,
        ai_generated: bool,
        category: BuildCategory = BuildCategory.DOOR,
    ) -> Build:
        """Apply submission metadata and persist an already prepared build."""
        build.submitter_id = submitter_id
        build.ai_generated = ai_generated
        build.category = category
        build.submission_status = Status.PENDING
        await self._persist(build)
        return build

    def edit(
        self,
        build_id: int,
        patch: BuildEditPatch,
        *,
        blocking: bool = False,
        timeout: float = 30,
    ) -> BuildEditLease:
        return BuildEditLease(
            self._repository,
            self._persist,
            build_id,
            patch,
            blocking=blocking,
            timeout=timeout,
        )

    async def confirm(self, build_id: int) -> Build:
        build = await self._get_required(build_id)
        await self._repository.confirm(build)
        return build

    async def deny(self, build_id: int) -> Build:
        build = await self._get_required(build_id)
        await self._repository.deny(build)
        return build

    async def refresh_record_titles(self) -> None:
        await self._repository.update_smallest_door_records_without_title()

    async def _get_required(self, build_id: int) -> Build:
        build = await self._repository.get_by_id(build_id)
        if build is None:
            raise BuildNotFoundError(build_id)
        return build

    async def _persist(self, build: Build) -> None:
        if not build.versions:
            build.versions = [await self._versions.newest("Java")]
        await self._embeddings.prepare(build)
        await self._repository.save(build)
        await self._embeddings.index(build)

    async def _set_restrictions(self, build: Build, restrictions: Sequence[str]) -> None:
        known_restrictions = await self._restrictions.fetch_all_restrictions()
        build.classify_restrictions(
            restrictions,
            {restriction.name: restriction.type for restriction in known_restrictions},
        )
