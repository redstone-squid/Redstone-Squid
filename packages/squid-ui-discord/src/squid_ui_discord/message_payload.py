"""The whole outgoing Discord message surface, as one value.

A Discord message has two halves. Squid's renderer owns the components; content and embeds
were owned by nobody, so delivery guessed at them from whatever `discord.Message` happened to
be in reach. A :class:`MessagePayload` is both halves plus the files that go with them,
so delivering is replacing a message rather than patching one field of it and hoping about
the rest.

Coherence is checked at construction because the incoherent combinations are exactly the ones
Discord answers with a 400 that names nothing useful. discord.py sets the Components V2 flag
implicitly -- `handle_message_parameters` turns it on whenever `view.has_components_v2()` --
and never checks that the rest of the payload agrees with it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import discord

from squid_ui.assets import Asset
from squid_ui.errors import LayoutError
from squid_ui_discord.attachments import files_for

type AnyView = discord.ui.View | discord.ui.LayoutView


class MessageMode(StrEnum):
    """Which of Discord's two message component modes a payload is written for."""

    CLASSIC = "classic"
    """Content, embeds, and up to five action rows: the pre-Components-V2 message."""

    COMPONENTS_V2 = "components_v2"
    """A `LayoutView` owning the whole message; content and embeds are forbidden."""


class MessageModeError(LayoutError):
    """A payload disagrees with its own mode, or with the message it is being written to."""


def message_mode(message: discord.Message) -> MessageMode:
    """Which mode a sent message is in, read from the flag Discord set on it."""
    return MessageMode.COMPONENTS_V2 if message.flags.components_v2 else MessageMode.CLASSIC


def _incoherent(
    mode: MessageMode, content: str | None, embeds: Sequence[discord.Embed], view: AnyView | None
) -> list[str]:
    """Every way this payload contradicts the mode it declares."""
    problems: list[str] = []
    if mode is MessageMode.COMPONENTS_V2:
        if content is not None:
            problems.append("a Components V2 message cannot carry content")
        if embeds:
            problems.append("a Components V2 message cannot carry embeds")
        if not isinstance(view, discord.ui.LayoutView):
            problems.append("a Components V2 message needs a LayoutView, which is the whole message")
        return problems
    if isinstance(view, discord.ui.LayoutView):
        problems.append("a classic message cannot carry a LayoutView")
    elif view is not None and view.has_components_v2():
        # `ActionRow._is_v2()` is `True` on purpose, so a view that would serialize to a
        # perfectly legal classic payload can still set the flag on its way out.
        problems.append("this classic view reports Components V2 items, which sets the flag implicitly")
    return problems


@dataclass(frozen=True, slots=True)
class MessagePayload:
    """Everything Squid puts on one Discord message, as one replacement value.

    Absent content and embeds are explicit clears rather than omitted kwargs: a payload
    describes the whole surface Squid owns, so what it does not name is what the message must
    stop showing.
    """

    mode: MessageMode
    content: str | None = None
    embeds: tuple[discord.Embed, ...] = ()
    view: AnyView | None = None
    assets: tuple[Asset, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "embeds", tuple(self.embeds))
        object.__setattr__(self, "assets", tuple(self.assets))
        problems = _incoherent(self.mode, self.content, self.embeds, self.view)
        if problems:
            raise MessageModeError("; ".join(problems))

    @classmethod
    def components_v2(cls, view: discord.ui.LayoutView, *, assets: Sequence[Asset] = ()) -> MessagePayload:
        """A Components V2 message: the layout is the message."""
        return cls(MessageMode.COMPONENTS_V2, view=view, assets=tuple(assets))

    @classmethod
    def classic(
        cls,
        *,
        content: str | None = None,
        embeds: Sequence[discord.Embed] = (),
        view: discord.ui.View | None = None,
        assets: Sequence[Asset] = (),
    ) -> MessagePayload:
        """A pre-Components-V2 message: content, embeds, and at most five action rows."""
        return cls(MessageMode.CLASSIC, content=content, embeds=tuple(embeds), view=view, assets=tuple(assets))

    @property
    def layout(self) -> discord.ui.LayoutView:
        """The Components V2 view, for a host surface that only speaks V2.

        Raises:
            MessageModeError: This is a classic payload, which has no layout to give.
        """
        if not isinstance(self.view, discord.ui.LayoutView):
            message = f"a {self.mode.value} payload has no LayoutView"
            raise MessageModeError(message)
        return self.view

    def build_files(self) -> list[discord.File]:
        """Materialize fresh file wrappers; a sent `discord.File` cannot be re-sent."""
        return files_for(self.assets)

    def _send_fields(self) -> dict[str, Any]:
        """The discord.py send kwargs this payload owns, attachments excluded."""
        if self.mode is MessageMode.COMPONENTS_V2:
            # Content and embeds are not merely empty here, they are forbidden: naming them
            # at all beside the V2 flag is a payload Discord is entitled to reject.
            return {"view": self.view}
        return {"content": self.content, "embeds": list(self.embeds), "view": self.view}

    def _edit_fields(self, previous: MessageMode) -> dict[str, Any]:
        """The discord.py edit kwargs that move a message in `previous` mode to this one.

        Raises:
            MessageModeError: The transition is one Discord does not offer.
        """
        if previous is MessageMode.COMPONENTS_V2 and self.mode is MessageMode.CLASSIC:
            message = "Discord cannot take the Components V2 flag back off a sent message"
            raise MessageModeError(message)
        if self.mode is MessageMode.CLASSIC:
            return {"content": self.content, "embeds": list(self.embeds), "view": self.view}
        if previous is MessageMode.CLASSIC:
            # The one transition with legacy fields to clear. V2 -> V2 must not name them.
            return {"view": self.view, "content": None, "embeds": []}
        return {"view": self.view}
