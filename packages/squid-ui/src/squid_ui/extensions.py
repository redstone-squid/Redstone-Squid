"""Dependency-leaf identity for one target extension.

Its own module because both ends need it and neither may import the other: the `Extension`
node lives in `primitives`, the adapter that prepares it in `planning`, and
`tests/architecture/test_boundaries.py` keeps the public target seam clear of `primitives`.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExtensionKind[PayloadT = object, ResourceT = object]:
    """One extension's wire name, the payload that authors it, and what it draws to.

    `PayloadT` types the `Extension` node's payload and `ResourceT` what the adapter's `prepare`
    produces, so both halves of the pairing are checked statically: `discord.item` is
    `ExtensionKind[ItemFactory, discord.ui.Item]`. Declare one as a module-level constant beside
    the adapter that answers for it, the way `ContextKey` and `GuardKind` are declared.
    """

    name: str
    """The spelling that crosses the wire and forms the `extension.<name>` capability string."""

    def __str__(self) -> str:
        return self.name
