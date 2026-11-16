"""The same route engine handles navigation paths and persistent control IDs."""

import pytest

from squid_ui.interactions.routing import Route
from squid_ui.routing import Converter, RoutePattern, RouteTable


@pytest.mark.parametrize("separator", [":", "/"])
def test_pattern_round_trip_and_aliases(separator: str) -> None:
    route = RoutePattern(
        f"builds{separator}{{id:int}}",
        aliases=(f"old{separator}{{id:int}}",),
        separator=separator,
        converter_types={"int": Converter("int", r"[0-9]+", int, str)},
    )
    assert route.match(route.build(id=12)) == {"id": 12}
    assert route.match(f"old{separator}12") == {"id": 12}
    assert route.match(f"builds{separator}bad") is None


def test_parameter_converters_come_from_the_declaring_adapter() -> None:
    pattern = RoutePattern(
        "builds/{id}", separator="/", parameter_converters={"id": Converter("id", r"[0-9]+", int, str)}
    )
    assert pattern.match(pattern.build(id=12)) == {"id": 12}
    with pytest.raises(ValueError):
        pattern.build(id="one/two")


def test_table_rejects_alias_collisions_and_resolves_endpoints() -> None:
    table = RouteTable[str]()
    table.add(Route("build:{id:int}", aliases=("old:{id:int}",)), "build")
    assert table.match("old:12") == ("build", {"id": 12})
    assert table.match("other:12") is None
    with pytest.raises(ValueError, match="overlaps"):
        table.add(Route("old:{name}"), "other")
    assert table.match("build:12") == ("build", {"id": 12})


def test_control_routes_remain_hashable() -> None:
    assert len({Route("build:{id:int}"), Route("build:{id:int}")}) == 1
