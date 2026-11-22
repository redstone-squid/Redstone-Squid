from types import SimpleNamespace
from typing import cast

import aiohttp
import discord
import pytest
import wrapture

from tests.e2e.discord.environment import command_environment, displayed_text


@pytest.mark.parametrize(("command", "heading"), [("bug", "Bug report"), ("suggestion", "Suggestion")])
async def test_feedback_posts_publicly_and_only_pings_owner(command: str, heading: str) -> None:
    async with command_environment("squid.bot.feedback_cog") as (bot, discord_env):
        guild = discord_env.guild
        channel = guild.create_text_channel("feedback")
        reporter, owner, bystander = (
            guild.add_member(discord_env.create_user(name)) for name in ("reporter", "owner", "bystander")
        )
        bot.owner_id = owner.id
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


@pytest.mark.parametrize("in_guild", [True, False])
@pytest.mark.parametrize("dm_fails", [True, False])
async def test_feedback_falls_back_to_dm_and_reports_delivery_failure(in_guild: bool, dm_fails: bool) -> None:
    async with command_environment("squid.bot.feedback_cog") as (bot, env):
        guild = env.guild
        reporter = env.create_user("reporter")
        owner = env.create_user("owner")
        bot.owner_id = owner.id
        owner_dm = owner.dm_channel
        send_dm = wrapture.binding(discord.User, "send")
        if dm_fails:
            response = cast(aiohttp.ClientResponse, SimpleNamespace(status=403, reason="Forbidden"))
            send_dm.on_call.raises(discord.Forbidden(response, "Cannot send messages"))

        with wrapture.timeline(send_dm):
            if in_guild:
                channel = guild.create_text_channel("feedback")
                result = await guild.add_member(reporter).slash(channel, "bug", message="Something broke")
            else:
                channel = reporter.dm_channel
                result = await reporter.slash("bug", message="Something broke")
            send_dm.events.finished().assert_once()
            send_dm.events.raising(discord.Forbidden).assert_times(int(dm_fails))

        post = channel.last_message
        assert post is not None
        assert post.interaction_metadata is None
        assert displayed_text(post) == ["Bug report: Something broke"]

        if dm_fails:
            assert owner_dm.last_message is None
            assert result.response is not None
            assert result.response.ephemeral
            assert "Your feedback was posted, but I couldn't DM the bot owner." in result.response.message.content
        else:
            assert result.response is None
            assert any(
                call.method == "DELETE" and call.path.endswith("/messages/@original") for call in env.http_requests
            )
            forwarded = owner_dm.last_message
            assert forwarded is not None
            context = (
                f"Server: {guild.name}\nhttps://discord.com/channels/{guild.id}/{channel.id}"
                if in_guild
                else "Submitted in a direct message."
            )
            assert displayed_text(forwarded) == [f"Bug report: Something broke\n\n{context}"]
            (send,) = (
                call
                for call in env.http_requests
                if call.method == "POST" and call.path == f"/channels/{owner_dm.id}/messages"
            )
            assert send.json is not None
            assert send.json["allowed_mentions"] == {"parse": []}
