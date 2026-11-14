"""Collection edits preserve identities and validate a complete recipe before saving."""

from unittest.mock import AsyncMock, Mock

import discord
import pytest

import squid_ui_discord as sd
from squid.bot.settings.vote_emoji_editor import VoteEmojiEditor
from squid.bot.submission.video_editor import VideoEditor
from squid.schematics.application.video import VideoInteraction, VideoRenderRequest
from squid.voting.domain import VoteChoice, VoteKind, VoteOption
from squid.voting.errors import InvalidVoteConfigurationError
from squid_ui_discord.testing import stage


async def test_vote_row_edit_preserves_option_identity_and_weight() -> None:
    before = VoteOption("👍", VoteChoice.GENERIC, identifier="stable", label="Yes", multiplier=2, guild_id=7)
    screen = VoteEmojiEditor(Mock(spec=discord.Guild, id=7), VoteKind.GENERIC, (before,))
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("edit-0"))
        await form.submit({"emoji": "✅", "label": "Proceed"})
        option = screen.options[0]
        assert (option.emoji, option.label) == ("✅", "Proceed")
        assert (option.identifier, option.multiplier, option.guild_id) == ("stable", 2, 7)
    with pytest.raises(InvalidVoteConfigurationError, match="between 2 and 10"):
        await screen._save(Mock(spec=sd.PressEvent))


async def test_video_input_selection_preserves_camera_and_explicit_activations() -> None:
    recipe = VideoRenderRequest(
        width=800,
        height=600,
        yaw=90,
        pitch=20,
        zoom=2,
        interactions=(VideoInteraction(tick=10), VideoInteraction(tick=30, position=(1, 2, 3))),
    )
    submit = AsyncMock()
    screen = VideoEditor(recipe, candidates=((4, 5, 6),), submit=submit)
    await screen._use_input((4, 5, 6), Mock(spec=sd.PressEvent))
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("render"))
        submit.assert_awaited_once()
        result = submit.call_args.args[1]
        assert result.interactions == (VideoInteraction(tick=10, position=(4, 5, 6)), recipe.interactions[1])
        assert (result.width, result.height, result.yaw, result.pitch, result.zoom) == (800, 600, 90, 20, 2)


async def test_video_rejects_partial_coordinates_and_activations_after_clip() -> None:
    submit = AsyncMock()
    screen = VideoEditor(VideoRenderRequest(), submit=submit)
    event = Mock(spec=sd.SubmitEvent, values={"tick": 1, "x": 2}, notice=AsyncMock())
    await screen._add(event)
    event.notice.assert_awaited_once()
    assert screen.interactions == (VideoInteraction(tick=20),)
    screen.seconds = 1
    press = Mock(spec=sd.PressEvent, notice=AsyncMock())
    await screen._apply(press)
    press.notice.assert_awaited_once()
    submit.assert_not_awaited()


async def test_static_video_omits_saved_activation_rows() -> None:
    submit = AsyncMock()
    screen = VideoEditor(VideoRenderRequest(), submit=submit)
    screen.simulate = False
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("render"))
    result = submit.call_args.args[1]
    assert result.interactions == ()
    assert not result.simulation_enabled
