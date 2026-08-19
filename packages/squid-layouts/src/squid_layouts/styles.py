"""Portable visual tokens lowered by target adapters."""

from enum import StrEnum


class ActionStyle(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    SUCCESS = "success"
    DANGER = "danger"


type Color = int
