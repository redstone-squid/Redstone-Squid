"""Map portable action tone and emphasis to shared control styles."""

from squid_ui.authoring.semantic import Emphasis, Tone
from squid_ui.palette import Role
from squid_ui.styles import ActionStyle


def button_style(tone: Tone | Role, emphasis: Emphasis) -> ActionStyle:
    return {
        Tone.SUCCESS: ActionStyle.SUCCESS,
        Tone.DANGER: ActionStyle.DANGER,
        Tone.INFO: ActionStyle.PRIMARY,
    }.get(tone.base, ActionStyle.PRIMARY if emphasis is Emphasis.STRONG else ActionStyle.SECONDARY)
