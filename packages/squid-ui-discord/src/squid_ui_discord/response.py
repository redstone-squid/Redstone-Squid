"""Response policy and outcomes for the owner-scoped Discord facade."""

from dataclasses import dataclass, fields
from enum import Enum
from types import MappingProxyType
from typing import TypedDict, Unpack

import discord

from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.access import AccessPolicy
from squid_ui_discord.audience import Audience, Private
from squid_ui_discord.contracts import FacadeContent
from squid_ui_discord.delivery import DeliveryResult
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_contracts import ExpiryPolicy
from squid_ui_discord.session_specs import SessionOptions, SessionSpec
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
"""Resolve live-component access to the actor who initiated presentation."""

type AccessSetting = AccessPolicy | _InvokerOnly | None


class ResponseOverrides(TypedDict, total=False):
    """Call-specific values accepted by facade operations."""

    audience: Audience
    access: AccessSetting
    timeout: float | None
    expiry: ExpiryPolicy | None
    follow_topics: bool
    session: SessionSpec | None
    allowed_mentions: discord.AllowedMentions | None
    root_options: SessionOptions | None


@dataclass(frozen=True, slots=True)
class ResponseSpec:
    """Immutable audience, delivery, and live-presentation policy."""

    audience: Setting[Audience] = UNSET
    access: Setting[AccessSetting] = UNSET
    timeout: Setting[float | None] = UNSET
    expiry: Setting[ExpiryPolicy | None] = UNSET
    follow_topics: Setting[bool] = UNSET
    session: Setting[SessionSpec | None] = UNSET
    allowed_mentions: Setting[discord.AllowedMentions | None] = UNSET
    root_options: Setting[SessionOptions | None] = UNSET

    def __post_init__(self) -> None:
        if self.root_options is not UNSET and self.root_options is not None:
            object.__setattr__(self, "root_options", MappingProxyType(dict(self.root_options)))

    def overlay(self, other: ResponseSpec | None = None, /, **overrides: Unpack[ResponseOverrides]) -> ResponseSpec:
        """Return this policy with specified values from the more-specific layer."""
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
    root_options=None,
)


@dataclass(frozen=True, slots=True)
class Response[ContentT: FacadeContent = FacadeContent]:
    """Response content paired with one call-specific policy layer."""

    content: ContentT
    spec: ResponseSpec | None = None
    overrides: ResponseOverrides | None = None


@dataclass(frozen=True, slots=True)
class Sent:
    """Static content was delivered; retained authority expires with its handles."""

    delivery: DeliveryResult

    async def edit(self, payload: MessagePayload, *, keep_attachments: bool = False) -> None:
        """Replace this delivery using its retained edit authority."""
        handle = self.delivery.handle
        if handle is None:
            message = "this delivery exposed no edit authority"
            raise RuntimeError(message)
        await handle.write(payload, keep_attachments=keep_attachments)

    async def delete(self) -> None:
        """Delete this delivery using its retained delete authority."""
        handle = self.delivery.delete_handle
        if handle is None:
            message = "this delivery exposed no delete authority"
            raise RuntimeError(message)
        await handle.delete()


@dataclass(frozen=True, slots=True)
class Presented[ComponentT: Component[ComponentsV2Target]]:
    """A live component was delivered; its root ends with its owner scope."""

    component: ComponentT
    root: MessageRoot
    session: Session | None
    delivery: DeliveryResult


@dataclass(frozen=True, slots=True)
class Rejected:
    """Session admission was refused after any configured notice was delivered."""

    reason: RejectionReason
    delivery: DeliveryResult | None = None

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class Abandoned:
    """Delivery was deliberately abandoned after any required notice."""

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
