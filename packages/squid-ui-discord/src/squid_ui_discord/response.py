"""Response policy and outcomes for the owner-scoped Discord facade."""

from dataclasses import dataclass, fields
from enum import Enum
from typing import TypedDict, Unpack

import discord

from squid_ui.chrome import Chrome
from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.access import AccessPolicy
from squid_ui_discord.audience import Audience, Private
from squid_ui_discord.contracts import FacadeContent
from squid_ui_discord.delivery import DeliveryResult
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_contracts import ExpiryPolicy
from squid_ui_discord.session_specs import SessionSpec
from squid_ui_discord.sessions import RejectionReason, Session


class _Unset(Enum):
    TOKEN = "unset"


UNSET = _Unset.TOKEN
"""A response setting inherited from the next less-specific policy layer."""

type Setting[T] = T | _Unset


class _InvokerOnly:
    def __repr__(self) -> str:
        return "invoker_only"


invoker_only = _InvokerOnly()
"""Resolve live-component access to `Owner(actor_id)` at delivery; `None` means the same."""

type AccessSetting = AccessPolicy | _InvokerOnly | None


class ResponseOverrides(TypedDict, total=False):
    """Per-call values for one facade operation, overlaid on the scope's `ResponseSpec`; keys as there."""

    audience: Audience
    access: AccessSetting
    timeout: float | None
    expiry: ExpiryPolicy | None
    follow_topics: bool
    session: SessionSpec | None
    allowed_mentions: discord.AllowedMentions | None
    chrome: Chrome | None


@dataclass(frozen=True, slots=True)
class ResponseSpec:
    """One layer of response policy; `UNSET` fields fall through to the layer below.

    Layers, least specific first: `DEFAULT_RESPONSE_SPEC`, `DiscordUIConfig.responses`, the
    scope's defaults, the content class's `__response_spec__`, then a call's
    `ResponseOverrides`. Fields that concern only live components (`access`, `timeout`,
    `expiry`, `follow_topics`, `session`, `chrome`) are ignored for static content.
    """

    audience: Setting[Audience] = UNSET
    access: Setting[AccessSetting] = UNSET
    """Who may click the live controls; `None` or `invoker_only` resolves to the acting user."""
    timeout: Setting[float | None] = UNSET
    """Seconds since the last accepted click before a live mount finishes; `None` never."""
    expiry: Setting[ExpiryPolicy | None] = UNSET
    """What happens as edit authority lapses; `RenewEphemeral` becomes `PauseUpdates` when no scheduler runs."""
    follow_topics: Setting[bool] = UNSET
    """Give the mount the runtime scheduler so it refreshes from topics and shared state."""
    session: Setting[SessionSpec | None] = UNSET
    """Open live content under this session policy; `None` mounts it outside any session."""
    allowed_mentions: Setting[discord.AllowedMentions | None] = UNSET
    """`None` sends with no mentions allowed."""
    chrome: Setting[Chrome | None] = UNSET

    def overlay(self, other: ResponseSpec | None = None, /, **overrides: Unpack[ResponseOverrides]) -> ResponseSpec:
        """Return this policy with `other`'s set fields on top, then `overrides` on top of those."""
        values = {field.name: getattr(self, field.name) for field in fields(self)}
        if other is not None:
            values.update(
                (field.name, value) for field in fields(other) if (value := getattr(other, field.name)) is not UNSET
            )
        values.update(overrides)
        return ResponseSpec(**values)  # pyrefly: ignore[bad-argument-type]


DEFAULT_RESPONSE_SPEC = ResponseSpec(
    audience="public",
    access=None,
    timeout=180,
    expiry=None,
    follow_topics=False,
    session=None,
    allowed_mentions=None,
    chrome=None,
)


class Response[ContentT: FacadeContent = FacadeContent]:
    """Content paired with the `ResponseOverrides` it is delivered under.

    What a command handler returns when the content alone would get the wrong audience.
    `Request.respond` unpacks it; overrides passed to `respond` itself win over these.
    """

    __slots__ = ("content", "overrides")

    def __init__(self, content: ContentT, **overrides: Unpack[ResponseOverrides]) -> None:
        self.content = content
        self.overrides = overrides

    def __repr__(self) -> str:
        return f"Response({self.content!r}, {self.overrides!r})"


@dataclass(frozen=True, slots=True)
class Sent:
    """Static content was delivered; `edit` and `delete` work only while `delivery`'s handles are live."""

    delivery: DeliveryResult

    async def edit(self, payload: MessagePayload, *, keep_attachments: bool = False) -> None:
        """Replace the message; raises `RuntimeError` when the delivery exposed no edit handle."""
        handle = self.delivery.handle
        if handle is None:
            message = "this delivery exposed no edit authority"
            raise RuntimeError(message)
        await handle.write(payload, keep_attachments=keep_attachments)

    async def delete(self) -> None:
        """Delete the message; raises `RuntimeError` when the delivery exposed no delete handle."""
        handle = self.delivery.delete_handle
        if handle is None:
            message = "this delivery exposed no delete authority"
            raise RuntimeError(message)
        await handle.delete()


@dataclass(frozen=True, slots=True)
class Presented[ComponentT: Component[ComponentsV2Target]]:
    """A live component was delivered; `root` finishes with its owner scope at the latest."""

    component: ComponentT
    root: MessageRoot
    session: Session | None
    """`None` when the response policy had no `session`, so the mount stands alone."""
    delivery: DeliveryResult


@dataclass(frozen=True, slots=True)
class Rejected:
    """Session admission was refused; falsy.

    `delivery` is the rejection notice sent to the actor, or `None` when the session policy
    configured none.
    """

    reason: RejectionReason
    delivery: DeliveryResult | None = None

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class Abandoned:
    """The destination chose not to deliver and already told the user why (`DeliveryAbandoned`); falsy."""

    def __bool__(self) -> bool:
        return False


type StaticResponseResult = Sent | Rejected | Abandoned
type ResponseResult[
    ComponentT: Component[ComponentsV2Target] = Component[ComponentsV2Target],
] = StaticResponseResult | Presented[ComponentT]


__all__ = [
    "DEFAULT_RESPONSE_SPEC",
    "UNSET",
    "Abandoned",
    "AccessSetting",
    "Audience",
    "Presented",
    "Private",
    "Rejected",
    "Response",
    "ResponseOverrides",
    "ResponseResult",
    "ResponseSpec",
    "Sent",
    "Setting",
    "StaticResponseResult",
    "invoker_only",
]
