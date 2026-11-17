"""Discord protocol and renderer behavior declarations."""

from enum import StrEnum


class DiscordAdapterCapability(StrEnum):
    """Behavior verified by this target's renderer adapters."""

    RENDER_V2 = "adapter.discord.render.components-v2"
    RENDER_CLASSIC = "adapter.discord.render.classic"
    DISPATCH = "adapter.discord.dispatch"
    INTERACTION_DELIVERY = "adapter.discord.interaction-delivery"
    MODAL_FORMS = "adapter.discord.modal-forms"


class DiscordCapability(StrEnum):
    """Native message and form features outside the shared semantic vocabulary."""

    ACTIONS_PREMIUM = "actions.discord.premium"
    FORMS_CHECKBOX_GROUP = "forms.discord.checkbox_group"
    FORMS_ENTITY = "forms.discord.entity"
    FORMS_FILE = "forms.discord.file"
    LAYOUT_EMBED = "layout.embed"
    LAYOUT_EMBED_FIELDS = "layout.embed_fields"
    MESSAGE_CONTENT = "message.content"
