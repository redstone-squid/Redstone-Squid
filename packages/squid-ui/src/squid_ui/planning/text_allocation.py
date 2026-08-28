"""Target-neutral text allocation for explicitly constrained authored regions."""


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


__all__ = ["truncate_text"]
