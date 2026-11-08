"""Settings test doubles that capture entity state when the service commits."""

from collections.abc import Collection, Iterable, Sequence
from dataclasses import replace

from squid.settings.models import ServerSetting
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork


class FakeSettingsRepository:
    def __init__(self, rows: dict[int, ServerSetting]) -> None:
        self.rows = rows

    async def get(self, server_id: int, *, for_update: bool = False) -> ServerSetting | None:
        return self.rows.get(server_id)

    async def get_many(self, server_ids: Iterable[int]) -> Sequence[ServerSetting]:
        return [self.rows[server_id] for server_id in dict.fromkeys(server_ids) if server_id in self.rows]

    async def get_or_create_for_update(self, server_id: int) -> ServerSetting:
        return self.rows.setdefault(server_id, ServerSetting(server_id=server_id))

    async def sync_presence(self, present_server_ids: Collection[int]) -> tuple[Sequence[int], Sequence[int]]:
        joined = [server_id for server_id in present_server_ids if server_id not in self.rows]
        left: list[int] = []
        for server_id in joined:
            self.rows[server_id] = ServerSetting(server_id=server_id)
        for row in self.rows.values():
            present = row.server_id in present_server_ids
            if row.in_server != present:
                (joined if present else left).append(row.server_id)
                row.in_server = present
        return sorted(joined), sorted(left)


def settings_state(rows: dict[int, ServerSetting] | None = None) -> CommittedState[dict[int, ServerSetting]]:
    return CommittedState(rows if rows is not None else {}, clone=_copy_settings)


def _copy_settings(rows: dict[int, ServerSetting]) -> dict[int, ServerSetting]:
    return {key: replace(row) for key, row in rows.items()}


class FakeSettingsUnitOfWork(FakeUnitOfWork[dict[int, ServerSetting]]):
    @property
    def settings(self) -> FakeSettingsRepository:
        return FakeSettingsRepository(self.state)
