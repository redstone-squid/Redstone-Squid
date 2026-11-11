"""Parse human-entered build facts independently of their input interface."""

import re

from squid.builds.domain.models import parse_time_string

_BARE_NUMBER = re.compile(r"[~≈]?\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)")


def parse_build_ticks(text: str) -> int:
    """Parse "30gt", "15rt", or "1.5s" into game ticks, insisting on a unit.

    parse_time_string reads a bare number as seconds, but submitters often mean ticks,
    so the form asks for the unit instead of guessing. The manifest's help text says so too.
    """
    if _BARE_NUMBER.fullmatch(text.strip()):
        message = "Add a unit: gt for game ticks, rt for redstone ticks, or s for seconds."
        raise ValueError(message)
    ticks = parse_time_string(text)
    if ticks is None or ticks < 0:
        message = "Enter a time such as 30gt, 15rt, or 1.5s."
        raise ValueError(message)
    return ticks
