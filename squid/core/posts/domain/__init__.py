"""Public bot-owned Discord post domain API."""

from squid.core.posts.domain.models import (
    PostReference,
    PostTarget,
    ResourceKind,
    StarboardEntryKey,
    Surface,
)

__all__ = [
    "PostReference",
    "PostTarget",
    "ResourceKind",
    "StarboardEntryKey",
    "Surface",
]
