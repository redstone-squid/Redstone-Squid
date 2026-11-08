"""Build checked bot and worker service graphs for unit tests.

A test supplies doubles for the services it exercises; every other field gets an unconfigured
stand-in. Construction rejects two kinds of drift that a cast would hide:

- a double that is not an instance of the declared service type, so pyrefly never compared its
  methods with production;
- a double defining a public method the service no longer has, which would read as covered while
  being dead.

An unconfigured stand-in is an instance of its service type, but any attribute access fails with
an AssertionError naming the service. A test that reaches a service it did not configure fails at
that point instead of continuing with a permissive mock.
"""

import inspect
from functools import cache
from typing import Any, NoReturn, cast, get_args, get_type_hints
from unittest.mock import NonCallableMock

from squid.runtime import BotServices, WorkerServices


class _Unconfigured:
    """Marks the stand-ins below so harnesses can tell an omitted service from a supplied one."""


@cache
def _unconfigured_type(service: type) -> type:
    def refuse(self: object, name: str) -> object:
        # Dunder reads come from Python itself, for example isinstance() reading __class__.
        if name.startswith("__") and name.endswith("__"):
            return object.__getattribute__(self, name)
        raise AssertionError(f"the test did not configure {service.__name__}; it read .{name}")

    def describe(self: object) -> str:
        return f"<unconfigured {service.__name__}>"

    return type(
        f"Unconfigured{service.__name__}",
        (_Unconfigured, service),
        {"__init__": lambda self: None, "__getattribute__": refuse, "__repr__": describe},
    )


def unconfigured[ServiceT](service: type[ServiceT]) -> ServiceT:
    """Return a stand-in for `service` that fails as soon as code under test uses it."""
    return cast(ServiceT, _unconfigured_type(service)())


def is_configured(service: object) -> bool:
    """Whether `service` is a double the test supplied rather than an unconfigured stand-in."""
    return not isinstance(service, _Unconfigured)


def _declared_types(annotation: object) -> list[type]:
    return [arg for arg in get_args(annotation) or (annotation,) if inspect.isclass(arg) and arg is not type(None)]


def _is_instance(value: object, declared: type) -> bool:
    if getattr(declared, "_is_protocol", False):
        # Most service protocols are not runtime-checkable; a double must name the protocol as a base.
        return declared in type(value).__mro__ or (isinstance(value, NonCallableMock) and value.__class__ is declared)
    return isinstance(value, declared)


def _phantom_methods(value: object, service: type) -> list[str]:
    """Public methods a hand-written double defines below `service` that `service` does not have."""
    if isinstance(value, NonCallableMock):
        return []
    owners = type(value).__mro__[: type(value).__mro__.index(service)]
    return sorted(
        name
        for owner in owners
        for name, member in vars(owner).items()
        if not name.startswith("_") and callable(member) and not hasattr(service, name)
    )


def _check_double(container: type, field: str, value: object) -> None:
    annotation = get_type_hints(container)[field]
    declared = _declared_types(annotation)
    if not declared:
        # Plain callables, such as the worker's maintenance functions, are checked by the type checker only.
        return
    service = next((candidate for candidate in declared if _is_instance(value, candidate)), None)
    if service is None:
        names = " | ".join(candidate.__name__ for candidate in declared)
        raise TypeError(f"{container.__name__}.{field} needs a {names}, got {type(value).__name__}")
    if phantom := _phantom_methods(value, service):
        raise TypeError(f"{type(value).__name__} defines {phantom}, absent from {service.__name__}")


def _build[ContainerT](
    container: type[ContainerT], defaults: dict[str, Any], overrides: dict[str, object]
) -> ContainerT:
    hints = get_type_hints(container)
    unknown = set(overrides) - set(hints)
    if unknown:
        raise TypeError(f"{container.__name__} has no fields {sorted(unknown)}")
    values: dict[str, object] = {}
    for field, annotation in hints.items():
        if field in overrides:
            value = overrides[field]
            if value is not None:
                _check_double(container, field, value)
            values[field] = value
        elif field in defaults:
            values[field] = defaults[field]
        else:
            values[field] = unconfigured(_declared_types(annotation)[0])
    return container(**values)


def bot_services(**overrides: object) -> BotServices:
    """Build a `BotServices` from the given doubles, leaving every other service unconfigured.

    Each double must be an instance of its field's declared type: a subclass of the service or
    an autospec of it.
    """
    return _build(BotServices, {}, overrides)


async def _unconfigured_job() -> NoReturn:
    raise AssertionError("the test did not configure this worker maintenance job")


def worker_services(**overrides: object) -> WorkerServices:
    """Build a `WorkerServices` from the given doubles, leaving every other capability unconfigured.

    Optional collaborators default to `None`, which is how a worker without them is composed.
    Maintenance callables default to coroutines that fail when awaited.
    """
    defaults: dict[str, Any] = {
        "event_wake_listener": None,
        "media_runner": None,
        "refresh_search_index": _unconfigured_job,
        "record_queue_health": _unconfigured_job,
        "purge_idempotency": _unconfigured_job,
        "expire_submission_drafts": _unconfigured_job,
        "cleanup_submission_schematics": _unconfigured_job,
        "cleanup_submission_inference": _unconfigured_job,
    }
    return _build(WorkerServices, defaults, overrides)
