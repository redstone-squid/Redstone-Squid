"""Identify the authored children shared by input preparation and semantic indexing."""

from dataclasses import dataclass
from typing import Any

from squid_ui.authoring import semantic as sem
from squid_ui.target_types import Renderable

LAYOUT_CONTAINERS = (
    sem.Group,
    sem.Stack,
    sem.Cluster,
    sem.Themed,
    sem.Block,
    sem.Section,
    sem.Article,
    sem.Aside,
    sem.Region,
)


@dataclass(frozen=True, slots=True)
class LayoutParts:
    children: tuple[Renderable[Any, Any], ...]
    heading: Renderable[Any, Any] | None = None
    header: tuple[Renderable[Any, Any], ...] = ()
    footer: tuple[Renderable[Any, Any], ...] = ()


def layout_parts(value: Renderable[Any, Any]) -> LayoutParts:
    """Return the named slots and ordinary children of a layout container."""
    if not isinstance(value, LAYOUT_CONTAINERS):
        message = f"{type(value).__name__} is not a layout container"
        raise TypeError(message)
    return LayoutParts(
        value.children,
        value.heading if isinstance(value, sem.Section | sem.Article) else None,
        value.header if isinstance(value, sem.Region) else (),
        value.footer if isinstance(value, sem.Region) else (),
    )
