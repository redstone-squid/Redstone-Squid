import discord
import pytest
from discord.ext import commands

from tests.e2e.discord.environment import cog_environment


@pytest.mark.parametrize(("command", "heading"), [("bug", "Bug report"), ("suggestion", "Suggestion")])
async def test_feedback_posts_publicly_and_only_pings_owner(command: str, heading: str) -> None:
    async with cog_environment() as discord_env:
        guild = discord_env.guild
        channel = guild.create_text_channel("feedback")
        reporter, owner, bystander = (
            guild.add_member(discord_env.create_user(name)) for name in ("reporter", "owner", "bystander")
        )
        assert isinstance(discord_env.bot, commands.Bot)
        discord_env.bot.owner_id = owner.id
        body = f"Please fix @everyone <@{bystander.id}>"

        result = await reporter.slash(channel, command, message=body)

        assert result.deferred
        assert result.ephemeral
        assert result.response is None
        assert any(
            call.method == "DELETE" and call.path.endswith("/messages/@original") for call in discord_env.http_requests
        )
        post = channel.last_message
        assert post is not None
        assert not post.flags.ephemeral
        assert post.interaction_metadata is None
        (display,) = post.components
        assert isinstance(display, discord.TextDisplay)
        assert display.content == f"<@{owner.id}>\n{heading}: Please fix @\u200beveryone <@\u200b{bystander.id}>"
        (send,) = (
            call
            for call in discord_env.http_requests
            if call.method == "POST" and call.path == f"/channels/{channel.id}/messages"
        )
        assert send.json is not None
        assert send.json["allowed_mentions"] == {"users": [owner.id], "parse": []}
