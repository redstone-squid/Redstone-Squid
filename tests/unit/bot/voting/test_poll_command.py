"""Slash arguments and the editor share publication settings and authorization."""

import inspect
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.voting.cog import VoteCog
from squid.bot.voting.poll_wizard import PollDraft, PollScreen, parse_option_lines
from squid.bot.voting.publisher import DiscordPollPublisher
from squid.core.actors import UserActor
from squid.core.voting.domain import PollScope, VoteVisibility
from tests.support.voting import GENERIC_MENU


@pytest.fixture
def poll_context() -> tuple[Any, Any]:
    cog = VoteCog.__new__(VoteCog)
    account = SimpleNamespace(id=7, needs_consent_refresh=False)
    cast(Any, cog).bot = SimpleNamespace(
        actors=SimpleNamespace(resolve=AsyncMock(return_value=UserActor(account_id=7, guild_id=10))),
        services=SimpleNamespace(
            accounts=SimpleNamespace(get_account_by_identity=AsyncMock(return_value=account)),
            permissions=SimpleNamespace(check=AsyncMock(return_value=SimpleNamespace(allowed=True))),
        ),
    )
    cast(Any, cog).publisher = SimpleNamespace(
        may_create_network=AsyncMock(return_value=True),
        resolve_options=AsyncMock(return_value=GENERIC_MENU),
        create_and_publish=AsyncMock(
            return_value=SimpleNamespace(message=SimpleNamespace(jump_url="https://discord.com/channels/1/2/3"))
        ),
    )
    request = SimpleNamespace(user=MagicMock(spec=discord.Member), guild=SimpleNamespace(id=10), channel=object())
    request.user.id = 11
    return cog, request


async def invoke(cog: Any, request: Any, **kwargs: object) -> sd.CommandResult:
    # Exercise the application handler; transport deferral belongs to sd.command.
    return await inspect.unwrap(cog.poll.callback)(cog, request, **kwargs)


async def test_preview_and_direct_publication_have_identical_settings(poll_context: tuple[Any, Any]) -> None:
    cog, request = poll_context
    arguments = {
        "question": r"Best door?\nChoose one",
        "choices": "Red, Blue",
        "duration": "2h15m",
        "visibility": "anonymous_hidden",
        "scope": "network",
    }
    screen = await invoke(cog, request, **arguments, preview=True)
    assert isinstance(screen, PollScreen)
    assert screen.driver.machine_state.current == sp.REVIEW_STEP
    cog.publisher.create_and_publish.assert_not_awaited()

    async with sd.testing.stage(screen, owner=object()) as ui:
        await ui.press(next(key for key in ui.root.snapshot().handler_keys if key.endswith("poll.finish")))
        assert isinstance(screen.outcome, sd.Completed)
    from_preview = cog.publisher.create_and_publish.call_args
    assert from_preview.kwargs["question"] == "Best door?\nChoose one"
    assert from_preview.kwargs["duration_seconds"] == 8100
    assert from_preview.kwargs["visibility"] is VoteVisibility.ANONYMOUS_HIDDEN
    assert from_preview.kwargs["scope"] is PollScope.NETWORK

    await invoke(cog, request, **arguments)
    assert cog.publisher.create_and_publish.call_count == 2
    assert cog.publisher.create_and_publish.call_args == from_preview


async def test_no_question_opens_editor_and_preserves_other_arguments(poll_context: tuple[Any, Any]) -> None:
    cog, request = poll_context
    screen = await invoke(cog, request, choices="Red, Blue", duration="3 days", visibility="visible_live")
    assert isinstance(screen, PollScreen)
    assert screen.driver.machine_state.current == "content"
    answers = screen.wizard.live_answers(screen.driver.machine_state)
    assert answers["settings"]["duration"] == 259200
    assert answers["settings"]["visibility"] is VoteVisibility.VISIBLE_LIVE
    content = screen.wizard.live_steps(screen.driver.machine_state)[0].form
    assert content is not None
    assert (
        next(
            field for field in content.items if isinstance(field, sl.forms.FormField) and field.key == "options"
        ).default
        == "Red, Blue"
    )
    cog.publisher.create_and_publish.assert_not_awaited()


async def test_network_permission_is_rechecked_at_publish(poll_context: tuple[Any, Any]) -> None:
    cog, request = poll_context
    screen = await invoke(cog, request, question="Ready?", scope="network", preview=True)
    assert isinstance(screen, PollScreen)
    cog.bot.services.permissions.check.return_value = SimpleNamespace(allowed=False)

    async with sd.testing.stage(screen, owner=object()) as ui:
        await ui.press(next(key for key in ui.root.snapshot().handler_keys if key.endswith("poll.finish")))
        assert screen.outcome is None
        assert not screen.driver.machine_state.complete
    cog.publisher.create_and_publish.assert_not_awaited()


def test_slash_options_cover_every_draft_setting() -> None:
    parameters = {parameter.name: parameter for parameter in cast(Any, VoteCog).poll.parameters}
    assert set(parameters) == {"question", "choices", "visibility", "duration", "scope", "preview"}
    assert {choice.value for choice in parameters["visibility"].choices} == set(VoteVisibility)
    assert {choice.value for choice in parameters["scope"].choices} == set(PollScope)
    assert all(not parameter.required for parameter in parameters.values())


def test_default_choices_do_not_need_a_configured_palette() -> None:
    menu = parse_option_lines(PollDraft("Ready?").option_lines, guild_id=10, palette=())
    assert [(alias.emoji, option.label) for option, alias in menu.rows()] == [("👍", "Yes"), ("👎", "No")]


@pytest.mark.parametrize(
    ("available", "usable", "expected"), [(True, True, True), (True, False, False), (False, False, False)]
)
def test_custom_emojis_are_resolved_across_the_bots_servers(available: bool, usable: bool, expected: bool) -> None:
    emoji = MagicMock()
    emoji.is_usable.return_value = usable
    bot = MagicMock()
    bot.get_emoji.return_value = emoji if available else None

    assert DiscordPollPublisher(bot)._emoji_is_usable("<:door:123456789012345678>") is expected
    bot.get_emoji.assert_called_once_with(123456789012345678)
