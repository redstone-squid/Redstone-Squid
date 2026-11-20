from tests.e2e.discord.environment import command_environment, displayed_text


async def test_help_browser_opens_command_and_returns_to_directory() -> None:
    async with command_environment("squid.bot.help_cog", "squid.bot.feedback_cog") as (_, discord_env):
        guild = discord_env.guild
        channel = guild.create_text_channel("help")
        member = guild.add_member(discord_env.create_user("reader"))

        choices = await member.autocomplete(channel, "help", "command", "bug")
        assert {"name": "bug", "value": "bug"} in choices
        opened = await member.slash(channel, "help")
        assert opened.response is not None
        assert not opened.response.ephemeral
        directory = displayed_text(opened.response.message)
        assert "## Redstone Squid help" in directory
        assert any("`/bug`" in text for text in directory)

        focused = await member.select(opened.response.message, ["bug"])
        assert focused.response is not None
        assert focused.response.id == opened.response.id
        assert "## /bug" in displayed_text(focused.response.message)

        back = await member.click(focused.response.message, label="Back")
        assert back.response is not None
        assert back.response.id == opened.response.id
        assert displayed_text(back.response.message) == directory


async def test_prefix_help_displays_a_slash_commands_description() -> None:
    async with command_environment("squid.bot.help_cog", "squid.bot.feedback_cog") as (_, discord_env):
        guild = discord_env.guild
        channel = guild.create_text_channel("help")
        member = guild.add_member(discord_env.create_user("reader"))

        await member.send(channel, "!help bug")

        reply = channel.last_message
        assert reply is not None
        assert "Report a bug with the bot. Your message will be public." in displayed_text(reply)
