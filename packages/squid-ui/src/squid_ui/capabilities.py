"""Shared semantic capabilities; targets may add their own native string tags."""

from enum import StrEnum


class Capability(StrEnum):
    ACTIONS_BUTTONS = "actions.buttons"
    ACTIONS_ENTITY = "actions.entity"
    ACTIONS_SELECT = "actions.select"
    FORMS_MODAL = "forms.modal"
    FORMS_INLINE = "forms.inline"
    LAYOUT_CONTAINER = "layout.container"
    LAYOUT_ALERT = "layout.alert"
    LAYOUT_CARD = "layout.card"
    LAYOUT_CAROUSEL = "layout.carousel"
    LAYOUT_GALLERY = "layout.gallery"
    LAYOUT_SECTION = "layout.section"
    LAYOUT_SEMANTIC = "layout.semantic"
    LAYOUT_TABLE = "layout.table"
