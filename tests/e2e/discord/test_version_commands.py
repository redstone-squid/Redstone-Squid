from typing import override

from squid.core.actors import ActingContext
from squid.core.versions.application import VersionService
from squid.core.versions.domain import Edition, MinecraftVersion
from tests.e2e.discord.environment import command_environment, displayed_text
from tests.e2e.discord.workspace_services import staff_permissions
from tests.support.accounts import account_service
from tests.support.services import bot_services


class EditionCatalogue(VersionService):
    def __init__(self) -> None:
        self.releases = (MinecraftVersion("Java", 1, 21, 5), MinecraftVersion("Bedrock", 1, 21, 50))

    @override
    async def list_versions(self, edition: Edition, *, context: ActingContext) -> list[MinecraftVersion]:
        return [release for release in self.releases if release.edition == edition]


async def test_versions_browses_a_bedrock_release_from_the_two_edition_catalogue() -> None:
    services = bot_services(versions=EditionCatalogue(), accounts=account_service(), permissions=staff_permissions())
    async with command_environment("squid.bot.version_tracking_cog", services=services) as (_, env):
        channel = env.guild.create_text_channel("versions")
        member = env.guild.add_member(env.create_user("builder"))

        opened = await member.slash(channel, "versions")

        assert opened.response is not None
        catalogue = "\n".join(displayed_text(opened.response.message))
        assert "Java 1.21.5" in catalogue
        assert "Bedrock 1.21.50" in catalogue

        selected = await member.select(opened.response.message, ["Bedrock:Bedrock 1.21.50"])

        assert selected.response is not None
        assert selected.response.id == opened.response.id
        detail = "\n".join(displayed_text(selected.response.message))
        assert "Version: Bedrock 1.21.50" in detail
        assert "Edition: Bedrock" in detail
