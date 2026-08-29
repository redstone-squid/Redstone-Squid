"""Dependency-leaf identity for one target extension.

Its own module because both ends need it and neither may import the other: the `Extension`
node lives in `primitives`, the adapter that prepares it in `planning`, and
`tests/architecture/test_boundaries.py` keeps the public target seam clear of `primitives`.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExtensionKind[PayloadT = object, ResourceT = object]:
    """One extension's wire name, the payload that authors it, and what it draws to.

    The parameters are what the bare string could not carry. An extension is a pairing --
    `discord.item` is authored with a zero-argument factory and produces a `discord.ui.Item`
    -- and with the kind spelled as a string, neither half was checked: the node's payload
    was `object`, and the adapter re-derived what it had been handed at runtime.

    Declare one as a module-level constant beside the adapter that answers for it, the way
    `ContextKey` and `GuardKind` are declared. `name` stays the spelling that crosses the
    wire and forms the capability string, so a scene is unaffected by any of this.
    """

    name: str

    def __str__(self) -> str:
        return self.name
