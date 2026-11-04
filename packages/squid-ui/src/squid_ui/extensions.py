"""Typed extension identities shared by authoring nodes and planner adapters.

Keeping the identity here lets both layers import it without depending on each other.
"""

from dataclasses import dataclass
from typing import override


@dataclass(frozen=True, slots=True)
class ExtensionKind[PayloadT = object, ResourceT = object]:
    """Identify an extension and relate its authored payload to its prepared resource type.

    Declare a module-level constant beside its adapter. The name is the stable wire
    identifier and contributes to the extension's capability string; type parameters
    check the payload/resource pairing without changing that encoding.
    """

    name: str

    @override
    def __str__(self) -> str:
        return self.name
