"""Frontend-neutral capability tags negotiated between a target and its planner.

A capability names one thing a frontend can render: an embed, a native entity picker, a modal.
Targets declare which ones they support and planning branches on them, so the vocabulary lives
here rather than as independently retyped literals at each declaration and comparison site.
"""

from enum import StrEnum


class Capability(StrEnum):
    ACTIONS_BUTTONS = "actions.buttons"
    ACTIONS_ENTITY = "actions.entity"
    ACTIONS_DISCORD_PREMIUM = "actions.discord.premium"
    ACTIONS_SELECT = "actions.select"
    FORMS_DISCORD_CHECKBOX_GROUP = "forms.discord.checkbox_group"
    FORMS_DISCORD_ENTITY = "forms.discord.entity"
    FORMS_DISCORD_FILE = "forms.discord.file"
    FORMS_MODAL = "forms.modal"
    FORMS_INLINE = "forms.inline"
    LAYOUT_CONTAINER = "layout.container"
    LAYOUT_ALERT = "layout.alert"
    LAYOUT_CARD = "layout.card"
    LAYOUT_CAROUSEL = "layout.carousel"
    LAYOUT_EMBED = "layout.embed"
    LAYOUT_EMBED_FIELDS = "layout.embed_fields"
    LAYOUT_GALLERY = "layout.gallery"
    LAYOUT_SECTION = "layout.section"
    LAYOUT_SEMANTIC = "layout.semantic"
    LAYOUT_TABLE = "layout.table"
    MESSAGE_CONTENT = "message.content"
