"""A module that contains the base view and buttons for the navigation UI."""
# Code from https://gist.github.com/trevorflahardy/6910cd684be4d5c36a913dc954895842 with medium modifications.

import abc
import functools
from collections.abc import Awaitable, Callable
from typing import (
    Any,
    Concatenate,
    Final,
    Self,
    cast,
    override,
)

import discord
from discord.utils import maybe_coroutine

from squid.bot.errors import ErrorHandledLayoutView
from squid.bot.utils.components import edit_interaction_layout

type BaseViewInit[**P, T] = Callable[Concatenate["BaseNavigableView[Any]", P], T]
type MaybeAwaitable[T] = T | Awaitable[T]
type MaybeAwaitableFunc[**P, T] = Callable[P, MaybeAwaitable[T]]
type MaybeAwaitableBaseNavigableViewFunc[ClientT: discord.Client] = MaybeAwaitableFunc[[], BaseNavigableView[ClientT]]


QUESTION_MARK: Final[str] = "\N{BLACK QUESTION MARK ORNAMENT}"
HOME: Final[str] = "\N{HOUSE BUILDING}"
NON_MARKDOWN_INFORMATION_SOURCE: Final[str] = "\N{INFORMATION SOURCE}"


async def resolve_parent[ClientT: discord.Client](
    parent: "BaseNavigableView[ClientT] | MaybeAwaitableBaseNavigableViewFunc[ClientT]",
) -> "BaseNavigableView[ClientT]":
    """Resolves the parent view."""
    if callable(parent):
        return await maybe_coroutine(parent)
    return parent


class BaseNavigableView[ClientT: discord.Client](ErrorHandledLayoutView, abc.ABC):
    """
    A view which adds the ability to navigate through a tree of views.

    This is achieved by making views aware of their parent view, and adding buttons to go back, go home, and stop.
    As a result of adding these buttons, subclass of this view should not use row 4 for their own buttons.
    """

    __slots__: tuple[str, ...] = ("_navigation_row", "parent")

    def __init__(
        self,
        /,
        parent: "BaseNavigableView[ClientT] | MaybeAwaitableBaseNavigableViewFunc[ClientT] | None" = None,
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

    def __init_subclass__(cls: "type[BaseNavigableView[Any]]") -> None:
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
        row = discord.ui.ActionRow()
        if self.parent is not None:
            row.add_item(BackButton[BaseNavigableView[ClientT], ClientT](self.parent))
            find_home = cast(Callable[[], Awaitable[BaseNavigableView[ClientT]]], self.find_home)
            row.add_item(HomeButton[BaseNavigableView[ClientT], ClientT](find_home))

        row.add_item(StopButton[Self, ClientT](self))
        self._navigation_row = row
        super().add_item(row)

    async def find_home(self) -> "BaseNavigableView[ClientT] | None":
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
        back_button = next(button for button in self.children if isinstance(button, BackButton))  # pyright: ignore [reportUnknownVariableType]
        await back_button.callback(interaction)

    async def press_home(self, interaction: discord.Interaction[ClientT]) -> None:
        """Press the home button."""
        home_button = next(button for button in self.children if isinstance(button, HomeButton))  # pyright: ignore [reportUnknownVariableType]
        await home_button.callback(interaction)

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

    __slots__: tuple[str, ...] = ("_navigation_parent",)

    def __init__(self, parent: BaseViewT | MaybeAwaitableBaseNavigableViewFunc[ClientT]) -> None:
        self._navigation_parent = parent
        super().__init__(style=discord.ButtonStyle.danger, label="Stop")

    @override
    async def callback(self, interaction: discord.Interaction[ClientT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]
        """Disables all the items in the view."""
        parent = await resolve_parent(self._navigation_parent)
        for child in parent.walk_children():
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                child.disabled = True

        parent.stop()
        await edit_interaction_layout(interaction, parent)


class HomeButton[BaseViewT: BaseNavigableView[Any], ClientT: discord.Client](discord.ui.Button[BaseViewT]):
    """A button used to go home within the parent tree."""

    __slots__: tuple[str, ...] = ("_navigation_parent",)

    def __init__(self, parent: BaseViewT | MaybeAwaitableBaseNavigableViewFunc[ClientT]) -> None:
        self._navigation_parent = parent
        super().__init__(label="Go Home", emoji=HOME)

    @override
    async def callback(self, interaction: discord.Interaction[ClientT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]
        """Edits the message with the root view."""
        parent = await resolve_parent(self._navigation_parent)
        await parent.update(interaction)


class BackButton[BaseViewT: BaseNavigableView[Any], ClientT: discord.Client](discord.ui.Button[BaseViewT]):
    """A button used to go back within the parent tree."""

    __slots__: tuple[str, ...] = ("_navigation_parent",)

    def __init__(self, parent: BaseNavigableView[ClientT] | MaybeAwaitableBaseNavigableViewFunc[ClientT]) -> None:
        super().__init__(label="Go Back")
        self._navigation_parent = parent

    @override
    async def callback(self, interaction: discord.Interaction[ClientT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]
        """Edits the message with the parent view."""
        parent = await resolve_parent(self._navigation_parent)
        await parent.update(interaction)
