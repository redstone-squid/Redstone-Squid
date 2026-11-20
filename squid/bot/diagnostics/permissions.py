"""Explain Discord permissions for configured posting channels and role assignment."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import override

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.core.settings.domain.guild import CHANNEL_FIELDS, GuildSettings
from squid.core.starboard.models import StarboardConfig
from squid_i18n import Text, tr
from squid_ui.ext.discord.target_types import ComponentsV2Target


@dataclass(frozen=True, slots=True)
class PermissionFinding:
    key: str
    title: Text
    detail: Text


class GuildPermissionDiagnostics(sl.Component[ComponentsV2Target, sd.Request]):
    """Show a snapshot of effective permissions, with bounded pages of findings."""

    def __init__(
        self,
        guild: discord.Guild,
        settings: GuildSettings,
        boards: Sequence[StarboardConfig],
        *,
        redstoner_role_id: int | None = None,
    ) -> None:
        self.guild = guild
        findings = [
            self.channel(
                key, tr(t"permission-posting-channel = {key} posting"), getattr(settings, field), voting=key == "Vote"
            )
            for key, field in CHANNEL_FIELDS.items()
        ]
        for board in boards:
            if board.enabled:
                name = board.name
                findings.append(
                    self.channel(
                        f"starboard-{board.id}", tr(t"permission-starboard = Starboard: {name}"), board.channel_id
                    )
                )
        if redstoner_role_id is not None:
            findings.append(self.role(redstoner_role_id))
        self.findings = tuple(findings)
        self.browser = sp.Browser[PermissionFinding, ComponentsV2Target, sd.Request](
            sl.sources.list_source(self.findings),
            key="permission-findings",
            identity=lambda finding: finding.key,
            label=lambda finding: finding.title,
            summary=lambda finding: Text.join(" — ", (finding.title, finding.detail)),
            detail=lambda finding: (sl.heading(finding.title), sl.paragraph(finding.detail)),
            fetch_size=5,
            title=tr(t"permission-diagnostics-heading = Discord permissions"),
        )

    def channel(self, key: str, title: Text, channel_id: int | None, *, voting: bool = False) -> PermissionFinding:
        if channel_id is None:
            return PermissionFinding(key, title, tr(t"permission-channel-unset = No channel configured."))
        channel = self.guild.get_channel_or_thread(channel_id)
        member = self.guild.me
        if channel is None:
            return PermissionFinding(
                key,
                title,
                tr(
                    t"permission-channel-unavailable = Access could not be checked. Confirm that the channel still exists and the bot can access it."
                ),
            )
        permissions = channel.permissions_for(member)
        required = {
            "view_channel": tr(t"permission-view-channel = View Channel"),
            "read_message_history": tr(t"permission-read-history = Read Message History"),
            "embed_links": tr(t"permission-embed-links = Embed Links"),
            "send_messages_in_threads" if isinstance(channel, discord.Thread) else "send_messages": tr(
                t"permission-send-messages = Send Messages (or Send Messages in Threads)"
            ),
        }
        if voting:
            required["add_reactions"] = tr(t"permission-add-reactions = Add Reactions")
        missing = Text.join(", ", (label for name, label in required.items() if not getattr(permissions, name)))
        if missing:
            detail = tr(t"permission-channel-missing = Channel {channel_id}: missing {missing}.")
        elif isinstance(channel, discord.Thread) and (channel.archived or channel.locked):
            detail = tr(t"permission-thread-closed = Channel {channel_id}: the thread is archived or locked.")
        else:
            detail = tr(t"permission-channel-ready = Channel {channel_id}: required posting permissions are present.")
        return PermissionFinding(key, title, detail)

    def role(self, role_id: int) -> PermissionFinding:
        title = tr(t"permission-redstoner-role = Redstoner role assignment")
        role, member = self.guild.get_role(role_id), self.guild.me
        if role is None:
            detail = tr(t"permission-role-unavailable = The configured role or bot membership is unavailable.")
        elif not member.guild_permissions.manage_roles:
            detail = tr(t"permission-role-manage = Missing Manage Roles.")
        elif role.managed or role.is_default():
            detail = tr(t"permission-role-managed = Discord manages this role; the bot cannot assign it.")
        elif role >= member.top_role:
            detail = tr(t"permission-role-hierarchy = Move the bot's highest role above the configured Redstoner role.")
        else:
            detail = tr(
                t"permission-role-ready = The bot can assign the configured role to members below its highest role."
            )
        return PermissionFinding("redstoner-role", title, detail)

    @override
    def render(self) -> sl.DocumentLike[ComponentsV2Target, sd.Request]:
        return (
            sl.note(
                tr(
                    t"permission-diagnostics-snapshot = Open Diagnostics again after changing roles or channel overrides. Source-channel access and individual member hierarchy may impose additional restrictions."
                )
            ),
            self.browser,
        )
