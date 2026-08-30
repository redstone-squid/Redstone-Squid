"""Declarative response policy for owner-scoped live components."""

from typing import ClassVar, cast

import discord

from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.audience import Audience, Private
from squid_ui_discord.message_root_contracts import ExpiryPolicy
from squid_ui_discord.request import DiscordRequest
from squid_ui_discord.response import AccessSetting, ResponseSpec, invoker_only
from squid_ui_discord.session_specs import SessionOptions, SessionSpec


class Screen[OwnerT = object](Component[ComponentsV2Target]):
    """A component whose class compiles immutable presentation policy once."""

    audience: ClassVar[Audience] = "personal"
    access: ClassVar[AccessSetting] = invoker_only
    timeout: ClassVar[float | None] = 180
    expiry: ClassVar[ExpiryPolicy | None] = None
    follow_topics: ClassVar[bool] = False
    session: ClassVar[SessionSpec | None] = None
    allowed_mentions: ClassVar[discord.AllowedMentions | None] = None
    root_options: ClassVar[SessionOptions] = {}
    __response_spec__: ClassVar[ResponseSpec]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls.__response_spec__ = cls._compile_response_spec()

    @property
    def opening(self) -> DiscordRequest[OwnerT]:
        """The request assigned before this instance's first load and render."""
        try:
            opening = self.__dict__["_screen_opening"]
        except KeyError:
            message = f"{type(self).__name__}.opening is unavailable before facade presentation"
            raise RuntimeError(message) from None
        return cast(DiscordRequest[OwnerT], opening)

    @classmethod
    def _compile_response_spec(cls) -> ResponseSpec:
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
        if not isinstance(cls.root_options, dict):
            message = f"{cls.__name__}.root_options must be a dict"
            raise TypeError(message)
        root_options = cast(SessionOptions, dict(cls.root_options))
        duplicates = {"expiry", "localization", "scheduler", "timeout"}.intersection(root_options)
        if duplicates:
            names = ", ".join(sorted(duplicates))
            message = f"{cls.__name__}.root_options repeats dedicated Screen policy: {names}"
            raise TypeError(message)
        return ResponseSpec(
            audience=cls.audience,
            access=cls.access,
            timeout=cls.timeout,
            expiry=cls.expiry,
            follow_topics=cls.follow_topics,
            session=cls.session,
            allowed_mentions=cls.allowed_mentions,
            root_options=root_options,
        )


Screen.__response_spec__ = Screen._compile_response_spec()


__all__ = ["Screen"]
