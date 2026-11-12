"""Settings application service tests."""

from functools import partial

from squid.settings.application import SettingsService
from squid.settings.models import ServerSetting
from tests.support.settings import FakeSettingsUnitOfWork, settings_state


async def test_membership_changes_commit_without_clearing_settings() -> None:
    committed = settings_state()
    service = SettingsService(uow_factory=partial(FakeSettingsUnitOfWork, committed))
    await service.guild_removed(1)
    assert committed.value == {}
    await service.guild_joined(1)
    assert committed.value[1].in_server
    committed.value[1].locale = "fr"
    await service.guild_removed(1)
    assert not committed.value[1].in_server
    assert committed.value[1].locale == "fr"
    await service.guild_joined(1)
    assert committed.value[1].in_server
    assert committed.value[1].locale == "fr"


async def test_reads_preserve_missing_server_semantics() -> None:
    committed = settings_state({1: ServerSetting(server_id=1, voting_channel_id=123), 2: ServerSetting(server_id=2)})
    service = SettingsService(uow_factory=partial(FakeSettingsUnitOfWork, committed))
    assert await service.get_many(iter([1, 2, 3]), "Vote") == {1: 123, 2: None}
    assert await service.get(3, "Vote") is None
    assert await service.get_locale(3) is None
    assert await service.get_all(3) == {}
    assert await service.get_all(1) == {"Smallest": None, "Fastest": None, "First": None, "Builds": None, "Vote": 123}
    assert set(committed.value) == {1, 2}
