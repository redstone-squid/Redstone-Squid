"""Public bot-owned Discord post domain API."""

from squid.posts.domain.models import (
    PostReference,
    PostTarget,
    ResourceKind,
    Surface,
    starboard_entry_key,
)

__all__ = [
    "PostReference",
    "PostTarget",
    "ResourceKind",
    "Surface",
    "starboard_entry_key",
]
