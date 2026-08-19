"""Discord Components V2 presentation limits as data.

The single source of truth for every hard limit the engine enforces. Exceeding any of these in
a payload makes Discord reject the request with HTTP 400 error code 50035 ("Invalid Form
Body"). Values follow the Discord API docs for Components V2 and modals; the ones discord.py
2.7 validates locally are cross-checked by tests.
"""

from dataclasses import dataclass


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


LIMITS = V2Limits()
"""The limits currently enforced by Discord."""
