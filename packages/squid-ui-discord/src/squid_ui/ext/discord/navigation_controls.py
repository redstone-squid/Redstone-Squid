"""Native Discord pager controls built from shared navigation state."""

from collections.abc import Awaitable, Callable, Sequence

from squid_ui.content.text import TextLike
from squid_ui.ext.discord.primitives.nodes import (
    Button,
    FormButton,
    Gallery,
    Option,
    RawItem,
    RoutedButton,
    RoutedSelect,
    Row,
    SelectMenu,
    Sep,
    Thumbnail,
)
from squid_ui.ext.discord.target_types import DiscordTarget
from squid_ui.interactions import ActionMode
from squid_ui.interactions.forms import FormBinding
from squid_ui.planning.pagination.navigation import (
    NavigationContext,
    PresentPageForm,
    SeekPageSelection,
    SubmitPageJump,
    page_jump_form,
)
from squid_ui.target_types import Renderable

SEEK_OPTION_LIMIT = 25

type NavNode = Row | SelectMenu | RoutedSelect | Sep | Thumbnail | Gallery | RawItem
type MountNavNode = Row | SelectMenu | RoutedSelect | RawItem
type NavFactory = Callable[[NavigationContext], Sequence[Renderable[DiscordTarget]]]
type PlannedNav = Callable[[NavigationContext], Sequence[NavNode]]


def navigation_controls(context: NavigationContext, *extra: Button | RoutedButton) -> Row:
    """Build stable boundary-aware controls from the facts in ``context``."""
    state = context.state

    def button(
        label: TextLike, binding: Callable[..., Awaitable[None]] | str, key: str, available: bool
    ) -> Button | RoutedButton:
        if isinstance(binding, str):
            return RoutedButton(label, binding, disabled=not available)
        return Button(label=label, on_click=binding, key=key, disabled=not available)

    previous = (
        (button(state.previous_label, context.on_previous, state.previous_key, state.has_previous),)
        if state.backward
        else ()
    )
    items = (*previous, button(state.next_label, context.on_next, state.next_key, state.has_next), *extra)
    if (size := context.fetch_size) is not None:
        form = FormBinding(size.key, size.spec, size.on_submit, size.mode, size.label)
        items = (*items, FormButton(size.label, PresentPageForm(form), form.key, mode=form.mode, form=form))
    return Row(items)


def default_nav(context: NavigationContext) -> Sequence[MountNavNode]:
    """The stock factory shared by materialized pages and source windows."""
    return (navigation_controls(context),)


def _seek_pages(page: int, extent: int) -> list[int]:
    """Which zero-based pages a jump select should offer, always including ``page``."""
    if extent <= SEEK_OPTION_LIMIT:
        return list(range(extent))
    # Spread jump choices across the whole range so distant pages remain accessible.
    step = (extent - 1) / (SEEK_OPTION_LIMIT - 1)
    ladder = {round(index * step) for index in range(SEEK_OPTION_LIMIT)}
    if page not in ladder:
        # Trade the nearest rung for the visible page, so the select can show it as selected
        # without growing past the option limit. The ends stay: they are the whole range.
        ladder.discard(min(ladder - {0, extent - 1}, key=lambda rung: abs(rung - page)))
        ladder.add(page)
    return sorted(ladder)


def seek_control(context: NavigationContext) -> SelectMenu | RoutedSelect | None:
    """A jump select for a cursor that can address a page, or `None` when it cannot."""
    state = context.state
    seek = context.on_seek
    extent, page = state.extent, state.index
    if seek is None or extent is None or page is None or extent < 2 or state.page_option is None:
        return None
    label = state.page_option

    options = tuple(
        Option(label(number + 1), str(number), default=number == page) for number in _seek_pages(page, extent)
    )
    if isinstance(seek, str):
        return RoutedSelect(options, seek, placeholder=state.seek_label or None)
    return SelectMenu(
        options, on_select=SeekPageSelection(seek), key=state.seek_key, placeholder=state.seek_label or None
    )


def page_select_nav(context: NavigationContext) -> Sequence[MountNavNode]:
    """`default_nav` plus a jump select wherever the cursor can address a page.

    Opt in per mount (`MessageRoot(..., nav=sl.page_select_nav)`): a select is a whole component
    row, and spending one on every paginator in the process is the host's call, not the
    framework's.
    """
    control = seek_control(context)
    return (navigation_controls(context),) if control is None else (navigation_controls(context), control)


def page_jump_nav(context: NavigationContext) -> Sequence[MountNavNode]:
    """Previous/Next plus a form-opening button for an addressable pager.

    Opt in with ``MessageRoot(..., nav=page_jump_nav)``. The form accepts every
    page, including pages beyond a select menu's option limit.
    """
    state = context.state
    seek = context.on_seek
    if state.index is None or state.extent is None or state.extent < 2:
        return (navigation_controls(context),)
    label = state.seek_label or context.chrome.jump_to_page
    if context.jump_route is not None:
        return (navigation_controls(context, RoutedButton(label, context.jump_route)),)
    if not callable(seek):
        return (navigation_controls(context),)
    form = FormBinding(
        f"{state.seek_key}.jump",
        page_jump_form(state.index, state.extent, title=label, chrome=context.chrome),
        SubmitPageJump(seek),
        ActionMode.REBASE,
        label,
        unavailable=context.chrome.page_jump_unavailable,
    )
    button = FormButton(label, PresentPageForm(form), form.key, mode=form.mode, form=form)
    return (navigation_controls(context, button),)
