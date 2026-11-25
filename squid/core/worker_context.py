"""Bind one queue delivery to its worker without retaining a usable lease token."""

from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from squid.core.actors import ActingContext

current_claim_reference: ContextVar[str | None] = ContextVar("current_claim_reference", default=None)


@contextmanager
def worker_execution(name: str, claim_token: UUID | None = None) -> Iterator[ActingContext]:
    """Start a distinct invocation; its claim reference joins the persisted queue history."""
    reference = sha256(claim_token.bytes).hexdigest() if claim_token is not None else None
    context = ActingContext.worker(name)
    with context.scope(), current_claim_reference.set(reference):
        yield context


@contextmanager
def worker_scope(context: ActingContext) -> Iterator[None]:
    """Enter a worker-only application operation using its supplied execution identity."""
    context.require_worker()
    with context.scope():
        yield


class WorkerOperation[T](Protocol):
    """A registered worker callback that receives this execution's identity."""

    def __call__(self, *, context: ActingContext) -> Awaitable[T]: ...
