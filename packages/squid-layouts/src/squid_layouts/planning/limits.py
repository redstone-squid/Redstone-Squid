"""Discord Components V2 presentation limits as data.

The single source of truth for every hard limit the engine enforces. Exceeding any of these in
a payload makes Discord reject the request with HTTP 400 error code 50035 ("Invalid Form
Body"). Values follow the Discord API docs for Components V2 and modals; the ones discord.py
2.7 validates locally are cross-checked by tests.
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


@dataclass(frozen=True, slots=True)
class V2Limits:
    """Hard limits for a Components V2 message and its children."""

    # Message-wide budgets.
    total_text: int = 4000
    """Combined length of all TextDisplay content in one message."""
    total_components: int = 40
    """Components per message, counting nested children."""
    attachments: int = 10

    # ActionRow.
    row_buttons: int = 5
    """Buttons per action row; a select occupies the entire row."""
    button_label: int = 80

    # Select menus.
    select_options: int = 25
    select_placeholder: int = 150
    option_label: int = 100
    option_value: int = 100
    option_description: int = 100

    # Section and media.
    section_texts: int = 3
    """TextDisplay children per Section, excluding the accessory."""
    gallery_items: int = 10
    gallery_item_description: int = 256

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
        """Every message-wide axis this target budgets, to the attribute holding its cap.

        The limits own this rather than the target profile, because the caps and the names
        for them have to agree and there is no way to keep two declarations in step.
        """
        return {DISPLAY_TEXT: "total_text", COMPONENTS: "total_components", ATTACHMENTS: "attachments"}

    @property
    def text_axes(self) -> Mapping[str, int]:
        """Every independent text pool this target budgets, by axis name.

        Independent is the operative word. Two pools do not lend to each other, so the
        allocator runs once per pool over the units tagged to it rather than once over a
        single total. Components V2 has exactly one pool, which is why this looks like
        ceremony here and stops looking like it the moment a target has two.
        """
        return {DISPLAY_TEXT: self.total_text}


LIMITS = V2Limits()
"""The limits currently enforced by Discord."""
