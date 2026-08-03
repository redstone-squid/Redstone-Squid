"""Detection of the optional native schematic engine.

The engine is an optional dependency with no musl or linux-aarch64 wheels, so every entry
point must tolerate its absence.
"""

import importlib.util
from functools import cache

ENGINE_MODULE = "nucleation"


@cache
def engine_installed() -> bool:
    """Return whether the native schematic engine is importable.

    Uses :func:`importlib.util.find_spec` rather than a trial import on purpose: importing a
    native extension is expensive, and an ABI-mismatched wheel can abort the interpreter
    outright. The real import happens only inside the worker subprocess, where a failure is
    contained and reported back as a capability answer.
    """
    try:
        return importlib.util.find_spec(ENGINE_MODULE) is not None
    except (ImportError, ValueError):
        # A shadowed or half-installed distribution can leave find_spec raising rather than
        # returning None; treat that as "not usable" instead of taking the bot down.
        return False
