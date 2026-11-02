"""Server settings application ports."""

from collections.abc import Iterable, Sequence
from typing import Protocol

from squid.settings.models import ServerSetting


class SettingsStore(Protocol):
    """Settings entities attached to the caller's transaction."""

    async def get(self, server_id: int, *, for_update: bool = False) -> ServerSetting | None: ...

    async def get_many(self, server_ids: Iterable[int]) -> Sequence[ServerSetting]: ...

    async def get_or_create_for_update(self, server_id: int) -> ServerSetting: ...
