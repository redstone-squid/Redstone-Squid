"""A module that contains the base view and buttons for the navigation UI."""
# Code from https://gist.github.com/trevorflahardy/6910cd684be4d5c36a913dc954895842 with medium modifications.

from __future__ import annotations

import abc
import functools
from collections.abc import Awaitable
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Concatenate,
    Final,
    Self,
    cast,
    override,
)

import discord
from discord.ui import Item
from discord.utils import maybe_coroutine

if TYPE_CHECKING:
    type BaseViewInit[**P, T] = Callable[Concatenate["BaseNavigableView[Any]", P], T]

type MaybeAwaitable[T] = T | Awaitable[T]
type MaybeAwaitableFunc[**P, T] = Callable[P, MaybeAwaitable[T]]
type MaybeAwaitableBaseNavigableViewFunc[ClientT: discord.Client] = MaybeAwaitableFunc[[], BaseNavigableView[ClientT]]


QUESTION_MARK: Final[str] = "\N{BLACK QUESTION MARK ORNAMENT}"
HOME: Final[str] = "\N{HOUSE BUILDING}"
NON_MARKDOWN_INFORMATION_SOURCE: Final[str] = "\N{INFORMATION SOURCE}"


async def resolve_parent[ClientT: discord.Client](
    parent: BaseNavigableView[ClientT] | MaybeAwaitableBaseNavigableViewFunc[ClientT],
) -> BaseNavigableView[ClientT]:
    """Resolves the parent view."""
    if callable(parent):
        return await maybe_coroutine(parent)
    return parent


class BaseNavigableView[ClientT: discord.Client](discord.ui.View, abc.ABC):
    """
    A view which adds the ability to navigate through a tree of views.

    This is achieved by making views aware of their parent view, and adding buttons to go back, go home, and stop.
    As a result of adding these buttons, subclass of this view should not use row 4 for their own buttons.
    """

    __slots__: tuple[str, ...] = ("parent",)

    def __init__(
        self,
        /,
        parent: BaseNavigableView[ClientT] | MaybeAwaitableBaseNavigableViewFunc[ClientT] | None = None,
        timeout: float | None = 180,
    ) -> None:
        """
        Initializes the navigable view.

        Args:
            parent (BaseNavigableView[ClientT] | None): The parent view of the view. This is used to navigate back.
            timeout (float | None): The timeout of the view.
        """
        self.parent: Final = parent
        super().__init__(timeout=timeout)

    def __init_subclass__(cls: type[Self]) -> None:
        """Wrap the init method of the child view to add the "Stop", "Go Home", and "Go Back" buttons."""
        cls.__init__ = BaseNavigableView._wrap_init(cls.__init__)
        return super().__init_subclass__()

    @staticmethod
    def _wrap_init[T, **P](__init__: BaseViewInit[P, T]) -> BaseViewInit[P, T]:
        """
        A decorator used to wrap the init of an existing child view's __init__ method,
        and then add the "Stop", "Go home", and "Go Back" buttons **always last**.
        """

        @functools.wraps(__init__)
        def wrapped(self: BaseNavigableView[Any], *args: P.args, **kwargs: P.kwargs) -> T:
            result = __init__(self, *args, **kwargs)
            self._add_menu_children()
            return result

        return wrapped

    def _add_menu_children(self) -> None:
        """Add the "Stop", "Go Home", and "Go Back" buttons to the view."""
        # We use super().add_item to allow ourselves to add the buttons to the last row while disallowing the user to do so.
        children_cls = {type(child) for child in self.children}
        if self.parent is not None:
            if BackButton not in children_cls:
                child = BackButton[BaseNavigableView[ClientT], ClientT](self.parent)
                super().add_item(child)

            if HomeButton not in children_cls:
                # self.find_home will never return None if the parent is not None.
                find_home = cast(Callable[[], Awaitable[BaseNavigableView[ClientT]]], self.find_home)
                child = HomeButton[BaseNavigableView[ClientT], ClientT](find_home)
                super().add_item(child)

        if StopButton not in children_cls:
            child = StopButton[Self, ClientT](self)
            super().add_item(child)

    async def find_home(self) -> BaseNavigableView[ClientT] | None:
        """Finds the home parent from a view."""
        if self.parent is None:
            return None

        parent = await resolve_parent(self.parent)

        while True:
            if parent.parent is None:
                return parent
            parent = await resolve_parent(parent.parent)

    async def press_back(self, interaction: discord.Interaction[ClientT]) -> None:
        """Press the back button."""
        back_button = next(button for button in self.children if isinstance(button, BackButton))
        await back_button.callback(interaction)

    async def press_home(self, interaction: discord.Interaction[ClientT]) -> None:
        """Press the home button."""
        home_button = next(button for button in self.children if isinstance(button, HomeButton))
        await home_button.callback(interaction)

    @override
    def add_item(self, item: Item[Any]) -> Self:
        if item.row == 4:
            raise ValueError("Row 4 is reserved for the navigation buttons.")
        return super().add_item(item)

    @abc.abstractmethod
    async def send(self, interaction: discord.Interaction[ClientT]) -> None:
        """Send the view to the interaction."""
        ...

    @abc.abstractmethod
    async def update(self, interaction: discord.Interaction[ClientT]) -> None:
        """Update the view in the interaction."""
        ...


class StopButton[BaseViewT: BaseNavigableView[Any], ClientT: discord.Client](discord.ui.Button[BaseViewT]):
    """A button used to stop the view."""

    __slots__: tuple[str, ...] = ("parent",)

    def __init__(self, parent: BaseViewT | MaybeAwaitableBaseNavigableViewFunc[ClientT]) -> None:
        self.parent = parent
        super().__init__(style=discord.ButtonStyle.danger, label="Stop", row=4)

    @override
    async def callback(self, interaction: discord.Interaction[ClientT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]
        """Disables all the items in the view."""
        parent = await resolve_parent(self.parent)
        for child in parent.children:
            # discord.ui.Item contains no attribute "disabled", but some of its children do.
            # Only discord.ui.Button and discord.ui.Select needs to be disabled, but this is faster than checking.
            child.disabled = True  # type: ignore

        parent.stop()


class HomeButton[BaseViewT: BaseNavigableView[Any], ClientT: discord.Client](discord.ui.Button[BaseViewT]):
    """A button used to go home within the parent tree."""

    __slots__: tuple[str, ...] = ("parent",)

    def __init__(self, parent: BaseViewT | MaybeAwaitableBaseNavigableViewFunc[ClientT]) -> None:
        self.parent = parent
        super().__init__(label="Go Home", emoji=HOME, row=4)

    @override
    async def callback(self, interaction: discord.Interaction[ClientT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]
        """Edits the message with the root view."""
        parent = await resolve_parent(self.parent)
        await parent.update(interaction)


class BackButton[BaseViewT: BaseNavigableView[Any], ClientT: discord.Client](discord.ui.Button[BaseViewT]):
    """A button used to go back within the parent tree."""

    __slots__: tuple[str, ...] = ("parent",)

    def __init__(self, parent: BaseNavigableView[ClientT] | MaybeAwaitableBaseNavigableViewFunc[ClientT]) -> None:
        super().__init__(label="Go Back", row=4)
        self.parent = parent

    @override
    async def callback(self, interaction: discord.Interaction[ClientT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]
        """Edits the message with the parent view."""
        parent = await resolve_parent(self.parent)
        await parent.update(interaction)
