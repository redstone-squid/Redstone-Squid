"""Minecraft version catalogue management."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import discord
from discord import app_commands
from discord.ext.commands import Cog

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.i18n import resolve_locale, t
from squid.bot.ui import L, render_payload, text_node
from squid.bot.utils.permissions import allows
from squid.core.i18n import _
from squid.permissions.domain.catalogue import VERSION_ENTRY_CREATE
from squid.versions.domain import Edition, MinecraftVersion
from squid_ui_discord import send_to

if TYPE_CHECKING:
    import squid.bot.app


class VersionOperations(Protocol):
    """Version reads and writes used by the live catalogue."""

    async def list_display(self, edition: Edition, *, limit: int | None = None) -> Sequence[str]: ...

    async def add(self, version_string: str, *, edition: Edition | None = None) -> MinecraftVersion: ...


@dataclass(frozen=True, slots=True)
class VersionItem:
    """One edition-qualified version shown in the catalogue."""

    edition: Edition
    value: str


type VersionAuthorizer = Callable[[], Awaitable[bool]]


class VersionScreen(sd.Screen):
    """A version catalogue that ends when closed, replaced, or timed out."""

    session_name = "versions"
    timeout = 300
    visibility = "personal"

    def __init__(
        self,
        versions: VersionOperations,
        *,
        can_create: bool,
        authorize_create: VersionAuthorizer,
    ) -> None:
        self._versions = versions
        self._can_create = can_create
        self._authorize_create = authorize_create
        self._browser: sp.Browser[VersionItem, sl.ComponentsV2Target] | None = None

    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        java = await self._versions.list_display("Java")
        bedrock = await self._versions.list_display("Bedrock")
        entries = tuple(VersionItem("Java", value) for value in java) + tuple(
            VersionItem("Bedrock", value) for value in bedrock
        )
        self._browser = sp.Browser(
            sl.sources.list_source(entries),
            key="versions",
            identity=lambda item: f"{item.edition}:{item.value}",
            label=lambda item: item.value,
            summary=lambda item: f"{item.value} · {item.edition}",
            detail=lambda item: sl.fields(
                sl.field(L(t"Version"), item.value),
                sl.field(L(t"Edition"), item.edition),
            ),
            page_size=15,
            title=L(t"Recognized Minecraft versions"),
            empty=L(t"No Minecraft versions are recognized yet."),
        )

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.status(L(t"Loading versions.")) if self._browser is None else self.boundary(self._browser, key="browser")
        ]
        if self._can_create:
            nodes.append(
                sl.form(
                    L(t"Add version"),
                    sl.forms.FormSpec(
                        L(t"Add Minecraft version"),
                        (
                            sl.forms.ChoiceField(
                                key="edition",
                                label=L(t"Edition"),
                                default="Java",
                                options=(
                                    sl.forms.ChoiceOption("java", L(t"Java"), "Java"),
                                    sl.forms.ChoiceOption("bedrock", L(t"Bedrock"), "Bedrock"),
                                ),
                            ),
                            sl.forms.TextField(key="version", label=L(t"Version"), maximum=100),
                        ),
                    ),
                    key="add-version",
                    on_submit=self._add,
                )
            )
        nodes.append(
            sl.action_controls(
                sl.action_control(L(t"Close"), self._close, key="close"),
                key="version-actions",
            )
        )
        return tuple(nodes)

    async def _add(self, event: sl.SubmitEvent) -> None:
        if not await self._authorize_create():
            await event.notice(L(t"You are no longer allowed to add versions."))
            return
        edition = cast(Edition, event.values["edition"])
        version_text = cast(str, event.values["version"])
        version = str(await self._versions.add(version_text, edition=edition))
        await self._refresh()
        await event.notice(L(t"Added {version}."))

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()


class VersionTracker[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="VersionTracker"):
    """Open the version catalogue and ingest configured channel announcements."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.version_service = bot.services.versions

    @app_commands.command(name="versions", description="Browse recognized Minecraft versions")
    async def versions(self, interaction: discord.Interaction[BotT]) -> None:
        """Open the paged version catalogue."""

        async def may_create() -> bool:
            return await allows(interaction, VERSION_ENTRY_CREATE)

        await VersionScreen(
            self.version_service,
            can_create=await may_create(),
            authorize_create=may_create,
        ).show(interaction)

    @Cog.listener(name="on_message")
    async def on_message_version_add(self, message: discord.Message) -> None:
        """Parse messages in the version-tracking channel and add them to the database."""
        channel_id = message.channel.id
        if channel_id != self.bot.community_config.version_tracker_channel_id:
            return
        version = await self.version_service.add(message.content.split("\n", 1)[0])
        locale = await resolve_locale(message, self.bot.services.settings)
        await send_to(self.bot.get_channel(channel_id))(  # type: ignore[arg-type]
            render_payload([text_node(t(locale, _("Version added successfully: {version}"), version=version))])
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the version catalogue cog."""
    await bot.add_cog(VersionTracker(bot))
