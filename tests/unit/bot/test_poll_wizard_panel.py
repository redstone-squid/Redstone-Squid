"""The Wizard-driven poll composition screen."""

from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import AsyncMock

import discord

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.voting.poll_wizard import PollDraft, PollScreen
from squid.voting.domain import PollScope, VoteVisibility
from squid.voting.errors import InvalidVoteConfigurationError
from squid_ui.testing import labels
from squid_ui_discord.testing import interaction_harness
from squid_ui_widgets import testing as wt
from tests.support.discord import make_layout_bot
from tests.support.voting import GENERIC_OPTIONS

OWNER_ID = 11


@dataclass(frozen=True, slots=True)
class Guild:
    id: int = 7


def make_screen(*, failure: Exception | None = None) -> PollScreen:
    resolve = AsyncMock(side_effect=failure, return_value=GENERIC_OPTIONS)
    publish = AsyncMock(return_value="https://discord.invalid/channels/1/2/3")
    return PollScreen(resolve, publish, allow_network=True)


async def open_screen(
    screen: PollScreen,
    *,
    message: discord.Message | None = None,
) -> tuple[PollScreen, sd.MessageRoot, Any]:
    bot = make_layout_bot()
    interaction = interaction_harness(user_id=OWNER_ID)
    interaction.client = bot
    interaction.guild = Guild()
    interaction.guild_locale = None
    interaction.locale = "en-US"
    if message is not None:
        interaction.original_response.return_value = message
    outcome = await bot.app_ui.respond(interaction, screen)
    assert isinstance(outcome, sd.Presented)
    sessions = bot.sessions.get(sd.SessionKey.user_guild("poll-wizard", OWNER_ID, 7))
    assert len(sessions) == 1
    return outcome.component, sessions[0].root, interaction


async def complete_wizard(screen: PollScreen) -> wt.MachineHarness[sp.WizardState, sl.ComponentsV2Target]:
    harness = wt.driving(screen.driver)
    await harness.submit("poll.content", {"question": "Best door?", "options": "One\nTwo"})
    await harness.submit(
        "poll.settings",
        {
            "visibility": VoteVisibility.ANONYMOUS_LIVE,
            "duration": 12 * 3600,
            "scope": PollScope.GUILD,
        },
    )
    assert harness.state.current == sp.REVIEW_STEP
    return harness


async def test_scheduler_backed_wizard_renews_its_private_session() -> None:
    _, message_root, _ = await open_screen(make_screen())

    assert PollScreen.timeout == 900
    assert message_root.scheduler is not None
    assert isinstance(message_root.expiry, sd.RenewEphemeral)


async def test_wizard_finishes_publication_once() -> None:
    screen = make_screen()
    harness = await complete_wizard(screen)

    await harness.press("poll.finish")
    await harness.press("poll.finish")

    cast(AsyncMock, screen._resolve_options).assert_awaited_once_with(("One", "Two"))
    cast(AsyncMock, screen._publish).assert_awaited_once_with(
        PollDraft(
            question="Best door?",
            options_text="One\nTwo",
            visibility=VoteVisibility.ANONYMOUS_LIVE,
            duration_seconds=12 * 3600,
            scope=PollScope.GUILD,
        ),
        GENERIC_OPTIONS,
    )
    assert screen.published_url is not None


async def test_invalid_options_return_the_wizard_to_review() -> None:
    screen = make_screen(failure=InvalidVoteConfigurationError("bad options"))
    harness = await complete_wizard(screen)

    await harness.press("poll.finish")

    assert harness.state.current == sp.REVIEW_STEP
    assert harness.state.complete is False
    cast(AsyncMock, screen._publish).assert_not_awaited()


def test_settings_step_uses_typed_portable_fields() -> None:
    screen = make_screen()
    settings = screen.wizard.live_steps(screen.wizard.initial_state)[1]
    assert settings.form is not None

    fields = {field.key: field for field in settings.form.items if isinstance(field, sl.forms.FormField)}

    assert isinstance(fields["visibility"], sl.forms.ChoiceField)
    assert isinstance(fields["duration"], sl.forms.DurationField)
    assert isinstance(fields["scope"], sl.forms.ChoiceField)
    assert fields["duration"].parse("12h") == 12 * 3600


async def test_cancelling_finishes_with_a_terminal_screen() -> None:
    screen, message_root, _opening = await open_screen(make_screen())

    await message_root.dispatch("cancel", interaction_harness(user_id=OWNER_ID))

    assert screen.cancelled is True
    assert labels(screen.render()) == []
