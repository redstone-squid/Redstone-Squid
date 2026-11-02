"""Settings test doubles that capture entity state when the service commits."""

from collections.abc import Iterable, Sequence
from dataclasses import replace
from types import TracebackType
from typing import Self

from squid.settings.models import ServerSetting


class FakeSettingsRepository:
    def __init__(self, rows: dict[int, ServerSetting]) -> None:
        self.rows = rows

    async def get(self, server_id: int, *, for_update: bool = False) -> ServerSetting | None:
        return self.rows.get(server_id)

    async def get_many(self, server_ids: Iterable[int]) -> Sequence[ServerSetting]:
        return [self.rows[server_id] for server_id in dict.fromkeys(server_ids) if server_id in self.rows]

    async def get_or_create_for_update(self, server_id: int) -> ServerSetting:
        return self.rows.setdefault(server_id, ServerSetting(server_id=server_id))


class FakeSettingsUnitOfWork:
    def __init__(self, committed: dict[int, ServerSetting]) -> None:
        self._committed = committed
        self.settings = FakeSettingsRepository({key: replace(row) for key, row in committed.items()})

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        pass

    async def commit(self) -> None:
        self._committed.clear()
        self._committed.update({key: replace(row) for key, row in self.settings.rows.items()})
