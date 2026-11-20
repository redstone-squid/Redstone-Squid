import discord

from tests.e2e.discord.environment import command_environment, displayed_text


async def test_layout_prefix_group_lists_its_three_showcase_commands() -> None:
    async with command_environment("squid.bot.help_cog", "squid.bot.showcase.cog", development_mode=True) as (_, env):
        channel = env.guild.create_text_channel("showcase")
        visitor = env.guild.add_member(env.create_user("reader"))

        await visitor.send(channel, "!layout")

        help_message = channel.last_message
        assert help_message is not None
        guide = "\n".join(displayed_text(help_message))
        assert "## /layout" in guide
        for name in ("catalogue", "workbench", "review"):
            assert f"/layout {name}" in guide


async def test_layout_catalogue_opens_a_private_record_standing() -> None:
    async with command_environment("squid.bot.showcase.cog", development_mode=True) as (_, env):
        channel = env.guild.create_text_channel("showcase")
        visitor = env.guild.add_member(env.create_user("reader"))

        opened = await visitor.slash(channel, "layout catalogue")

        assert opened.response is not None
        assert opened.response.ephemeral
        record = "\n".join(displayed_text(opened.response.message))
        assert "Smallest 2x2 Seamless Door" in record
        assert "Lowest volume" in record


async def test_layout_workbench_posts_a_public_editor_and_its_build_preview() -> None:
    async with command_environment("squid.bot.showcase.cog", development_mode=True) as (_, env):
        channel = env.guild.create_text_channel("showcase")
        visitor = env.guild.add_member(env.create_user("reader"))

        opened = await visitor.slash(channel, "layout workbench")

        assert opened.response is not None
        assert not opened.response.ephemeral
        assert "Draft: Flush piston door" in displayed_text(opened.response.message)
        (preview,) = opened.followups
        assert not preview.ephemeral
        assert preview.id != opened.response.id
        assert "## Flush piston door" in displayed_text(preview.message)
        assert "Dimensions: 4x4x3" in displayed_text(preview.message)


async def test_layout_review_opens_two_named_seats_for_the_simulated_room() -> None:
    async with command_environment("squid.bot.showcase.cog", development_mode=True) as (_, env):
        channel = env.guild.create_text_channel("review")
        host = env.guild.add_member(env.create_user("reviewer"))

        opened = await host.slash(channel, "layout review")

        assert opened.response is not None
        assert not opened.response.ephemeral
        assert "0 approved · 0 requested changes · 2 reviewers" in displayed_text(opened.response.message)
        assert {displayed_text(panel.message)[0] for panel in opened.followups} == {
            "## Alice · simulated reviewer",
            "## Bob · simulated reviewer",
        }


async def test_layout_review_live_admits_another_member_to_the_room() -> None:
    async with command_environment("squid.bot.showcase.cog", development_mode=True) as (_, env):
        channel = env.guild.create_text_channel("review")
        host = env.guild.add_member(env.create_user("host"))
        guest = env.guild.add_member(env.create_user("guest"))

        opened = await host.slash(channel, "layout review", mode="live")

        assert opened.response is not None
        assert "Waiting for reviewers." in "\n".join(displayed_text(opened.response.message))
        joined = await guest.click(opened.response.message, label="Join review")
        assert any(
            "guest · reviewer" in text
            for response in (joined.response, *joined.followups)
            if response
            for text in displayed_text(response.message)
        )


async def test_dev_group_preserves_hidden_help_and_owner_can_open_the_private_dashboard() -> None:
    async with command_environment("squid.bot.help_cog", "squid.bot.devtools_cog", development_mode=True) as (bot, env):
        channel = env.guild.create_text_channel("dev")
        role = env.guild.create_role("Administrator", permissions=discord.Permissions(administrator=True))
        owner = env.guild.add_member(env.create_user("owner"), roles=[role])
        bot.owner_id = owner.id

        await owner.send(channel, "!dev")

        guide = channel.last_message
        assert guide is not None
        assert "No command named `dev` is available." in displayed_text(guide)
        dashboard = await owner.slash(channel, "dev ui")
        assert dashboard.response is not None
        assert dashboard.response.ephemeral
        overview = "\n".join(displayed_text(dashboard.response.message))
        assert "Operational dashboard" in overview
        assert "scheduler" in overview
        assert "Download snapshot" in overview
