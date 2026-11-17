"""Traverse display edges declared by scene values."""

from collections.abc import Iterator

from squid_ui.scene.base import SceneNode


def is_scene_node(node: object) -> bool:
    return isinstance(node, SceneNode)


def scene_children(node: object) -> Iterator[tuple[str, object]]:
    if not isinstance(node, SceneNode):
        return
    for name in node.DISPLAY_FIELDS:
        yield from _values(name, getattr(node, name))


def _values(path: str, value: object) -> Iterator[tuple[str, object]]:
    if isinstance(value, tuple):
        for index, child in enumerate(value):
            yield from _values(f"{path}.{index}", child)
    elif value is not None:
        yield path, value
