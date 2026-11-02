"""Settings application service tests."""

from functools import partial

import pytest

from squid.settings.application import SettingsService
from squid.settings.domain import Setting
from squid.settings.models import ServerSetting
from tests.support.settings import FakeSettingsUnitOfWork, settings_state


@pytest.mark.parametrize("setting", ["Smallest", "Fastest", "First", "Builds", "Vote"])
async def test_setting_and_clearing_channels_commits_their_values(setting: Setting) -> None:
    committed = settings_state()
    service = SettingsService(uow_factory=partial(FakeSettingsUnitOfWork, committed))

    await service.set_channel(1, setting, 123)
    assert await service.get(1, setting) == 123
    await service.clear(1, setting)
    assert await service.get(1, setting) is None


@pytest.mark.parametrize("locale", ["zh-CN", None])
async def test_settings_locale_write_commits_the_tracked_entity(locale: str | None) -> None:
    committed = settings_state({1: ServerSetting(server_id=1, locale="en")})
    service = SettingsService(uow_factory=partial(FakeSettingsUnitOfWork, committed))

    await service.set_locale(1, locale)

    assert committed.value[1].locale == locale


async def test_membership_changes_commit_without_clearing_settings() -> None:
    committed = settings_state()
    service = SettingsService(uow_factory=partial(FakeSettingsUnitOfWork, committed))
    await service.guild_removed(1)
    assert committed.value == {}
    await service.guild_joined(1)
    assert committed.value[1].in_server
    await service.set_locale(1, "fr")
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
