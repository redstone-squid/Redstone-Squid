"""Direct smoke contracts for every application machine advertised in the README."""

import squid_ui as sl
import squid_ui_widgets as sp


async def _finished(_event: sp.TransitionEvent[sp.DecisionState]) -> None:
    return None


def _form(title: str = "Value") -> sl.forms.FormSpec:
    return sl.forms.FormSpec(title, (sl.forms.TextField(key="value", label="Value"),))


def _assert_routed_render[StateT](machine: sp.StateMachine[StateT]) -> None:
    assert sp.RouteDriver(lambda request: f"route:{request.action}").render(machine, machine.initial_state)


def test_pure_machines_render_through_component_and_route_drivers() -> None:
    decision = sp.Decision("Choose", (sp.DecisionOption("one", "One"),))
    collection = sp.CollectionEditor("Items", create=_form(), label=lambda value: str(value["value"]))
    editor = sp.Editor("Edit", (sp.EditorSection.from_form("value", "Value", _form()),))
    menu = sp.Menu("Menu", (sp.MenuEntry("entry", "Entry", "Body"),))
    tabs = sp.Tabs((sp.Tab("one", "One", "Body"),), key="tabs")
    choices = sp.MultiChoice(
        "Choices",
        (sp.MultiChoiceGroup("group", "Group", (sl.semantic.Choice("one", "One"),)),),
    )
    ranked = sp.RankedList(("A", "B"), key="ranked", label=str, value=lambda value: len(value))
    wizard = sp.Wizard("Wizard", (sp.WizardStep("value", "Value", _form()),), review=True)

    machines = (decision, collection, editor, menu, tabs, choices, ranked, wizard)
    for machine in machines:
        component = machine.build_component()
        assert component.render()

    _assert_routed_render(decision)
    _assert_routed_render(collection)
    _assert_routed_render(editor)
    _assert_routed_render(menu)
    _assert_routed_render(tabs)
    _assert_routed_render(choices)
    _assert_routed_render(ranked)
    _assert_routed_render(wizard)

    assert sp.confirm("Confirm?", on_confirm=_finished).render()


async def test_resource_backed_and_actor_keyed_widgets_render_directly() -> None:
    source = sl.sources.list_source(("one", "two"))

    async def picked(_event: sl.interactions.ActionEvent, _items: tuple[str, ...]) -> None:
        return None

    agreement = sp.Agreement("Approve?", (sp.AgreementParticipant("one", "One"),))
    browser = sp.Browser(source, identity=str, label=str, detail=lambda item: item)
    search = sp.SearchPicker(
        lambda _query: source,
        identity=str,
        label=str,
        on_pick=picked,
    )
    ranking = sp.SourceRankedList(source, key="ranking", page_size=10, identity=str, label=str)

    assert agreement.render()
    assert browser.render()
    assert search.render()
    assert ranking.render()


def test_state_machine_protocol_is_satisfied_by_the_advertised_pure_machines() -> None:
    machine: sp.StateMachine[sp.DecisionState] = sp.Decision(
        "Choose",
        (sp.DecisionOption("one", "One"),),
    )
    assert machine.transition(machine.initial_state, "choose:one").decided == "one"

    route = sp.TransitionRoute("choose:one", machine.initial_state, "next")
    assert isinstance(route.state, sp.DecisionState)
