"""Declarative response policy for live components."""

from typing import Any, ClassVar, cast, override

import discord

from squid_ui.chrome import Chrome
from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.access import Everyone
from squid_ui_discord.audience import Audience, Private
from squid_ui_discord.message_root_contracts import ExpiryPolicy
from squid_ui_discord.request import Request
from squid_ui_discord.response import AccessSetting, ResponseSpec, invoker_only
from squid_ui_discord.session_specs import ScopeKind, SessionSpec
from squid_ui_discord.sessions import DEFAULT_ADMISSION, AdmissionSpec, Reject


class Screen[OwnerT = Any](Component[ComponentsV2Target]):
    """A component whose class compiles immutable presentation policy once.

    `Screen[Cog]` types `opening.owner`; the bare form leaves it `Any`.
    """

    audience: ClassVar[Audience] = "personal"
    access: ClassVar[AccessSetting] = invoker_only
    timeout: ClassVar[float | None] = 180
    expiry: ClassVar[ExpiryPolicy | None] = None
    follow_topics: ClassVar[bool] = False
    session: ClassVar[SessionSpec | None] = None
    allowed_mentions: ClassVar[discord.AllowedMentions | None] = None
    chrome: ClassVar[Chrome | None] = None
    __response_spec__: ClassVar[ResponseSpec]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls.__response_spec__ = cls._compile_response_spec()

    @property
    def opening(self) -> Request[OwnerT]:
        """The request assigned before this instance's first load and render."""
        try:
            opening = self.__dict__["_screen_opening"]
        except KeyError:
            message = f"{type(self).__name__}.opening is unavailable before facade presentation"
            raise RuntimeError(message) from None
        return cast(Request[OwnerT], opening)

    @classmethod
    def _compile_response_spec(cls) -> ResponseSpec:
        """Validate and freeze this screen class's declarative response policy."""
        if cls.audience not in ("public", "personal") and not isinstance(cls.audience, Private):
            message = f"{cls.__name__}.audience must be 'public', 'personal', or Private"
            raise TypeError(message)
        if (
            cls.access is not None
            and cls.access is not invoker_only
            and not callable(getattr(cls.access, "check", None))
        ):
            message = f"{cls.__name__}.access must be an access policy, invoker_only, or None"
            raise TypeError(message)
        return ResponseSpec(
            audience=cls.audience,
            access=cls.access,
            timeout=cls.timeout,
            expiry=cls.expiry,
            follow_topics=cls.follow_topics,
            session=cls.session,
            allowed_mentions=cls.allowed_mentions,
            chrome=cls.chrome,
        )


Screen.__response_spec__ = Screen._compile_response_spec()


class _SessionScreen[OwnerT = Any](Screen[OwnerT]):
    _preset_scope: ClassVar[ScopeKind] = ScopeKind.USER
    _preset_admission: ClassVar[AdmissionSpec] = DEFAULT_ADMISSION

    @override
    def __init_subclass__(
        cls,
        *,
        session_name: str | None = None,
        _preset_base: bool = False,
        **kwargs: object,
    ) -> None:
        if session_name is not None:
            if "session" in cls.__dict__:
                message = "Declare either session_name or session, not both"
                raise TypeError(message)
            cls.session = SessionSpec(session_name, scope=cls._preset_scope, admission=cls._preset_admission)
        name = None if cls.session is None else cls.session.name
        if not _preset_base and (not isinstance(name, str) or not name):
            message = f"{cls.__name__} requires a non-empty session_name or a named SessionSpec"
            raise TypeError(message)
        super().__init_subclass__(**kwargs)


class UserSessionScreen[OwnerT = Any](_SessionScreen[OwnerT], _preset_base=True):
    """An owner-only personal screen with one replaceable session per user.

    Declare `session_name` as a class keyword, or `session` for a custom recipe.
    """


class UserGuildSessionScreen[OwnerT = Any](_SessionScreen[OwnerT], _preset_base=True):
    """An owner-only personal screen with one replaceable session per user and guild."""

    _preset_scope = ScopeKind.USER_GUILD


class SharedGuildSessionScreen[OwnerT = Any](_SessionScreen[OwnerT], _preset_base=True):
    """A public screen with one shared session per guild; duplicate opens are rejected."""

    _preset_scope = ScopeKind.GUILD
    _preset_admission = AdmissionSpec(collision=Reject())
    access = Everyone()
    audience = "public"


__all__ = ["Screen", "SharedGuildSessionScreen", "UserGuildSessionScreen", "UserSessionScreen"]
