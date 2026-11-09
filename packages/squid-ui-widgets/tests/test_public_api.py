"""Public namespace and packaging contracts for the machine library."""

import squid_ui_widgets as sp
from squid_ui import testing as engine


def test_the_confirm_guard_composes_with_portable_guards() -> None:
    import squid_ui as sl

    assert sl.guards.all_of(sl.guards.cooldown(5), sp.guards.confirm("Sure?"))


def test_patterns_import_without_a_transport_installed() -> None:
    """Block frontend and host imports to expose accidental package dependencies."""
    engine.assert_imports_without(
        ["squid_ui_widgets", "squid_ui_widgets.guards", "squid_ui_widgets.machines"],
        "discord",
        "anyio",
        "squid_storage",
        "squid_ui_discord",
    )


def test_component_and_machine_entrypoints_are_distinct() -> None:
    import squid_ui as sl

    for component, machine in ((sp.Tabs, sp.machines.Tabs), (sp.Wizard, sp.machines.Wizard)):
        assert issubclass(component, sl.Component)
        assert not issubclass(machine, sl.Component)
        assert not hasattr(component, "build_component")
        assert not hasattr(machine, "build_component")
