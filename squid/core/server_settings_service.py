from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(slots=True)
class ServerSettings:
    """Settings for a Discord server."""

    server_id: int

    smallest_channel_id: int | None = None
    fastest_channel_id: int | None = None
    first_channel_id: int | None = None
    builds_channel_id: int | None = None
    voting_channel_id: int | None = None

    staff_roles_ids: list[int] = field(default_factory=list)
    trusted_roles_ids: list[int] = field(default_factory=list)

    in_server: bool


class ServerSettingsRepository:
    """Repository for ServerSettings persistence and queries."""

    async def get(self, server_ids: Iterable[int], setting: str) -> dict[int, Any]: ...
    async def get_single(self, server_id: int, setting: str) -> Any: ...
    async def get_all(self, server_id: int) -> dict[str, Any]: ...
    async def set(self, server_id: int, setting: str, value: Any) -> None: ...
    async def set_all(self, server_id: int, settings: dict[str, Any]) -> None: ...


class ServerSettingsService:
    """Service for ServerSettings domain logic and orchestration."""

    async def get_setting(self, server_id: int, setting: str) -> Any: ...
    async def set_setting(self, server_id: int, setting: str, value: Any) -> None: ...
    async def get_all_settings(self, server_id: int) -> dict[str, Any]: ...
    async def set_multiple_settings(self, server_id: int, settings: dict[str, Any]) -> None: ...
