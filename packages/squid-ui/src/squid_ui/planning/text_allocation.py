"""Target-neutral text allocation for explicitly constrained authored regions."""

from collections.abc import Callable, Sequence


def allocate_pages[ItemT](
    items: Sequence[ItemT],
    *,
    size: Callable[[ItemT], int],
    capacity: int,
    widows: int = 1,
) -> tuple[tuple[ItemT, ...], ...]:
    """Group ordered items into lossless pages under an authored character cap."""
    if capacity < 1:
        message = "page capacity must be positive"
        raise ValueError(message)
    pages: list[list[ItemT]] = []
    current: list[ItemT] = []
    spent = 0
    for item in items:
        cost = size(item)
        if current and spent + cost > capacity:
            pages.append(current)
            current = []
            spent = 0
        current.append(item)
        spent += cost
    if current or not pages:
        pages.append(current)
    if len(pages) > 1 and len(pages[-1]) < widows:
        needed = widows - len(pages[-1])
        movable = max(0, len(pages[-2]) - widows)
        moved = min(needed, movable)
        if moved:
            pages[-1][0:0] = pages[-2][-moved:]
            del pages[-2][-moved:]
    return tuple(tuple(page) for page in pages)


def truncate_text(value: str, capacity: int, *, keep: str = "head") -> tuple[str, int]:
    """Fit text to an authored cap and return the fitted text plus characters omitted."""
    if capacity < 0:
        message = "text capacity must not be negative"
        raise ValueError(message)
    if len(value) <= capacity:
        return value, 0
    if keep == "tail":
        return value[-capacity:] if capacity else "", len(value) - capacity
    return value[:capacity], len(value) - capacity


__all__ = ["allocate_pages", "truncate_text"]
