"""Bind one queue delivery to its worker without retaining a usable lease token."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from uuid import UUID

from squid.core.actors import ActingContext

current_claim_reference: ContextVar[str | None] = ContextVar("current_claim_reference", default=None)


@contextmanager
def worker_execution(name: str, claim_token: UUID | None = None) -> Iterator[None]:
    """Start a distinct invocation; its claim reference joins the persisted queue history."""
    reference = sha256(claim_token.bytes).hexdigest() if claim_token is not None else None
    with ActingContext.worker(name).scope(), current_claim_reference.set(reference):
        yield
