"""Server settings application ports."""

from collections.abc import Collection, Iterable, Sequence
from typing import Protocol

from squid.core.settings.domain.runtime import RuntimeSettings
from squid.core.settings.models import RuntimeSetting, ServerSetting
from squid.core.unit_of_work import UnitOfWork
from squid.core.voting.application.ports import VoteRepository


class SettingsRepository(Protocol):
    """Settings entities attached to the caller's transaction."""

    async def runtime(self, defaults: RuntimeSettings, *, for_update: bool = False) -> RuntimeSetting: ...

    async def record_runtime_change(
        self, actor_account_id: int, before: RuntimeSettings, after: RuntimeSettings
    ) -> None: ...

    async def get(self, server_id: int, *, for_update: bool = False) -> ServerSetting | None: ...

    async def get_many(self, server_ids: Iterable[int]) -> Sequence[ServerSetting]: ...

    async def get_or_create_for_update(self, server_id: int) -> ServerSetting: ...

    async def sync_presence(self, present_server_ids: Collection[int]) -> tuple[Sequence[int], Sequence[int]]: ...


class SettingsUnitOfWork(UnitOfWork, Protocol):
    """Transaction and repositories used by SettingsService."""

    @property
    def settings(self) -> SettingsRepository: ...


class GuildSettingsUnitOfWork(SettingsUnitOfWork, Protocol):
    @property
    def votes(self) -> VoteRepository: ...
