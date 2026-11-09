"""Frontend selection follows declared framework edges and leaves application values alone."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

import pytest

from squid_ui.forms import FormBinding, FormSpec, TextField
from squid_ui.handlers import FrontendHandler, HandlerSet, bind_handlers, handlers
from squid_ui.interactions import ActionBinding, ActionEvent, ActionMode, PressEvent
from squid_ui.planning.generated import GeneratedHandler
from squid_ui.planning.navigation import _SeekSelection
from squid_ui.planning.semantic_adaptation.handlers import (
    ChoiceCommit,
    ChooseChoice,
    CloseItem,
    EntityCommit,
    FlipToggle,
    FocusItem,
    ForwardSelection,
    GoToDestination,
    ItemCommit,
    NavigationCommit,
    PresentForm,
    RouteSelection,
    SelectChoices,
    SelectDestination,
    SelectEntities,
    SelectEntityFallback,
    ToggleDetails,
)
from squid_ui.presentation import PresentationState
from squid_ui.semantic import Controlled, Details, Summary, Toggle


async def portable(event: ActionEvent) -> None:
    pass


def registration(frontend: str = "html") -> HandlerSet:
    return handlers(FrontendHandler(frontend, portable))


@dataclass
class ApplicationCallback[ValueT]:
    metadata: object

    async def __call__(self, event: ValueT) -> None:
        pass


def test_application_callbacks_and_form_data_are_opaque() -> None:
    unrelated: dict[str, object] = {"unsupported": registration("elsewhere")}
    unrelated["cycle"] = unrelated
    callback = ApplicationCallback[ActionEvent](unrelated)
    spec = FormSpec("Name", (TextField(key="name", label="Name"),), prefill={"name": unrelated})
    actions = {"action": ActionBinding("action", callback)}
    forms = {"form": FormBinding("form", spec, callback)}

    selected_actions, selected_forms = bind_handlers(actions, forms, "html")

    assert selected_actions is actions
    assert selected_forms is forms
    assert selected_actions["action"].handler is callback
    assert selected_forms["form"].spec.prefill["name"] is unrelated


def test_bindings_routes_and_form_callbacks_preserve_shared_identity() -> None:
    callback = registration()
    spec = FormSpec("Name", (TextField(key="name", label="Name"),), on_invalid=callback)
    present = PresentForm(spec, "form", callback, ActionMode.EXCLUSIVE, "", None)
    routes = {"open": ActionBinding("open", present)}
    router = RouteSelection(routes)
    action = ActionBinding("menu", router, routes=routes)
    form = FormBinding("form", spec, callback)

    actions, forms = bind_handlers({"one": action, "two": action}, {"form": form}, "html")

    assert actions["one"] is actions["two"]
    selected_router = actions["one"].handler
    assert isinstance(selected_router, RouteSelection)
    assert selected_router.routes is actions["one"].routes
    selected_present = selected_router.routes["open"].handler
    assert isinstance(selected_present, PresentForm)
    assert selected_present.spec is forms["form"].spec
    assert selected_present.on_submit is forms["form"].on_submit is callback.branches[0]
    assert selected_present.spec.on_invalid is callback.branches[0]
    assert present.spec is spec
    assert spec.on_invalid is callback


def test_shared_commit_and_ownership_are_selected_once() -> None:
    ownership = Controlled((), registration())
    session = PresentationState()
    first = ChoiceCommit(ownership, "one", (), session)
    second = ChoiceCommit(ownership, "two", (), session)
    actions, _ = bind_handlers(
        {
            "one": ActionBinding("one", ChooseChoice(first, "a")),
            "two": ActionBinding("two", ChooseChoice(first, "b")),
            "three": ActionBinding("three", SelectChoices(second)),
        },
        {},
        "html",
    )
    one, two, three = (actions[key].handler for key in ("one", "two", "three"))
    assert isinstance(one, ChooseChoice)
    assert isinstance(two, ChooseChoice)
    assert isinstance(three, SelectChoices)
    assert one.commit is two.commit
    assert one.commit.ownership is three.commit.ownership
    assert one.commit.session is three.commit.session is session


@pytest.mark.parametrize("details", (False, True))
def test_shared_semantic_nodes_remain_shared(details: bool) -> None:
    session = PresentationState()
    if details:
        node = Details("details", Summary("Details"), (), Controlled(value=False, on_change=registration()))
        first = ToggleDetails(node, open=False, session=session)
        second = ToggleDetails(node, open=True, session=session)
    else:
        node = Toggle("toggle", "Toggle", Controlled(value=False, on_change=registration()))
        first = FlipToggle(node, on=False, session=session)
        second = FlipToggle(node, on=True, session=session)
    actions, _ = bind_handlers({"one": ActionBinding("one", first), "two": ActionBinding("two", second)}, {}, "html")
    one, two = actions["one"].handler, actions["two"].handler
    assert isinstance(one, FlipToggle | ToggleDetails)
    assert isinstance(two, FlipToggle | ToggleDetails)
    assert one.node is two.node
    assert one.node is not node


def generated_adapters(callback: HandlerSet) -> tuple[GeneratedHandler[Any], ...]:
    session = PresentationState()
    choices = ChoiceCommit(Controlled((), callback), "choices", (), session)
    entities = EntityCommit(Controlled((), callback), "entities", (), session)
    items = ItemCommit(Controlled(None, callback), "items", session)
    navigation = NavigationCommit(Controlled("a", callback), "nav", session)
    return (
        ChooseChoice(choices, "a"),
        SelectChoices(choices),
        SelectEntities(entities),
        SelectEntityFallback(entities, {}),
        CloseItem(items),
        FocusItem(items),
        SelectDestination(navigation),
        GoToDestination(navigation, "a"),
        ToggleDetails(
            Details("details", Summary("Details"), (), Controlled(value=False, on_change=callback)),
            open=False,
            session=session,
        ),
        FlipToggle(Toggle("toggle", "Toggle", Controlled(value=False, on_change=callback)), on=False, session=session),
        ForwardSelection(callback, "a"),
    )


@pytest.mark.parametrize("index", range(11))
def test_generated_adapters_validate_nested_frontend_coverage(index: int) -> None:
    adapter = generated_adapters(registration("elsewhere"))[index]
    with pytest.raises(TypeError, match="no handler implementation"):
        bind_handlers({"action": ActionBinding("action", adapter)}, {}, "html")


@pytest.mark.parametrize("present", (False, True))
def test_form_invalid_callback_is_validated_for_both_binding_paths(present: bool) -> None:
    spec = FormSpec("Name", (TextField(key="name", label="Name"),), on_invalid=registration("elsewhere"))
    with pytest.raises(TypeError, match="no handler implementation"):
        if present:
            adapter = PresentForm(spec, "form", portable, ActionMode.EXCLUSIVE, "", None)
            bind_handlers({"form": ActionBinding("form", adapter)}, {}, "html")
        else:
            bind_handlers({}, {"form": FormBinding("form", spec, portable)}, "html")


def test_integer_seek_callback_is_opaque() -> None:
    callback = ApplicationCallback[int]({"unrelated": registration("elsewhere")})
    adapter = _SeekSelection(callback)
    actions = {"seek": ActionBinding("seek", adapter)}
    selected, _ = bind_handlers(actions, {}, "html")
    assert selected is actions
    assert adapter.seek is callback


def test_generated_adapters_must_declare_binding_behavior() -> None:
    class Incomplete(GeneratedHandler[PressEvent]):
        @override
        async def __call__(self, event: PressEvent) -> None:
            pass

    # Calling through a factory exercises the runtime abstract-method contract.
    factory: Callable[[], object] = Incomplete
    with pytest.raises(TypeError, match="_bind_handlers"):
        factory()
