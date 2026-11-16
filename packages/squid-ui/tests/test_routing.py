"""The same route engine handles navigation paths and persistent control IDs."""

import inspect
from typing import Literal, get_type_hints

import pytest

import squid_ui as sl
import squid_ui.routing as routing
from squid_ui.interactions.routing import Route
from squid_ui.routing import AddressParts, AddressRoute, Converter, InvalidAddress, RoutePattern, RouteTable


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


def test_public_routing_namespace_exposes_both_adapters() -> None:
    assert sl.routing is routing
    assert sl.routing.Route is Route
    assert sl.routing.RouteTable is RouteTable


def test_typed_addresses_use_the_same_table_and_converters() -> None:
    def destination(build_id: int, inbox: bool = False, tab: Literal["media", "versions"] | None = None) -> None:
        pass

    address = AddressRoute(
        "/builds/{build_id}", inspect.signature(destination), get_type_hints(destination), fragment="tab"
    )
    table = RouteTable[AddressRoute]()
    table.add(address.pattern, address)
    parts = AddressParts.parse(address.build(12, inbox=True, tab="media"))
    assert parts.path == "/builds/12"
    found = table.match(parts.path[1:])
    assert found is not None
    matched, arguments = found
    assert matched.decode(parts, arguments) == {"build_id": 12, "inbox": True, "tab": "media"}
    assert address.build(12) == "/builds/12"
    assert table.match("builds/0") is None
    with pytest.raises(InvalidAddress):
        address.build(build_id=True)
    with pytest.raises(InvalidAddress, match="Unknown query"):
        matched.decode(AddressParts.parse("/builds/12?other=true"), arguments)


def test_tables_conservatively_reject_unknown_converter_intersections() -> None:
    table = RouteTable[str]()
    table.add(RoutePattern("builds/{id}", separator="/"), "first")
    with pytest.raises(ValueError, match="overlaps"):
        table.add(RoutePattern("builds/latest", separator="/"), "second")
    assert table.match("builds/latest") == ("first", {"id": "latest"})
