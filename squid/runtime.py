"""Framework-neutral application services and process runtime."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from squid.builds.application import BuildInferenceService, BuildQueryService, BuildService, RestrictionService
from squid.community.application import RedstonerService, WelcomeRelayService
from squid.messages.application import MessageService
from squid.records.application import RecordComputationService, RecordService
from squid.search.application import SearchService
from squid.settings.application import SettingsService
from squid.tags.application import TagService
from squid.users.application import UserService
from squid.versions.application.services import VersionService
from squid.voting.application import VoteService


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    """Long-lived application services created by the process composition root."""

    builds: BuildService
    build_inference: BuildInferenceService
    restrictions: RestrictionService
    build_queries: BuildQueryService
    messages: MessageService
    records: RecordService
    record_computation: RecordComputationService
    search: SearchService
    tags: TagService
    refresh_search_index: Callable[[], Awaitable[tuple[int, int]]]
    settings: SettingsService
    users: UserService
    versions: VersionService
    votes: VoteService
    redstoner: RedstonerService
    welcome_relay: WelcomeRelayService


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    """Own application services and process-level resource callbacks."""

    services: ApplicationServices
    close_resources: Callable[[], Awaitable[None]]
    keep_database_active: Callable[[], Awaitable[None]]

    async def close(self) -> None:
        await self.close_resources()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
