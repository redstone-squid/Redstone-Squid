"""Frontend promotion and reconnection for durable Discord sessions."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import discord

from squid_discord.delivery import Abandoned, DeliveryResult, EditHandle, StaleHandleError, handle_for
from squid_discord.durability import FrontendAddress
from squid_discord.mount import Mount
from squid_discord.presentation import DiscordMode, DiscordPresentation


@dataclass(frozen=True, slots=True)
class Promoted:
    """A delivered mount now has portable coordinates and permanent edit authority."""

    address: FrontendAddress
    handle: EditHandle


@dataclass(frozen=True, slots=True)
class NotDurable:
    """A delivery cannot be recovered safely after this process exits."""

    reason: str


type PromotionResult = Promoted | NotDurable


@dataclass(frozen=True, slots=True)
class RecoveredBinding:
    """One restored mount and the persisted message it must reclaim."""

    record_mount_id: str
    mount: Mount
    address: FrontendAddress


@dataclass(frozen=True, slots=True)
class Reconnected:
    """Every mount in a restored session was redrawn onto its Discord message."""

    record_mount_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Missing:
    """Discord definitively reported that one or more persisted messages are gone."""

    record_mount_ids: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Unreachable:
    """One or more persisted messages could not be reached temporarily."""

    record_mount_ids: tuple[str, ...]
    reasons: tuple[str, ...]


type ReconnectResult = Reconnected | Missing | Unreachable


class DurableFrontend(Protocol):
    """A frontend that can promote live deliveries and reconnect restored sessions."""

    async def promote(self, mount: Mount, result: DeliveryResult) -> PromotionResult: ...

    async def reconnect(self, bindings: Sequence[RecoveredBinding]) -> ReconnectResult: ...


@dataclass(frozen=True, slots=True)
class _ResolvedBinding:
    binding: RecoveredBinding
    message: discord.Message


class DiscordFrontend:
    """Promote and reconnect durable mounts through bot-token Discord messages."""

    frontend = "discord"

    def __init__(self, client: discord.Client) -> None:
        self.client = client

    async def promote(self, mount: Mount, result: DeliveryResult) -> PromotionResult:
        """Trade a recoverable public delivery up to permanent bot-token authority."""
        message = result.message
        if message is None:
            return NotDurable("the delivery did not expose an addressable message")
        if result.ephemeral is True:
            return NotDurable("ephemeral Discord messages cannot be recovered")
        address = mount.address
        if address is None or address.message_id != message.id or address.channel_id != message.channel.id:
            return NotDurable("the delivery result does not address this mount's message")

        try:
            durable_message = await self._normal_message(message)
        except discord.NotFound:
            return NotDurable("Discord no longer has the delivered message")

        handle = handle_for(durable_message)
        await mount.adopt_handle(handle)
        values: dict[str, str | int] = {
            "channel_id": durable_message.channel.id,
            "message_id": durable_message.id,
            # Recorded, not re-derived on recovery: which mode the message is in decides
            # whether the first edit after a restart has legacy fields to clear.
            "mode": handle.mode.value,
        }
        if durable_message.guild is not None:
            values["guild_id"] = durable_message.guild.id
        return Promoted(FrontendAddress(self.frontend, values), handle)

    async def reconnect(self, bindings: Sequence[RecoveredBinding]) -> ReconnectResult:
        """Resolve a whole session before redrawing any of its restored mounts."""
        resolved: list[_ResolvedBinding] = []
        missing: list[tuple[str, str]] = []
        unreachable: list[tuple[str, str]] = []

        for binding in bindings:
            try:
                channel_id, message_id = self._coordinates(binding.address)
                message = await self._fetch_message(channel_id, message_id)
            except discord.NotFound:
                missing.append((binding.record_mount_id, "Discord no longer has the channel or message"))
            except discord.Forbidden:
                unreachable.append((binding.record_mount_id, "Discord denied access to the channel or message"))
            except discord.HTTPException:
                unreachable.append((binding.record_mount_id, "Discord could not resolve the channel or message"))
            else:
                resolved.append(_ResolvedBinding(binding, message))

        if missing:
            return Missing(tuple(item[0] for item in missing), tuple(item[1] for item in missing))
        if unreachable:
            return Unreachable(tuple(item[0] for item in unreachable), tuple(item[1] for item in unreachable))

        for item in resolved:
            handle = handle_for(item.message, mode=self._mode(item.binding.address))

            async def edit(
                presentation: DiscordPresentation,
                /,
                *,
                message: discord.Message = item.message,
                authority: EditHandle = handle,
            ) -> DeliveryResult:
                await authority.write(presentation)
                return DeliveryResult(message, authority, message_id=message.id, ephemeral=False)

            try:
                result = await item.binding.mount.send(edit)
            except discord.NotFound:
                return Missing((item.binding.record_mount_id,), ("Discord no longer has the message",))
            except discord.Forbidden, StaleHandleError:
                return Unreachable(
                    (item.binding.record_mount_id,),
                    ("Discord denied permanent edit access to the message",),
                )
            except discord.HTTPException:
                return Unreachable(
                    (item.binding.record_mount_id,),
                    ("Discord could not reconnect the message",),
                )
            if isinstance(result, Abandoned):
                return Unreachable(
                    (item.binding.record_mount_id,),
                    ("the restored mount finished before it could reconnect",),
                )

        return Reconnected(tuple(binding.record_mount_id for binding in bindings))

    async def _normal_message(self, message: discord.Message) -> discord.Message:
        """Return a message whose ``edit`` endpoint uses the bot token."""
        if type(message) is discord.Message:
            return message
        return await self._fetch_message(message.channel.id, message.id)

    async def _fetch_message(self, channel_id: int, message_id: int) -> discord.Message:
        channel = self.client.get_channel(channel_id)
        if channel is None:
            channel = await self.client.fetch_channel(channel_id)
        fetch_message = getattr(channel, "fetch_message", None)
        if fetch_message is None:
            message = f"Discord channel {channel_id} cannot contain messages"
            raise TypeError(message)
        return await fetch_message(message_id)

    def _coordinates(self, address: FrontendAddress) -> tuple[int, int]:
        if address.frontend != self.frontend:
            message = f"unsupported frontend {address.frontend!r}"
            raise ValueError(message)
        channel_id = address.values.get("channel_id")
        message_id = address.values.get("message_id")
        if not isinstance(channel_id, int) or isinstance(channel_id, bool):
            message = "Discord address channel_id must be an integer"
            raise TypeError(message)
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            message = "Discord address message_id must be an integer"
            raise TypeError(message)
        return channel_id, message_id

    def _mode(self, address: FrontendAddress) -> DiscordMode | None:
        """The message mode this address recorded, or `None` for one written before it did."""
        mode = address.values.get("mode")
        if mode is None:
            return None
        if not isinstance(mode, str) or mode not in set(DiscordMode):
            message = f"Discord address mode {mode!r} is not a message mode"
            raise TypeError(message)
        return DiscordMode(mode)
