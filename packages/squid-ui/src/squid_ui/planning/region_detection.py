"""Find explicit regions before selecting a supported production route."""

from collections.abc import Sequence
from typing import Any

from squid_ui.semantic import Region


def contains_regions(nodes: Sequence[Any]) -> bool:
    for node in nodes:
        if isinstance(node, Region):
            return True
        for name in ("children", "header", "footer", "alternates", "choices"):
            children = getattr(node, name, ())
            if isinstance(children, tuple) and contains_regions(children):
                return True
        for name in ("node", "primary", "content"):
            child = getattr(node, name, None)
            if child is not None and contains_regions((child,)):
                return True
    return False
