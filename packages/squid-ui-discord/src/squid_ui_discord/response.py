"""Describe how a Discord response should be presented and what delivery produced.

ResponseSpec combines audience, access, and lifetime settings. The facade overlays
runtime defaults with a Screen's policy, then call-specific overrides; UNSET inherits
the earlier value, while None is an explicit setting. For example, timeout=None disables
the idle timeout rather than inheriting the runtime's timeout.

Delivery returns Sent for static content or Presented for a live component, with
Rejected and Abandoned recording admission refusal and deliberate non-delivery.
"""

from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, TypedDict, Unpack, cast, override

import discord

from squid_ui.chrome import Chrome
from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.access import AccessPolicy, Owner
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
    @override
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
    live_updates: bool
    retain_routed_on_timeout: bool
    session: SessionSpec | None
    allowed_mentions: discord.AllowedMentions | None
    chrome: Chrome | None


@dataclass(frozen=True, slots=True)
class ResponseSpec:
    """Immutable audience, delivery, and live-presentation policy."""

    audience: Setting[Audience] = UNSET
    access: Setting[AccessSetting] = UNSET
    timeout: Setting[float | None] = UNSET
    expiry: Setting[ExpiryPolicy | None] = UNSET
    live_updates: Setting[bool] = UNSET
    """Schedule background refreshes for observed reactive fields and external topics.

    Without it, only the message's own interactions, its resource loading, and explicit
    refresh() calls redraw it. A message pressed or closed while still showing state from
    before an outside change logs a warning naming the screen.
    """
    retain_routed_on_timeout: Setting[bool] = UNSET
    session: Setting[SessionSpec | None] = UNSET
    allowed_mentions: Setting[discord.AllowedMentions | None] = UNSET
    chrome: Setting[Chrome | None] = UNSET

    def overlay(self, other: ResponseSpec | None = None, /, **overrides: Unpack[ResponseOverrides]) -> ResponseSpec:
        """Return this policy with specified values from the more-specific layer."""
        values = {field.name: getattr(self, field.name) for field in fields(self)}
        if other is not None:
            values.update(
                (field.name, value) for field in fields(other) if (value := getattr(other, field.name)) is not UNSET
            )
        values.update(overrides)
        return ResponseSpec(**values)  # pyrefly: ignore[bad-argument-type]

    def resolve(self, actor_id: int | None) -> ResolvedResponse:
        """Resolve inheritance and actor access once before constructing a presentation."""

        def value[T](setting: Setting[T], *, fallback: T) -> T:
            return fallback if setting is UNSET else cast(T, setting)

        access = value(self.access, fallback=None)
        if access is None or access is invoker_only:
            access = None if actor_id is None else Owner(actor_id)
        return ResolvedResponse(
            audience=value(self.audience, fallback="public"),
            access=cast(AccessPolicy | None, access),
            timeout=value(self.timeout, fallback=180),
            expiry=value(self.expiry, fallback=None),
            live_updates=value(self.live_updates, fallback=False),
            retain_routed_on_timeout=value(self.retain_routed_on_timeout, fallback=False),
            session=value(self.session, fallback=None),
            allowed_mentions=value(self.allowed_mentions, fallback=None),
            chrome=value(self.chrome, fallback=None),
        )


@dataclass(frozen=True, slots=True)
class ResolvedResponse:
    """Concrete presentation settings; no inheritance remains at the delivery boundary."""

    audience: Audience
    access: AccessPolicy | None
    timeout: float | None
    expiry: ExpiryPolicy | None
    live_updates: bool
    retain_routed_on_timeout: bool
    session: SessionSpec | None
    allowed_mentions: discord.AllowedMentions | None
    chrome: Chrome | None


DEFAULT_RESPONSE_SPEC = ResponseSpec(
    audience="public",
    access=None,
    timeout=180,
    expiry=None,
    live_updates=False,
    retain_routed_on_timeout=False,
    session=None,
    allowed_mentions=None,
    chrome=None,
)


class Response[ContentT: FacadeContent = FacadeContent]:
    """Content paired with the call-level policy it should be delivered under.

    What a command handler returns when the content alone would get the wrong audience.
    """

    __slots__ = ("_options", "content")

    def __init__(self, content: ContentT, **overrides: Unpack[ResponseOverrides]) -> None:
        self.content = content
        self._options = overrides

    @override
    def __repr__(self) -> str:
        return f"Response({self.content!r}, {self._options!r})"


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
class Presented[ComponentT: Component[ComponentsV2Target, Any]]:
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
    ComponentT: Component[ComponentsV2Target, Any] = Component[ComponentsV2Target, Any],
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
