"""Pure domain models (as opposed to all the impure god objects scattering around in the codebase)."""

import abc
import builtins
import inspect
from dataclasses import dataclass, field, Field
from datetime import datetime
from typing import Any, Callable, TypeVar, final, TypedDict, override, ClassVar

from pydantic import BaseModel

from squid.db import schema


_event_cls_registry: dict[str, type["Event"]] = {}


def event_from_sa_event(
    event: schema.Event,
) -> "Event":
    """Create an Event instance from a SQLAlchemy event."""
    if event.type not in _event_cls_registry:
        raise ValueError(f"Unknown event type: {event.type}")

    event_cls = _event_cls_registry[event.type]
    return event_cls.from_sa_event(event)


@dataclass(frozen=True, slots=True, kw_only=True)
class Event(abc.ABC):
    """Represents a custom event in the database."""

    aggregate: str
    aggregate_id: int
    type: ClassVar[str]
    payload: BaseModel

    @classmethod
    @abc.abstractmethod
    def from_sa_event[T](cls: builtins.type[T], event: schema.Event) -> T:
        """Create an Event instance from a SQLAlchemy event."""
        raise NotImplementedError("Subclasses must implement this method.")

    def __init_subclass__(cls, **kwargs: Any):
        """Automatically register subclasses of Event."""
        # Do not call super().__init_subclass__, as the dataclass metaclass tries to check whether the subclass is a
        # dataclass, which it is not when __init_subclass__ is called, because the decorator is applied after the class is created.
        # super().__init_subclass__(**kwargs)

        # Register concrete event classes only
        if inspect.isabstract(cls):
            return

        if not hasattr(cls, "type"):
            raise RuntimeError("Event subclasses must define a 'type' class variable.")

        if cls.type not in _event_cls_registry:
            _event_cls_registry[cls.type] = cls


class VoteSessionClosedPayload(BaseModel):
    """Payload for the VoteSessionClosed event."""

    result: schema.VoteSessionResultLiteral
    closed_at: datetime


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class VoteSessionClosed(Event):
    """Represents a vote session closed event."""

    aggregate = "vote_session"
    type = "vote_session_closed"
    payload: VoteSessionClosedPayload

    @classmethod
    @override
    def from_sa_event(cls, event: schema.Event) -> "VoteSessionClosed":
        """Create a VoteSessionClosed instance from a SQLAlchemy event."""
        assert event.type == cls.type, f"""Event type mismatch: expected {cls.type}, got {event.type}."""
        return cls(
            aggregate=event.aggregate,
            aggregate_id=event.aggregate_id,
            payload=VoteSessionClosedPayload.model_validate(event.payload),
        )
