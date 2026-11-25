"""Minecraft version catalogue management."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, Self, override

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.i18n import localization_for, resolve_locale
from squid.bot.ui import render_payload, text_node, tr
from squid.bot.utils.permissions import Grants, requires_grant
from squid.core.actors import ActingContext
from squid.core.permissions.domain.catalogue import VERSION_ENTRY_CREATE
from squid.core.versions.domain import Edition, MinecraftVersion
from squid.core.versions.errors import InvalidVersionError
from squid_i18n import md_tr
from squid_ui.content.text import localization_scope
from squid_ui.ext.discord import send_to
from squid_ui.ext.discord.i18n import to_locale_str
from squid_ui.ext.discord.target_types import ComponentsV2Target

if TYPE_CHECKING:
    import squid.bot.app


class VersionOperations(Protocol):
    """Version reads and writes used by the live catalogue."""

    async def list_display(
        self, edition: Edition, *, limit: int | None = None, context: ActingContext
    ) -> Sequence[str]: ...

    async def add(
        self, version_string: str, *, edition: Edition | None = None, context: ActingContext
    ) -> MinecraftVersion: ...


@dataclass(frozen=True, slots=True)
class VersionItem:
    """One edition-qualified version shown in the catalogue."""

    edition: Edition
    value: str


class VersionScreen(sd.Screen):
    """A version catalogue that ends when replaced or timed out."""

    policy = sd.ResponseSpec(nav=sd.navigation_controls.page_jump_nav, timeout=300, session=sd.SessionSpec("versions"))

    def __init__(
        self,
        versions: VersionOperations,
        *,
        grants: Grants,
    ) -> None:
        self._versions = versions
        self.grants = grants
        self._browser: sp.Browser[VersionItem, ComponentsV2Target, sd.Request] | None = None

    @override
    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        java = await self._versions.list_display("Java", context=ActingContext.current())
        bedrock = await self._versions.list_display("Bedrock", context=ActingContext.current())
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
                sl.field(tr(t"bot-version-tracking-version = Version"), item.value),
                sl.field(tr(t"bot-version-tracking-edition = Edition"), item.edition),
            ),
            fetch_size=15,
            title=tr(t"versions-recognized-list-title = Recognized Minecraft versions"),
            empty=tr(t"versions-none-recognized = No Minecraft versions are recognized yet."),
        )

    @override
    def render(self) -> list[sl.LayoutNode[ComponentsV2Target, sd.Request]]:
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = [
            sl.status(tr(t"bot-version-tracking-loading-versions = Loading versions."))
            if self._browser is None
            else self._browser
        ]
        if VERSION_ENTRY_CREATE in self.grants:
            nodes.append(
                sl.form(
                    tr(t"bot-version-tracking-add-version = Add version"),
                    _AddVersionForm().spec(),
                    key="add-version",
                    on_submit=self._add,
                )
            )
        return nodes

    @requires_grant(
        VERSION_ENTRY_CREATE, denied=tr(t"versions-add-permission-denied = You are no longer allowed to add versions.")
    )
    async def _add[ContextT](self, event: sl.SubmitEvent[ContextT]) -> None:
        form = _AddVersionForm.from_submission(event)
        edition = form.edition
        version_text = form.version
        version = str(await self._versions.add(version_text, edition=edition, context=ActingContext.current()))
        await self._refresh()
        await event.notice(md_tr(t"bot-version-tracking-added = Added {version}."))


class VersionTracker[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT], name="VersionTracker"):
    """Open the version catalogue and ingest configured channel announcements."""

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.version_service = bot.services.versions

    @sd.command(
        name=to_locale_str(tr(t"command-versions-name = versions")),
        description=to_locale_str(tr(t"command-versions-help = Browse recognized Minecraft versions")),
    )
    async def versions(self, request: sd.Request[Self, BotT]) -> VersionScreen:
        """Open the paged version catalogue."""
        return VersionScreen(self.version_service, grants=await Grants.load(request, VERSION_ENTRY_CREATE))

    @sd.Cog.listener(name="on_message")
    async def on_message_version_add(self, message: discord.Message) -> None:
        """Ingest version announcements, ignoring our own replies and non-version posts."""
        if message.guild is None or message.guild.id != self.bot.owner_server_id:
            return
        settings = await self.bot.services.settings.get_runtime(context=ActingContext.current())
        channel_id = message.channel.id
        if channel_id != settings.version_tracker_channel_id:
            return
        if message.author == self.bot.user:
            return
        try:
            version = await self.version_service.add(
                message.content.split("\n", 1)[0], context=ActingContext.worker("discord.version-announcement")
            )
        except InvalidVersionError:
            return
        locale = await resolve_locale(message, self.bot.services.settings)
        with localization_scope(localization_for(locale)):
            payload = render_payload(
                [
                    text_node(
                        md_tr(
                            t"bot-version-tracking-version-added-successfully = Version added successfully: {version}",
                        )
                    )
                ]
            )
        await send_to(self.bot.get_channel(channel_id))(  # type: ignore[arg-type]
            payload
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the version catalogue cog."""
    await bot.add_cog(VersionTracker(bot))


class _AddVersionForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"bot-version-tracking-add-minecraft-version = Add Minecraft version")

    edition = sl.forms.ChoiceField[Edition](
        label=tr(t"bot-version-tracking-edition = Edition"),
        default="Java",
        options=(
            sl.forms.ChoiceOption("java", tr(t"bot-version-tracking-java = Java"), "Java"),
            sl.forms.ChoiceOption("bedrock", tr(t"bot-version-tracking-bedrock = Bedrock"), "Bedrock"),
        ),
    )
    version = sl.forms.TextField(label=tr(t"bot-version-tracking-version = Version"), maximum=100)
