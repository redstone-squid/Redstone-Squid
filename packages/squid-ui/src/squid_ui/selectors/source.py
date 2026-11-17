"""A committed tree that live selector queries can follow."""

from squid_reactivity import Signal


class QueryRoot:
    """Publish trees after successful delivery; captures retain their old index.

    Reading a query tracks this root as a reactive dependency. Updating an
    identical tree does not invalidate readers. Captures pin membership, not
    the mutable application objects referenced by matches.
    """

    def __init__(self, tree: object = ()) -> None:
        self._tree = tree
        self._changed = Signal()

    def read(self) -> object:
        self._changed.track()
        return self._tree

    def publish(self, tree: object) -> None:
        if tree is self._tree:
            return
        self._tree = tree
        self._changed.notify()
