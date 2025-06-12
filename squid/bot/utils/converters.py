"""Converter utilities for discord.py."""

from __future__ import annotations

import inspect
from types import FrameType

from discord.ext.commands import FlagConverter


def fix_converter_annotations[_FlagConverter: type[FlagConverter]](cls: _FlagConverter) -> _FlagConverter:
    """
    Fixes discord.py being unable to evaluate annotations if `from __future__ import annotations` is used AND the `FlagConverter` is a nested class.

    This works because discord.py uses the globals() and locals() function to evaluate annotations at runtime.
    See https://discord.com/channels/336642139381301249/1328967235523317862 for more information about this.
    """
    previous_frame: FrameType = inspect.currentframe().f_back  # type: ignore
    previous_frame.f_globals[cls.__name__] = cls
    return cls 