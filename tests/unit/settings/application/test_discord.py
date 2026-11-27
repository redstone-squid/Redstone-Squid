"""Naming and checking the Discord IDs that runtime settings hold."""

from squid.core.settings.application.discord import RuntimeDiscordReferences
from squid.core.settings.domain.runtime import RuntimeSettings
from squid.core.validation import RuleIssue, RuleParams
from tests.support.discord_directory import (
    LOUNGE,
    OWNER_GUILD,
    PARTNER_LOG,
    REDSTONER,
    STARBOARD_BOT,
    WELCOME,
    FakeDirectory,
)


def references(directory: FakeDirectory | None = None) -> RuntimeDiscordReferences:
    return RuntimeDiscordReferences(directory or FakeDirectory(), owner_guild_id=OWNER_GUILD)


async def test_names_cover_channels_in_other_guilds_and_skip_unknown_ids() -> None:
    settings = RuntimeSettings(
        welcome_channel_id=WELCOME.id,
        build_log_channel_ids=(PARTNER_LOG.id, 999),
        redstoner_role_id=REDSTONER.id,
        redstoner_starboard_author_id=STARBOARD_BOT.id,
    )

    names = await references().names(settings)

    assert names is not None
    assert names[WELCOME.id] == "welcome"
    assert names[PARTNER_LOG.id] == "partner-builds"
    assert names[REDSTONER.id] == "Redstoner"
    assert names[STARBOARD_BOT.id] == "Starboard"
    assert 999 not in names


async def test_names_are_unknown_rather_than_failing_while_discord_is_down() -> None:
    assert await references(FakeDirectory(available=False)).names(RuntimeSettings()) is None
    assert await RuntimeDiscordReferences(None, owner_guild_id=None).names(RuntimeSettings()) is None


async def test_a_role_in_a_channel_field_and_a_category_are_rejected() -> None:
    before = RuntimeSettings(welcome_channel_id=WELCOME.id)
    after = before.model_copy(
        update={"welcome_channel_id": REDSTONER.id, "version_tracker_channel_id": LOUNGE.id, "redstoner_role_id": 5}
    )

    issues = await references().issues(before, after)

    assert issues == (
        RuleIssue(field="redstoner_role_id", params=RuleParams(rule="discord_role", reason="unknown")),
        RuleIssue(field="welcome_channel_id", params=RuleParams(rule="discord_channel", reason="unknown")),
        RuleIssue(field="version_tracker_channel_id", params=RuleParams(rule="discord_channel", reason="category")),
    )


async def test_unchanged_ids_and_outages_never_block_an_edit() -> None:
    stale = RuntimeSettings(welcome_channel_id=999)
    edited = stale.model_copy(update={"welcome_enabled": False})

    assert await references().issues(stale, edited) == ()
    moved = stale.model_copy(update={"welcome_channel_id": 998})
    assert await references(FakeDirectory(available=False)).issues(stale, moved) == ()
