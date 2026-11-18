"""Declare content that evaluates against scoped layout facts."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from squid_ui.authoring.document import DocumentLike, as_document
from squid_ui.authoring.semantic import Contextual
from squid_ui.boundary import Boundary

if TYPE_CHECKING:
    from squid_ui.layout import LayoutContext


def context[R, C](
    callback: Callable[[LayoutContext], DocumentLike[R, C]],
    *,
    key: str,
    initial: DocumentLike[R, C] = (),
    components: tuple[Boundary[R, C], ...] = (),
) -> Contextual[R, C]:
    if not key:
        message = "a contextual occurrence requires a nonempty key"
        raise ValueError(message)
    document = as_document(initial)
    if document.assets or document.key is not None:
        message = "context initial content cannot declare document assets or identity"
        raise ValueError(message)
    return Contextual(document.children, key, callback=callback, components=components)
