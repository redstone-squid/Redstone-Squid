"""Server settings domain values."""

from typing import Literal, TypedDict


class SettingOptions(TypedDict, total=False, closed=True):
    """Channel IDs by setting name; None means that channel is not configured."""

    Smallest: int | None
    Fastest: int | None
    First: int | None
    Builds: int | None
    Vote: int | None


type ScalarChannelSetting = Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
type Setting = Literal["Smallest", "Fastest", "First", "Builds", "Vote"]
"""Names of configurable channel destinations. Permissions are managed separately."""
