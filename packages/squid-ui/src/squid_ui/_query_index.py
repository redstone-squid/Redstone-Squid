"""Storage for a render's lazily built query index, outside its serialized data."""


class QueryIndexOwner:
    """Keep derived query data out of dataclass fields, equality, and serialization.

    Frozen slotted dataclasses inherit this slot without initializing it. Their generated
    copy/pickle state includes only declared fields, so copies start without an index.
    """

    __slots__ = ("_query_index",)
    _query_index: object
