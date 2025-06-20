from dataclasses import dataclass, field
from typing import Any, Sequence, Mapping, Final

from squid.db.builds import frozen_field
from squid.db.schema import Status, BuildCategory, RecordCategory, DoorOrientationName, Info


@dataclass(kw_only=True)
class Build:
    id: int | None = None
    submission_status: Status | None = None
    record_category: RecordCategory | None = None
    versions: list[str] = field(default_factory=list)
    version_spec: str | None = None

    width: int | None = None
    height: int | None = None
    depth: int | None = None

    wiring_placement_restrictions: list[str] = field(default_factory=list)
    component_restrictions: list[str] = field(default_factory=list)
    miscellaneous_restrictions: list[str] = field(default_factory=list)

    extra_info: Info = field(default_factory=Info)
    creators_ign: list[str] = field(default_factory=list)

    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    world_download_urls: list[str] = field(default_factory=list)

    submitter_id: int | None = None
    # TODO: save the submitted time too
    completion_time: str | None = None
    edited_time: str | None = None

    original_message: Message | None = None

    ai_generated: bool | None = None
    embedding: list[float] | None = field(default=None, repr=False)

@dataclass(kw_only=True)
class Door(Build):
    door_width: int | None = None
    door_height: int | None = None
    door_depth: int | None = None

    door_type: list[str] = field(default_factory=list)
    door_orientation_type: DoorOrientationName | None = None

    normal_closing_time: int | None = None
    normal_opening_time: int | None = None
    visible_closing_time: int | None = None
    visible_opening_time: int | None = None

@dataclass(kw_only=True)
class Extender(Build):
    pass

@dataclass(kw_only=True)
class Utility(Build):
    pass

@dataclass(kw_only=True)
class Entrance(Build):
    pass


class BuildRepository:
    """Repository for Build persistence and queries."""

    async def get_by_id(self, build_id: int) -> Any: ...
    async def get_by_message_id(self, message_id: int) -> Any: ...
    async def get_by_ids(self, build_ids: list[int]) -> list[Any]: ...
    async def get_by_filter(self, filter: Mapping[str, Any] | None = None) -> list[Any]: ...
    async def save(self, build: Any) -> None: ...
    async def delete(self, build_id: int) -> None: ...
    async def get_unsent_builds(self, server_id: int) -> list[Any] | None: ...

class BuildService:
    """Service for Build domain logic and orchestration."""

    async def create_build(self, data: dict[str, Any]) -> Any: ...
    async def update_build(self, build_id: int, data: dict[str, Any]) -> Any: ...
    async def confirm_build(self, build_id: int) -> None: ...
    async def deny_build(self, build_id: int) -> None: ...
    async def generate_embedding(self, build_id: int) -> list[float] | None: ...
    async def set_restrictions(self, build_id: int, restrictions: Sequence[str] | Mapping[str, Sequence[str]]) -> None: ...
    async def get_title(self, build_id: int) -> str: ...
    async def ai_generate_from_message(self, message: Any, *, prompt_path: str = "prompt.txt", model: str = "gpt-4.1-nano") -> Any: ... 