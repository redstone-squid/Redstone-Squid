"""Discord presentation limits as data, for both message modes.

The single source of truth for every hard limit the engine enforces. Exceeding any of these in
a payload makes Discord reject the request with HTTP 400 error code 50035 ("Invalid Form
Body"). Values follow the Discord API docs for Components V2, classic messages, components,
and modals; the ones discord.py 2.7 validates locally are cross-checked by tests.

Documentation consulted, pinned here so a future reader can re-verify rather than re-guess:

- https://docs.discord.com/developers/resources/message (classic content, embeds)
- https://docs.discord.com/developers/components/reference (rows, buttons, selects, modals)
- https://docs.discord.com/developers/components/overview (the irreversible V2 transition)

Almost none of these are enforced client-side. discord.py checks `len(embeds) > 10` and the
25-child cap on `discord.ui.View`; everything else — the 6,000-character aggregate and every
per-value embed cap — is server-only, which is why the renderer runs a strict payload audit.
"""

from collections.abc import Mapping
from dataclasses import dataclass

ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"

DISPLAY_TEXT = "display_text"
"""Components V2 TextDisplay content, budgeted across the whole message."""

CONTENT_TEXT = "content_text"
"""A classic message's `content` field."""

EMBED_TEXT = "embed_text"
"""Every embed's titles, descriptions, field names and values, footers, and authors."""

TEXT_AXES = frozenset({DISPLAY_TEXT, CONTENT_TEXT, EMBED_TEXT})
"""Every axis that holds message text, whichever target is in play."""

COMPONENTS = "components"
ATTACHMENTS = "attachments"
EMBEDS = "embeds"
ROWS = "rows"
CONTROLS = "controls"


@dataclass(frozen=True, slots=True)
class DiscordLimits:
    """What every Discord message obeys, whichever component mode it is in.

    Row width, control text, custom-ID length, and modal shape are properties of Discord's
    components rather than of a message mode, so they are stated once. A mode-specific
    strategy may not borrow another mode's *message-wide* totals, which is why those live
    on the subclasses.
    """

    attachments: int = 10
    """A conservative library cap. Discord's message docs do not state a number."""

    # ActionRow.
    row_buttons: int = 5
    """Buttons per action row; a select occupies the entire row."""
    button_label: int = 80
    link_url: int = 512

    # Select menus.
    select_options: int = 25
    select_placeholder: int = 150
    option_label: int = 100
    option_value: int = 100
    option_description: int = 100

    # Modals.
    modal_title: int = 45
    modal_components: int = 5
    label_text: int = 45
    label_description: int = 100
    text_input_placeholder: int = 100
    text_input_value: int = 4000
    """Cap on a TextInput's value, default, and max_length."""

    custom_id: int = 100

    @property
    def budgets(self) -> Mapping[str, str]:
        """Every message-wide axis this mode budgets, to the attribute holding its cap.

        The limits own this rather than the target profile, because the caps and the names
        for them have to agree and there is no way to keep two declarations in step.
        """
        raise NotImplementedError

    @property
    def text_axes(self) -> Mapping[str, int]:
        """Every independent text pool this mode budgets, by axis name.

        Independent is the operative word. Two pools do not lend to each other, so the
        allocator runs once per pool over the units tagged to it rather than once over a
        single total.
        """
        raise NotImplementedError

    def fits_controls(self, controls: int, rows: int) -> bool:
        """Whether a message of this mode could hold that many controls in that many rows.

        The semantic adapters ask this to decide whether laying actions out individually is
        even offerable. They must not ask it in one mode's units: a V2 message spends its
        component budget on rows and buttons alike, while a classic message counts view
        children and action rows against separate caps.
        """
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class V2Limits(DiscordLimits):
    """Hard limits for a Components V2 message and its children."""

    # Message-wide budgets.
    total_text: int = 4000
    """Combined length of all TextDisplay content in one message."""
    total_components: int = 40
    """Components per message, counting nested children."""

    # Section and media.
    section_texts: int = 3
    """TextDisplay children per Section, excluding the accessory."""
    gallery_items: int = 10
    gallery_item_description: int = 1024

    @property
    def budgets(self) -> Mapping[str, str]:
        return {DISPLAY_TEXT: "total_text", COMPONENTS: "total_components", ATTACHMENTS: "attachments"}

    @property
    def text_axes(self) -> Mapping[str, int]:
        # Exactly one pool, which is why this looks like ceremony here and stops looking
        # like it the moment a target has two.
        return {DISPLAY_TEXT: self.total_text}

    def fits_controls(self, controls: int, rows: int) -> bool:
        # An ActionRow and each of its buttons are all components against one total.
        return controls + rows <= self.total_components


@dataclass(frozen=True, slots=True)
class ClassicLimits(DiscordLimits):
    """Hard limits for a pre-Components-V2 message: content, embeds, and action rows."""

    # Message-wide budgets.
    content: int = 2000
    """Length of the `content` field. Its own pool: it never borrows from embed text."""
    embed_text: int = 6000
    """Titles, descriptions, field names and values, footers, and author names, added up
    across every embed on the message. Server-enforced only."""
    embeds: int = 10
    rows: int = 5
    controls: int = 25
    """Children a `discord.ui.View` accepts, which is the only one discord.py checks."""

    # Per-embed shape. Local caps like these describe what a legal embed *is*; they are not
    # budget, and reducing one would change what a legal document is rather than how much
    # room is left. They are clamped and validated exactly as `button_label` is.
    embed_title: int = 256
    embed_description: int = 4096
    embed_fields: int = 25
    field_name: int = 256
    field_value: int = 1024
    embed_footer: int = 2048
    embed_author: int = 256

    @property
    def budgets(self) -> Mapping[str, str]:
        return {
            CONTENT_TEXT: "content",
            EMBED_TEXT: "embed_text",
            EMBEDS: "embeds",
            ROWS: "rows",
            CONTROLS: "controls",
            ATTACHMENTS: "attachments",
        }

    @property
    def text_axes(self) -> Mapping[str, int]:
        return {CONTENT_TEXT: self.content, EMBED_TEXT: self.embed_text}

    def fits_controls(self, controls: int, rows: int) -> bool:
        return controls <= self.controls and rows <= self.rows


LIMITS = V2Limits()
"""The Components V2 limits currently enforced by Discord."""

CLASSIC_LIMITS = ClassicLimits()
"""The classic-message limits currently enforced by Discord."""
