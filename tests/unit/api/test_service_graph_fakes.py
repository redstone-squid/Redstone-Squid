"""Guards that keep the API transport doubles honest about production.

Subclassing each service gives the type checker override-compatibility checking, and
constructing a real `ApiServices` makes a new field a type error. Neither catches a
double that keeps a method production no longer has: such a method type-checks, reads
as covered, and is dead. These tests close that gap.
"""

import inspect
from typing import get_args, get_type_hints

import pytest

from squid.runtime import ApiServices
from tests.unit.api import fakes
from tests.unit.api.fakes import build_services

# Every collaborator now subclasses the service it replaces. Keeping this set named and
# empty means reintroducing an untyped stand-in has to be an explicit edit here.
UNTYPED_STANDINS: set[str] = set()


def _doubles() -> list[tuple[str, type]]:
    """Every double in `fakes` that stands in for a production service by subclassing it."""
    found = [
        (name, value)
        for name, value in vars(fakes).items()
        if inspect.isclass(value) and value.__module__ == fakes.__name__ and value.__mro__[1:2]
    ]
    return [(name, value) for name, value in found if value.__mro__[1] is not object]


def test_doubles_are_discovered() -> None:
    """Fail loudly if the discovery above silently stops finding the doubles."""
    assert len(_doubles()) >= 15


@pytest.mark.parametrize(("name", "double"), _doubles(), ids=lambda value: getattr(value, "__name__", value))
def test_double_defines_no_method_production_lacks(name: str, double: type) -> None:
    """A double may narrow a service's behavior, but not invent a method it does not have."""
    del name
    service = double.__mro__[1]
    phantom = sorted(
        member
        for member in vars(double)
        if not member.startswith("_") and callable(vars(double)[member]) and not hasattr(service, member)
    )
    assert not phantom, f"{double.__name__} defines {phantom}, absent from {service.__name__}"


def test_every_double_really_is_the_service_it_replaces() -> None:
    """Each populated collaborator is an instance of its declared production type.

    Construction alone only proves a value was passed. This proves the value would
    satisfy the routes' declared types, so a double cannot quietly become a stand-in
    for a service it has nothing to do with.
    """
    services = build_services()
    hints = get_type_hints(ApiServices)
    impostors = []
    for field in ApiServices.__dataclass_fields__:
        value = getattr(services, field)
        if value is None:
            continue
        declared = [arg for arg in get_args(hints[field]) or (hints[field],) if arg is not type(None)]
        if not any(isinstance(value, candidate) for candidate in declared):
            impostors.append(field)
    assert set(impostors) == UNTYPED_STANDINS


def test_optional_collaborators_are_the_only_ones_left_empty() -> None:
    """Only fields the API itself treats as optional may be `None` in a transport app."""
    services = build_services()
    empty = {field for field in ApiServices.__dataclass_fields__ if getattr(services, field) is None}
    assert empty == {"api_keys", "web_auth", "vote_members"}
