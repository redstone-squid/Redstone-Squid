"""Settings test doubles that capture entity state when the service commits."""

from collections.abc import Iterable, Sequence
from dataclasses import replace

from squid.settings.models import ServerSetting
from tests.support.unit_of_work import FakeUnitOfWork


class FakeSettingsRepository:
    def __init__(self, rows: dict[int, ServerSetting]) -> None:
        self.rows = rows

    async def get(self, server_id: int, *, for_update: bool = False) -> ServerSetting | None:
        return self.rows.get(server_id)

    async def get_many(self, server_ids: Iterable[int]) -> Sequence[ServerSetting]:
        return [self.rows[server_id] for server_id in dict.fromkeys(server_ids) if server_id in self.rows]

    async def get_or_create_for_update(self, server_id: int) -> ServerSetting:
        return self.rows.setdefault(server_id, ServerSetting(server_id=server_id))


class FakeSettingsUnitOfWork(FakeUnitOfWork):
    def __init__(self, committed: dict[int, ServerSetting]) -> None:
        self._committed = committed
        self._settings = FakeSettingsRepository({key: replace(row) for key, row in committed.items()})

    @property
    def settings(self) -> FakeSettingsRepository:
        return self._settings

    async def commit(self) -> None:
        self._committed.clear()
        self._committed.update({key: replace(row) for key, row in self.settings.rows.items()})
