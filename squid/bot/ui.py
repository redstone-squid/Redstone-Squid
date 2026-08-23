"""squid-layouts glue: localized chrome, house colours, and the semantic layout vocabulary.

This module is the bot's front door to the `squid_layouts` package. The package resolves text,
while this host supplies the gettext catalogue and translatable chrome messages.

The layout helpers keep the exact signatures of their `squid.bot.utils.components`
predecessors, so call sites migrate by changing an import line.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from string.templatelib import Template
from typing import Any, Literal

import discord
from discord.ext.commands import Context

import squid_layouts as ui
from squid.core.i18n import catalog_for, negotiate_locale

DISCORD_RED = 0xF04747
DISCORD_YELLOW = 0xFAA61A
DISCORD_GREEN = 0x43B581
DISCORD_BLUE = 0x5865F2
DISCORD_GREY = 0x4F545C
_DEFAULT_EXPIRY = ui.discord.PauseUpdates()

__all__ = [
    "CHROME",
    "DISCORD_BLUE",
    "DISCORD_GREEN",
    "DISCORD_GREY",
    "DISCORD_RED",
    "DISCORD_YELLOW",
    "MOUNT_DEFAULTS",
    "CardField",
    "CardSection",
    "L",
    "PagedList",
    "Private",
    "Visibility",
    "card_layout",
    "contribute",
    "create_mount",
    "destination",
    "error_layout",
    "help_layout",
    "info_layout",
    "link_layout",
    "localization_for",
    "render_item",
    "render_static",
    "reply",
    "send_component",
    "text_layout",
    "truncate_display_text",
    "warning_layout",
]


@dataclass(frozen=True, slots=True)
class CardField:
    """A labelled value rendered inside a card."""

    name: ui.TextLike
    value: ui.TextLike


@dataclass(frozen=True, slots=True)
class CardSection:
    """A titled group of related values rendered inside a card."""

    title: ui.TextLike
    fields: Sequence[CardField]


def L(message: str | Template, /, **params: object) -> ui.text.Message:
    """Mark and defer a translatable string: ``L(t"Page {page} of {pages}")``."""
    if isinstance(message, str):
        return ui.text.Message(message, params)
    if params:
        detail = "template strings already contain their interpolation values"
        raise TypeError(detail)
    values: dict[str, object] = {}
    parts: list[str] = []
    for string, interpolation in zip(message.strings, message.interpolations, strict=False):
        parts.append(string)
        name = interpolation.expression
        if not name.isidentifier():
            detail = f"template string interpolation {name!r} is not a placeholder name"
            raise ValueError(detail)
        parts.append("{" + name + "}")
        values[name] = interpolation.value
    parts.append(message.strings[-1])
    return ui.text.Message("".join(parts), values)


def localization_for(locale: str | None) -> ui.text.Localization:
    """Build the framework localization backed by the bot's negotiated catalogue."""
    resolved = negotiate_locale(locale)
    catalog = catalog_for(resolved)
    return ui.text.Localization(locale=resolved, gettext=catalog.gettext, ngettext=catalog.ngettext)


def _try_again_in(seconds: float) -> ui.text.Message:
    """Round a guard's remaining cooldown up to whole seconds before wording it."""
    whole = max(1, ceil(seconds))
    return L(t"Try again in {whole} seconds.")


CHROME = ui.semantic.Chrome(
    and_n_more=lambda count: L(t"…and {count} more."),
    not_yours=L(t"These list controls belong to someone else."),
    session_ended=L(t"This session has ended."),
    not_now=L(t"You can't do that right now."),
    try_again_in=_try_again_in,
    working=L(t"Working…"),
    updates_paused=L(t"Live updates paused — press any control to resume."),
    session_expiring=L(t"This session is about to expire."),
    continue_session=L(t"Continue Session"),
    previous=L(t"Previous"),
    next=L(t"Next"),
    back=L(t"Back"),
    home=L(t"Home"),
    close=L(t"Close"),
    on=L(t"On"),
    off=L(t"Off"),
    download=L(t"Download"),
    confirm=L(t"Confirm"),
    cancel=L(t"Cancel"),
    apply=L(t"Apply"),
    save=L(t"Save"),
    unsaved=L(t"Unsaved changes"),
    search=L(t"Search"),
    no_results=L(t"No results"),
    decided=lambda label: L(t"You chose {label}.", label=label),
    add=L(t"Add"),
    edit=L(t"Edit"),
    remove=L(t"Remove"),
    move_up=L(t"Move up"),
    move_down=L(t"Move down"),
    review=L(t"Review"),
    finish=L(t"Finish"),
    unanswered=L(t"Not answered yet"),
    page_footer=lambda page, pages: L(t"Page {page} of {pages}"),
)
_OPEN_LINK = L(t"Open link")


@dataclass(frozen=True, slots=True)
class Private:
    """Deliver where a channel can never see it: ephemeral or DM, with `reason` explaining why."""

    reason: str


type Visibility = Private | Literal["public", "personal"]


async def reply(
    ctx: Context[Any],
    view: discord.ui.LayoutView,
    *,
    visibility: Visibility = "public",
    locale: str | None = None,
    files: Sequence[discord.File] = (),
) -> discord.Message | None:
    """The one reply entry point: send `view` with an explicit audience.

    "public" answers in the channel; "personal" is ephemeral where the transport allows it
    (see `squid.bot.utils.visibility.personal`); `Private(reason)` must never reach a channel
    and falls back to a DM on the prefix side.
    """
    # Imported lazily: visibility -> utils.components -> this module would otherwise cycle.
    from squid.bot.utils.visibility import deliver_privately, personal

    if isinstance(visibility, Private):
        return await deliver_privately(ctx, view, reason=visibility.reason, locale=locale, files=files)
    extra: dict[str, Any] = {"files": list(files)} if files else {}
    ephemeral = visibility == "personal" and personal(ctx)
    return await ctx.send(view=view, ephemeral=ephemeral, allowed_mentions=ui.discord.delivery.no_mentions(), **extra)


def destination(
    ctx: Context[Any],
    *,
    visibility: Visibility = "public",
    locale: str | None = None,
    files: Sequence[discord.File] = (),
) -> ui.discord.Destination:
    """Where a mount's first message goes, in the same vocabulary `reply` uses.

    The audience rule stays host-side: "public" answers in the channel, "personal" is
    ephemeral where the transport allows it, and `Private(reason)` must never reach a channel
    at all. `files` are the host's own attachments; the mount adds its rendered assets.

    A closed DM under `Private` delivers nothing, which is not the same as delivering without
    a handle, so it is reported as `DeliveryAbandoned` rather than as a `None` message.
    """
    # Imported lazily: visibility -> utils.components -> this module would otherwise cycle.
    from squid.bot.utils.visibility import deliver_privately, personal

    if isinstance(visibility, Private):
        if ctx.interaction is not None:
            return ui.discord.reply_to(ctx, ephemeral=True, files=files)

        async def privately(presentation: ui.discord.DiscordPresentation) -> ui.discord.DeliveryReceipt:
            message = await deliver_privately(
                ctx,
                presentation.layout,
                reason=visibility.reason,
                locale=locale,
                files=[*files, *presentation.files()],
            )
            if message is None:
                raise ui.discord.DeliveryAbandoned
            handle = ui.discord.delivery.handle_for(message, mode=presentation.mode)
            return ui.discord.DeliveryReceipt(message, handle)

        return privately

    ephemeral = visibility == "personal" and personal(ctx)
    return ui.discord.reply_to(ctx, ephemeral=ephemeral, files=files)


def contribute(
    nodes: ui.DocumentLike,
    *,
    to: discord.ui.LayoutView,
    followed_by: Sequence[discord.ui.Item[Any]] = (),
    locale: str | None = None,
    strict: bool = False,
) -> ui.discord.AttachedFragment:
    """Contribute a Squid region to a hand-assembled view, through the bot's chrome.

    `followed_by` carries the rows the host adds after the Squid region: they are costed
    into the plan and placed here, so the view proven legal is the view that gets sent.
    """
    return ui.discord.contribute(
        nodes,
        to=to,
        followed_by=followed_by,
        chrome=CHROME,
        localization=localization_for(locale),
        strict=strict,
    )


def render_item(
    node: ui.LayoutNode,
    *,
    locale: str | None = None,
    reservation: ui.discord.ResourceCost = ui.discord.EMPTY_RESERVATION,
) -> discord.ui.Item[Any]:
    """Render one node to a detached item, for composition into a larger layout.

    Prefer `contribute`, which measures the host and places the result atomically. This
    stays for callers that build the surrounding view themselves and know their own budget.
    """
    view = render_static([node], locale=locale, reservation=reservation)
    item = view.children[0]
    view.remove_item(item)
    return item


def render_static(
    nodes: ui.DocumentLike,
    *,
    locale: str | None = None,
    strict: bool = False,
    reservation: ui.discord.ResourceCost = ui.discord.EMPTY_RESERVATION,
) -> discord.ui.LayoutView:
    """Render a sessionless document through the bot's chrome and catalogue."""
    # `.layout` rather than the whole presentation: the bot is entirely on Components V2,
    # where the layout *is* the message, and every caller here wants a view to send.
    return ui.discord.render_static(
        nodes,
        chrome=CHROME,
        localization=localization_for(locale),
        strict=strict,
        reservation=reservation,
    ).layout


def truncate_display_text(content: str, limit: int) -> str:
    """Fit text into a Discord display budget with an explicit marker."""
    if len(content) <= limit:
        return content
    if limit <= 1:
        return "\u2026"[:limit]
    return content[: limit - 1].rstrip() + "\u2026"


async def _component_error_hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
    # Imported lazily: errors.py -> utils.components -> this module would otherwise cycle.
    from squid.bot.errors import handle_interaction_error

    await handle_interaction_error(interaction, error, surface=f"component:{source}")


MOUNT_DEFAULTS = ui.discord.MountDefaults(chrome=CHROME, on_error=_component_error_hook)


def create_mount(
    component: ui.Component,
    *,
    access: ui.discord.AccessPolicy,
    locale: str | None = None,
    chrome: ui.semantic.Chrome | None = None,
    timeout: float = 180,
    reactor: ui.discord.Reactor | None = None,
    expiry: ui.discord.ExpiryPolicy | None = _DEFAULT_EXPIRY,
) -> ui.discord.Mount:
    """A mount wired to the bot's chrome and shared interaction error handler."""
    defaults = MOUNT_DEFAULTS if chrome is None else MOUNT_DEFAULTS.replace(chrome=chrome)
    return defaults.mount(
        component,
        access=access,
        localization=localization_for(locale),
        timeout=timeout,
        scheduler=reactor,
        expiry=expiry,
    )


async def send_component(
    ctx: Context[Any],
    component: ui.Component,
    *,
    access: ui.discord.AccessPolicy,
    locale: str | None = None,
    timeout: float = 180,
    visibility: Visibility = "public",
    reactor: ui.discord.Reactor | None = None,
) -> ui.discord.Mount:
    """Mount a component and send it as the reply to a command.

    Pass ``reactor`` for a panel that must react to something another mount changes -- a
    shared namespace, or a bot topic. Without one the mount is refreshed only by its own
    clicks.
    """
    mount = create_mount(component, access=access, locale=locale, timeout=timeout, reactor=reactor)
    await mount.send(destination(ctx, visibility=visibility, locale=locale))
    return mount


class PagedList(ui.Component):
    """A card holding one page of a pre-rendered list, plus the controls to walk it.

    The reactive successor to `squid.bot.utils.pagination.ListPaginator`: `page_size` entries
    per page is a deliberate UX pin, expressed as the engine's count-based `Paginate`.
    Passing ``None`` lets the engine fill each page from the target's measured text budget.
    The mount owns paging, its access policy, and expiry. It does not fetch — every caller
    holds its whole list before rendering.
    """

    def __init__(
        self,
        title: str,
        entries: Sequence[str],
        *,
        empty: str,
        locale: str | None = None,
        page_size: int | None = 10,
        separator: str = "\n\n",
        accent_colour: int = DISCORD_GREEN,
    ) -> None:
        self.title = title
        self.entries = tuple(entries)
        self.empty = empty
        self.locale = locale
        self.page_size = None if page_size is None else max(1, page_size)
        self.separator = separator
        self.accent_colour = accent_colour

    def render(self) -> Sequence[ui.primitives.Node]:
        # An entry list that fits on one page produces no pager, and so no controls: a row of
        # two dead buttons reads as a broken control rather than as an absent one.
        body: ui.primitives.Node = (
            ui.primitives.Lines(
                self.entries,
                join=self.separator,
                overflow=ui.primitives.Paginate(key="entries", per=self.page_size, footer=self._page_footer),
            )
            if self.entries
            else ui.primitives.Text(self.empty)
        )
        return [ui.primitives.Panel(children=(ui.primitives.Heading(self.title), body), accent=self.accent_colour)]

    def _page_footer(self, page: int, pages: int) -> ui.text.Message:
        total = len(self.entries)
        return L(t"Page {page} of {pages} · {total} in total")

    async def send(self, ctx: Context[Any], *, visibility: Visibility = "public") -> ui.discord.Mount:
        """Send the first page bound to a mount that owns paging, access, and expiry."""
        return await send_component(
            ctx,
            self,
            access=ui.discord.Owner(ctx.author.id) if ctx.author else ui.discord.Everyone(),
            locale=self.locale,
            visibility=visibility,
        )


def _fields(fields: Sequence[CardField]) -> tuple[ui.semantic.Field, ...]:
    return tuple(ui.field(field.name, field.value) for field in fields)


def _groups(sections: Sequence[CardSection]) -> tuple[ui.semantic.Section, ...]:
    # A nested section per group: each field steps its own Condense ladder independently
    # rather than a whole group stepping in lockstep — finer-grained, not a regression.
    return tuple(ui.section(ui.heading(s.title), ui.fields(*_fields(s.fields))) for s in sections if s.fields)


def card_layout(
    title: ui.TextLike,
    description: ui.TextLike | None = None,
    *,
    accent_colour: int = DISCORD_GREEN,
    fields: Sequence[CardField] = (),
    sections: Sequence[CardSection] = (),
    footer: ui.TextLike | None = None,
    media: Sequence[str] = (),
    locale: str | None = None,
) -> discord.ui.LayoutView:
    """Create a standalone V2 card."""
    extra_media = media[1:]
    node = ui.section(
        ui.heading(title),
        # The body is the card's shock absorber: truncate lets it give up characters under
        # pressure before a field or the footer loses any.
        description and ui.truncate(ui.paragraph(description)),
        # `fields`/`extra_media` are tuples: an empty one is falsy but not `False`, and
        # `_children` only skips `None`/`False`, so the truthiness check must be explicit.
        bool(fields) and ui.fields(*_fields(fields)),
        *_groups(sections),
        bool(extra_media) and ui.media(*extra_media, key="media"),
        footer and ui.note(footer),
        accent=accent_colour,
        thumbnail=media[0] if media else None,
    )
    return render_static([node], locale=locale)


def text_layout(
    content: ui.TextLike, *, accent_colour: int | None = None, locale: str | None = None
) -> discord.ui.LayoutView:
    """Create a simple V2 text response."""
    # Truncate-wrapped rather than bare: a plain paragraph lowers to Never, which *raises*
    # on an overlong message. This is the bot's most-used reply path, so it clips.
    node: ui.LayoutNode = ui.truncate(ui.paragraph(content))
    if accent_colour is not None:
        node = ui.block(node, accent=accent_colour)
    return render_static([node], locale=locale)


def _prefixed(prefix: str, value: ui.TextLike) -> ui.TextLike:
    if isinstance(value, ui.text.Message):
        plural = None if value.plural is None else prefix + value.plural
        return ui.text.Message(prefix + value.template, value.params, value.dialect, plural)
    if isinstance(value, ui.text.ResolvedText):
        return ui.text.ResolvedText(prefix + value.content, value.dialect)
    return prefix + value


def error_layout(
    title: ui.TextLike, description: ui.TextLike | None, *, locale: str | None = None
) -> discord.ui.LayoutView:
    return card_layout(
        title,
        _prefixed(":x: ", description or ""),
        accent_colour=DISCORD_RED,
        locale=locale,
    )


def warning_layout(
    title: ui.TextLike, description: ui.TextLike | None, *, locale: str | None = None
) -> discord.ui.LayoutView:
    return card_layout(
        _prefixed(":warning: ", title),
        description,
        accent_colour=DISCORD_YELLOW,
        locale=locale,
    )


def info_layout(
    title: ui.TextLike, description: ui.TextLike | None, *, locale: str | None = None
) -> discord.ui.LayoutView:
    return card_layout(title, description, accent_colour=DISCORD_GREEN, locale=locale)


def help_layout(
    title: ui.TextLike,
    description: ui.TextLike | None,
    *,
    sections: Sequence[CardSection] = (),
    footer: ui.TextLike | None = None,
    locale: str | None = None,
) -> discord.ui.LayoutView:
    return card_layout(
        title,
        description,
        accent_colour=DISCORD_BLUE,
        sections=sections,
        footer=footer,
        locale=locale,
    )


def link_layout(
    title: ui.TextLike,
    url: str,
    *,
    description: ui.TextLike | None = None,
    label: ui.TextLike = _OPEN_LINK,
    locale: str | None = None,
) -> discord.ui.LayoutView:
    """Create a card whose primary action opens a URL."""
    node = ui.section(
        ui.heading(title),
        description and ui.truncate(ui.paragraph(description)),
        ui.actions(ui.link(label, url, key="open-link"), key="link"),
        accent=DISCORD_GREEN,
    )
    return render_static([node], locale=locale)
