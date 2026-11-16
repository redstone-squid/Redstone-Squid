"""Optional navigation to public locations within an interface."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Focusable[Target](Protocol):
    """Reveal named content, independently of keyboard focus or delivery target.

    Each call changes the current location once; rendering must not reapply it.
    Implementations may defer a request until loading completes. Calling focus
    again replaces that request or changes the location of an already loaded item.
    Targets describe public locations, not paths through the rendered tree.
    """

    def focus_targets(self) -> frozenset[Target]:
        """Return the locations currently available to this instance."""
        ...

    def focus(self, target: Target) -> None:
        """Reveal a location, raising ValueError if it is unavailable.

        This changes presentation state without firing user-action callbacks.
        The host remains responsible for delivering the resulting render.
        """
        ...
