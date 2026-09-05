"""Portable visual tokens lowered by target adapters."""

from enum import StrEnum


class ActionStyle(StrEnum):
    """Button colour; lowers to Discord's `ButtonStyle` of the same name and to `squid-button--{value}` in HTML."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    SUCCESS = "success"
    DANGER = "danger"


type Color = int
"""An RGB colour packed as `0xRRGGBB`."""
