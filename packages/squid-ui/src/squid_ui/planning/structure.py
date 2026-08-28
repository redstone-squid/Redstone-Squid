"""The structural rules every semantic traversal states identically, stated once.

The HTML compiler, the Discord lowering pass, and the decision nomination walk all visit the
same semantic tree, and each had grown its own copy of the grammar that names a position in
it. A spelling corrected in one walk stayed wrong in the others, and the planner's
path-keyed state only holds together while every walk agrees on the spelling.

What belongs here is target-neutral by definition: how a child's path is derived from its
parent's, and how a fallback branch is named and selected. How a target reacts to the node
at that path does not.
"""

from collections.abc import Iterator, Mapping, Sequence


def indexed_children[T](children: Sequence[T], path: str) -> Iterator[tuple[T, str]]:
    """Pair each child with its position-derived path under the parent's."""
    for index, child in enumerate(children):
        yield child, f"{path}.{index}"


def branch_paths(path: str, branches: int) -> tuple[str, ...]:
    """Give each semantic fallback branch a stable path."""
    return (f"{path}.primary", *(f"{path}.alternate.{index}" for index in range(branches - 1)))


def fallback_rung(path: str, branches: int, selected: Mapping[str, int]) -> int:
    """Return one validated selected fallback rung."""
    rung = selected.get(path, 0)
    if not 0 <= rung < branches:
        message = f"{path}: planner selected unavailable fallback branch {rung}"
        raise ValueError(message)
    return rung


__all__ = ["branch_paths", "fallback_rung", "indexed_children"]
