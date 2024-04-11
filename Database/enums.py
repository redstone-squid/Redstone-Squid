from enum import IntEnum


class Status(IntEnum):
    """The status of a submission."""
    PENDING = 0
    CONFIRMED = 1
    DENIED = 2
