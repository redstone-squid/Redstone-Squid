from enum import IntEnum, StrEnum


class Status(IntEnum):
    """The status of a submission."""
    PENDING = 0
    CONFIRMED = 1
    DENIED = 2


class Category(StrEnum):
    """The categories of the builds."""
    DOOR = 'Door'
    EXTENDER = 'Extender'
    UTILITY = 'Utility'
    ENTRANCE = 'Entrance'
