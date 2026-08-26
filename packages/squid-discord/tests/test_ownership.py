"""Who owns a stateful node's value: the author, or the presentation session."""

from collections.abc import Awaitable, Callable

import squid_layouts as sl
from squid_discord import DISCORD_V2_DPY27
from squid_layouts import TextLike, scene
from squid_layouts.forms import FormLike, SubmitHandler
from squid_layouts.interactions import ActionPolicy, Actor, SelectionEvent, Visibility
from squid_layouts.planning import plan
from squid_layouts.runtime import PresentationSession
from squid_layouts.semantic import (
    Choice,
    ChoiceEvent,
    Choices,
    Controlled,
    Destination,
    Details,
    Item,
    Items,
    Managed,
    Navigation,
    Paragraph,
)


class _Responder:
    """A frontend that records nothing; the assertions are about who was called."""

    async def acknowledge(self) -> None: ...

    async def notice(self, text: TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None: ...

    async def redirect(self, url: str) -> None: ...

    async def finish(self) -> None: ...

    async def present_form(
        self,
        form: FormLike,
        *,
        key: str = "form",
        on_submit: SubmitHandler | None = None,
        policy: ActionPolicy | None = None,
        label: TextLike = "",
        record=None,
    ) -> None: ...

    def invalidate(self) -> None: ...


async def _fire(result, key: str, event) -> None:
    """Run the binding registered under ``key`` — the one the reader would have clicked."""
    await result.bindings[key].handler(event)


def _select(values: tuple[str, ...]) -> SelectionEvent:
    return SelectionEvent(Actor("7"), _Responder(), values=values)


def _recorder[EventT]() -> tuple[list[EventT], Callable[[EventT], Awaitable[None]]]:
    """A handler plus the events it saw, so a test can assert who the engine called."""
    seen: list[EventT] = []

    async def record(event: EventT) -> None:
        seen.append(event)

    return seen, record


ENTRIES = (
    Item("one", sl.semantic.ItemLabel("One"), (Paragraph("first detail"),), "first"),
    Item("two", sl.semantic.ItemLabel("Two"), (Paragraph("second detail"),), "second"),
)


def _opened(result) -> bool:
    return any(
        isinstance(node, scene.Text) and "second detail" in node.content for node in result.scene.components_v2.children
    )


# --- Items -----------------------------------------------------------------------------


async def test_a_managed_items_seed_opens_an_entry_and_back_gets_out_of_it() -> None:
    """The seed must not re-apply after Back, or the reader is trapped in the entry."""
    session = PresentationSession()
    document = Items("catalog", ENTRIES, Managed("two"))

    assert _opened(plan(document, target=DISCORD_V2_DPY27, session=session))

    await _fire(plan(document, target=DISCORD_V2_DPY27, session=session), "catalog.back", _select(()))

    assert not _opened(plan(document, target=DISCORD_V2_DPY27, session=session))


async def test_a_controlled_items_never_reads_the_session() -> None:
    session = PresentationSession()
    session.select("catalog", ("two",))

    _, record = _recorder()

    assert not _opened(
        plan(Items("catalog", ENTRIES, Controlled(None, record)), target=DISCORD_V2_DPY27, session=session)
    )


async def test_a_controlled_items_reports_both_opening_and_backing_out() -> None:
    session = PresentationSession()
    seen, record = _recorder()

    listing = plan(Items("catalog", ENTRIES, Controlled(None, record)), target=DISCORD_V2_DPY27, session=session)
    await _fire(listing, "catalog.focus", _select(("two",)))

    detail = plan(Items("catalog", ENTRIES, Controlled("two", record)), target=DISCORD_V2_DPY27, session=session)
    await _fire(detail, "catalog.back", _select(()))

    assert [event.opened for event in seen] == ["two", None]
    assert not session.selections


# --- Details ---------------------------------------------------------------------------


def _disclosed(result) -> bool:
    return any(
        isinstance(node, scene.Text) and "hidden body" in node.content for node in result.scene.components_v2.children
    )


async def test_a_managed_details_seed_applies_once_and_then_the_session_owns_it() -> None:
    session = PresentationSession()
    document = Details(
        "debug", sl.semantic.Summary("Debug details"), (Paragraph("hidden body"),), Managed(initial=True)
    )

    assert _disclosed(plan(document, target=DISCORD_V2_DPY27, session=session))

    await _fire(plan(document, target=DISCORD_V2_DPY27, session=session), "debug.toggle", _select(()))

    assert not _disclosed(plan(document, target=DISCORD_V2_DPY27, session=session))


async def test_a_controlled_details_reports_the_requested_state_and_ignores_the_session() -> None:
    session = PresentationSession()
    session.disclose("debug", open_=True)
    seen, record = _recorder()
    document = Details(
        "debug",
        sl.semantic.Summary("Debug details"),
        (Paragraph("hidden body"),),
        Controlled(value=False, on_change=record),
    )

    result = plan(document, target=DISCORD_V2_DPY27, session=session)
    await _fire(result, "debug.toggle", _select(()))

    assert not _disclosed(result)
    assert [event.opened for event in seen] == [True]


# --- Choices and Navigation ------------------------------------------------------------


def _chosen(result) -> tuple[str, ...]:
    select = next(node for node in result.scene.components_v2.children if isinstance(node, scene.Select))
    return tuple(option.value for option in select.options if option.default)


async def test_a_managed_choices_remembers_a_selection_with_no_author_state() -> None:
    session = PresentationSession()
    document = Choices("size", tuple(Choice(str(index), f"Choice {index}") for index in range(6)))

    await _fire(plan(document, target=DISCORD_V2_DPY27, session=session), "size", _select(("3",)))

    assert _chosen(plan(document, target=DISCORD_V2_DPY27, session=session)) == ("3",)


async def test_a_controlled_choices_still_reports_what_changed() -> None:
    session = PresentationSession()
    seen, record = _recorder()
    document = Choices(
        "size",
        tuple(Choice(str(index), f"Choice {index}") for index in range(6)),
        Controlled(("1",), record),
    )

    await _fire(plan(document, target=DISCORD_V2_DPY27, session=session), "size", _select(("3",)))

    assert isinstance(seen[0], ChoiceEvent)
    assert (seen[0].selected, seen[0].added, seen[0].removed) == (("3",), ("3",), ("1",))
    assert not session.selections


async def test_a_managed_navigation_remembers_the_destination_and_defaults_to_the_first() -> None:
    session = PresentationSession()
    document = Navigation("tabs", tuple(Destination(str(index), f"Tab {index}") for index in range(6)))

    assert _chosen(plan(document, target=DISCORD_V2_DPY27, session=session)) == ("0",)

    await _fire(plan(document, target=DISCORD_V2_DPY27, session=session), "tabs", _select(("4",)))

    assert _chosen(plan(document, target=DISCORD_V2_DPY27, session=session)) == ("4",)


async def test_a_managed_navigation_forgets_a_destination_that_went_away() -> None:
    session = PresentationSession()
    session.select("tabs", ("4",))
    document = Navigation("tabs", tuple(Destination(str(index), f"Tab {index}") for index in range(6, 12)))

    assert _chosen(plan(document, target=DISCORD_V2_DPY27, session=session)) == ("6",)
