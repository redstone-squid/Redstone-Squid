"""squid-ui glue: localized chrome, house colours, and the semantic layout vocabulary.

This module is the bot's front door to the `squid_ui` package. The package resolves text,
while this host supplies the gettext catalogue and translatable chrome messages.

The bot owns only localized chrome and audience policy; rendering and delivery stay in
``squid_ui``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from string.templatelib import Template
from typing import Any, Literal

import discord
from discord.ext.commands import Context

import squid_ui as ui
import squid_ui_discord as sd
from squid.core.i18n import catalog_for, negotiate_locale

DISCORD_RED = 0xF04747
DISCORD_YELLOW = 0xFAA61A
DISCORD_GREEN = 0x43B581
DISCORD_BLUE = 0x5865F2
DISCORD_GREY = 0x4F545C
_DEFAULT_EXPIRY = sd.PauseUpdates()

__all__ = [
    "CHROME",
    "DISCORD_BLUE",
    "DISCORD_GREEN",
    "DISCORD_GREY",
    "DISCORD_RED",
    "DISCORD_YELLOW",
    "HOST_DEFAULTS",
    "PALETTES",
    "CardField",
    "CardSection",
    "L",
    "PagedList",
    "Private",
    "Visibility",
    "card_layout",
    "card_node",
    "contribute",
    "create_message_root",
    "error_layout",
    "error_node",
    "help_layout",
    "info_layout",
    "info_node",
    "link_layout",
    "localization_for",
    "message_destination",
    "render_item",
    "render_payload",
    "reply_payload",
    "respond_payload",
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


CHROME = ui.chrome.Chrome(
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

PALETTES = ui.PaletteRegistry(
    {
        "squid": ui.Palette(
            brand=DISCORD_BLUE,
            neutral=DISCORD_GREY,
            info=DISCORD_BLUE,
            success=DISCORD_GREEN,
            warning=DISCORD_YELLOW,
            danger=DISCORD_RED,
        )
    },
    default="squid",
)


@dataclass(frozen=True, slots=True)
class Private:
    """Deliver where a channel can never see it: ephemeral or DM, with `reason` explaining why."""

    reason: str


type Visibility = Private | Literal["public", "personal"]


async def reply_payload(
    ctx: Context[Any],
    payload: sd.message_payload.MessagePayload,
    *,
    visibility: Visibility = "public",
    allowed_mentions: discord.AllowedMentions | None = None,
    files: Sequence[discord.File] = (),
) -> sd.delivery.DeliveryResult:
    """Deliver a complete Squid payload through the selected command audience."""
    from squid.bot.utils.visibility import personal

    if isinstance(visibility, Private):
        from squid.bot.utils.visibility import deliver_privately

        message = await deliver_privately(
            ctx,
            payload,
            reason=visibility.reason,
            allowed_mentions=allowed_mentions,
            files=files,
        )
        if message is None:
            raise sd.delivery.DeliveryAbandoned
        handle = sd.delivery.handle_for(message, mode=payload.mode)
        return sd.delivery.DeliveryResult(message, handle)

    message_destination = sd.reply_to(
        ctx,
        ephemeral=visibility == "personal" and personal(ctx),
        files=files,
        allowed_mentions=allowed_mentions,
    )
    return await message_destination(payload)


async def respond_payload(
    interaction: discord.Interaction[Any],
    payload: sd.MessagePayload,
    *,
    ephemeral: bool = True,
    wait: bool = False,
    allowed_mentions: discord.AllowedMentions | None = None,
) -> sd.delivery.DeliveryResult:
    """Deliver a complete payload as an interaction response or follow-up."""
    return await sd.respond_to(
        interaction,
        ephemeral=ephemeral,
        wait=wait,
        allowed_mentions=allowed_mentions,
    )(payload)


def message_destination(
    ctx: Context[Any],
    *,
    visibility: Visibility = "public",
    locale: str | None = None,
    files: Sequence[discord.File] = (),
) -> sd.MessageDestination:
    """Where a mount's first message goes, in the same vocabulary `reply` uses.

    The audience rule stays host-side: "public" answers in the channel, "personal" is
    ephemeral where the transport allows it, and `Private(reason)` must never reach a channel
    at all. `files` are the host's own attachments; the mount adds its rendered assets.

    A closed DM under `Private` delivers nothing, which is not the same as delivering without
    a handle, so it is reported as `DeliveryAbandoned` rather than as a `None` message.
    """
    # Imported lazily to keep the command UI helpers independent from audience policy.
    from squid.bot.utils.visibility import deliver_privately, personal

    if isinstance(visibility, Private):
        if ctx.interaction is not None:
            return sd.reply_to(ctx, ephemeral=True, files=files)

        async def privately(
            payload: sd.message_payload.MessagePayload,
        ) -> sd.delivery.DeliveryResult:
            message = await deliver_privately(
                ctx,
                payload,
                reason=visibility.reason,
                locale=locale,
                files=files,
            )
            if message is None:
                raise sd.delivery.DeliveryAbandoned
            handle = sd.delivery.handle_for(message, mode=payload.mode)
            return sd.delivery.DeliveryResult(message, handle)

        return privately

    ephemeral = visibility == "personal" and personal(ctx)
    return sd.reply_to(ctx, ephemeral=ephemeral, files=files)


def contribute(
    nodes: ui.DocumentLike[ui.ComponentsV2Target],
    *,
    to: discord.ui.LayoutView,
    followed_by: Sequence[discord.ui.Item[Any]] = (),
    locale: str | None = None,
    strict: bool = False,
) -> sd.fragments.AttachedFragment:
    """Contribute a Squid region to a hand-assembled view, through the bot's chrome.

    `followed_by` carries the rows the host adds after the Squid region: they are costed
    into the plan and placed here, so the view proven legal is the view that gets sent.
    """
    return sd.contribute(
        nodes,
        to=to,
        followed_by=followed_by,
        chrome=CHROME,
        localization=localization_for(locale),
        palette=PALETTES.resolve(),
        strict=strict,
    )


def render_item(
    node: ui.LayoutNode[ui.ComponentsV2Target],
    *,
    locale: str | None = None,
    reservation: sd.ResourceCost = sd.EMPTY_RESERVATION,
) -> discord.ui.Item[Any]:
    """Render one node to a detached item through the bot's chrome and catalogue."""
    return sd.render_item(
        node,
        chrome=CHROME,
        localization=localization_for(locale),
        palette=PALETTES.resolve(),
        reservation=reservation,
    )


def render_payload(
    nodes: ui.DocumentLike[ui.ComponentsV2Target],
    *,
    locale: str | None = None,
    strict: bool = False,
    reservation: sd.ResourceCost = sd.EMPTY_RESERVATION,
) -> sd.message_payload.MessagePayload:
    """Render a complete Discord payload through the bot's chrome and catalogue."""
    return sd.render_static(
        nodes,
        chrome=CHROME,
        localization=localization_for(locale),
        palette=PALETTES.resolve(),
        strict=strict,
        reservation=reservation,
    )


def truncate_display_text(content: str, limit: int) -> str:
    """Fit text into a Discord display budget with an explicit marker."""
    if len(content) <= limit:
        return content
    if limit <= 1:
        return "\u2026"[:limit]
    return content[: limit - 1].rstrip() + "\u2026"


async def _component_error_hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
    # Imported lazily to keep error handling independent from the command UI catalogue.
    from squid.bot.errors import handle_interaction_error

    await handle_interaction_error(interaction, error, surface=f"component:{source}")


HOST_DEFAULTS = sd.MessageRootDefaults(chrome=CHROME, palette=PALETTES.resolve(), on_error=_component_error_hook)
"""What the bot installs with: the chrome and error handling every panel shares.

Only the half that can be written down as a value. The other half -- a challenge presenter,
which needs the session registry and the background runner -- is assembled by
`sd.install` and reached back through `ClientRuntime.of`, so a panel built from a click
gets the same wiring as one opened through `bot.mounts`.
"""


def create_message_root(
    component: ui.Component[ui.ComponentsV2Target],
    *,
    source: sd.runtime.RuntimeSource,
    access: sd.AccessPolicy,
    locale: str | None = None,
    chrome: ui.chrome.Chrome | None = None,
    timeout: float | None = 180,
    scheduler: sd.MessageRootScheduler | None = None,
    expiry: sd.message_root.ExpiryPolicy | None = _DEFAULT_EXPIRY,
) -> sd.MessageRoot:
    """A mount wired to the bot's chrome and shared interaction error handler.

    `source` is whatever names the bot -- the client, the interaction, or the command context
    the panel is being built for. It is what finds the installed host, and so the challenge
    presenter a guard needs.

    `scheduler` stays explicit rather than inherited from the host: a panel is refreshed only by
    its own clicks unless it says it reacts to something else.
    """
    defaults = sd.ClientRuntime.of(source).defaults
    if chrome is not None:
        defaults = defaults.replace(chrome=chrome)
    return defaults.mount(
        component,
        access=access,
        localization=localization_for(locale),
        timeout=timeout,
        scheduler=scheduler,
        expiry=expiry,
    )


async def send_component(
    ctx: Context[Any],
    component: ui.Component[ui.ComponentsV2Target],
    *,
    access: sd.AccessPolicy,
    locale: str | None = None,
    timeout: float = 180,
    visibility: Visibility = "public",
    scheduler: sd.MessageRootScheduler | None = None,
) -> sd.MessageRoot:
    """MessageRoot a component and send it as the reply to a command.

    Pass ``scheduler`` for a panel that must react to something another mount changes -- a
    shared namespace, or a bot topic. Without one the mount is refreshed only by its own
    clicks.
    """
    message_root = create_message_root(
        component, source=ctx, access=access, locale=locale, timeout=timeout, scheduler=scheduler
    )
    await message_root.send(message_destination(ctx, visibility=visibility, locale=locale))
    return message_root


class PagedList(ui.Component[ui.ComponentsV2Target]):
    """A card holding one page of a pre-rendered list, plus the controls to walk it.

    The reactive page primitive: `page_size` entries
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

    def render(self) -> Sequence[ui.LayoutNode[ui.ComponentsV2Target]]:
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

    async def send(self, ctx: Context[Any], *, visibility: Visibility = "public") -> sd.MessageRoot:
        """Send the first page bound to a mount that owns paging, access, and expiry."""
        return await send_component(
            ctx,
            self,
            access=sd.Owner(ctx.author.id) if ctx.author else sd.Everyone(),
            locale=self.locale,
            visibility=visibility,
        )


def _fields(fields: Sequence[CardField]) -> tuple[ui.semantic.Field, ...]:
    return tuple(ui.field(field.name, field.value) for field in fields)


def _groups(sections: Sequence[CardSection]) -> tuple[ui.semantic.Section, ...]:
    # A nested section per group: each field steps its own Condense ladder independently
    # rather than a whole group stepping in lockstep — finer-grained, not a regression.
    return tuple(ui.section(ui.heading(s.title), ui.fields(*_fields(s.fields))) for s in sections if s.fields)


def card_node(
    title: ui.TextLike,
    description: ui.TextLike | None = None,
    *,
    accent_colour: int = DISCORD_GREEN,
    fields: Sequence[CardField] = (),
    sections: Sequence[CardSection] = (),
    footer: ui.TextLike | None = None,
    media: Sequence[str] = (),
) -> ui.LayoutNode[ui.ComponentsV2Target]:
    """Build a semantic card that can be composed inside a component render."""
    extra_media = media[1:]
    return ui.section(
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
) -> sd.message_payload.MessagePayload:
    """Create a standalone V2 card."""
    return render_payload(
        [
            card_node(
                title,
                description,
                accent_colour=accent_colour,
                fields=fields,
                sections=sections,
                footer=footer,
                media=media,
            )
        ],
        locale=locale,
    )


def text_layout(
    content: ui.TextLike, *, accent_colour: int | None = None, locale: str | None = None
) -> sd.message_payload.MessagePayload:
    """Create a simple V2 text response."""
    # Truncate-wrapped rather than bare: a plain paragraph lowers to Never, which *raises*
    # on an overlong message. This is the bot's most-used reply path, so it clips.
    node: ui.LayoutNode[ui.ComponentsV2Target] = ui.truncate(ui.paragraph(content))
    if accent_colour is not None:
        node = ui.block(node, accent=accent_colour)
    return render_payload([node], locale=locale)


def _prefixed(prefix: str, value: ui.TextLike) -> ui.TextLike:
    if isinstance(value, ui.text.Message):
        plural = None if value.plural is None else prefix + value.plural
        return ui.text.Message(prefix + value.template, value.params, value.markup, plural)
    if isinstance(value, ui.text.ResolvedText):
        return ui.text.ResolvedText(prefix + value.content, value.markup)
    return prefix + value


def error_layout(
    title: ui.TextLike, description: ui.TextLike | None, *, locale: str | None = None
) -> sd.message_payload.MessagePayload:
    return render_payload([error_node(title, description)], locale=locale)


def error_node(title: ui.TextLike, description: ui.TextLike | None) -> ui.LayoutNode[ui.ComponentsV2Target]:
    """Build an error card for composition inside a component render."""
    return card_node(
        title,
        _prefixed(":x: ", description or ""),
        accent_colour=DISCORD_RED,
    )


def warning_layout(
    title: ui.TextLike, description: ui.TextLike | None, *, locale: str | None = None
) -> sd.message_payload.MessagePayload:
    return card_layout(
        _prefixed(":warning: ", title),
        description,
        accent_colour=DISCORD_YELLOW,
        locale=locale,
    )


def info_layout(
    title: ui.TextLike, description: ui.TextLike | None, *, locale: str | None = None
) -> sd.message_payload.MessagePayload:
    return render_payload([info_node(title, description)], locale=locale)


def info_node(title: ui.TextLike, description: ui.TextLike | None) -> ui.LayoutNode[ui.ComponentsV2Target]:
    """Build an informational card for composition inside a component render."""
    return card_node(title, description, accent_colour=DISCORD_GREEN)


def help_layout(
    title: ui.TextLike,
    description: ui.TextLike | None,
    *,
    sections: Sequence[CardSection] = (),
    footer: ui.TextLike | None = None,
    locale: str | None = None,
) -> sd.message_payload.MessagePayload:
    return card_layout(
        title,
        description,
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
) -> sd.message_payload.MessagePayload:
    """Create a card whose primary action opens a URL."""
    node = ui.section(
        ui.heading(title),
        description and ui.truncate(ui.paragraph(description)),
        ui.action_controls(ui.link(label, url, key="open-link"), key="link"),
        accent=DISCORD_GREEN,
    )
    return render_payload([node], locale=locale)
