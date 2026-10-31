"""Public bot-owned Discord post domain API."""

from squid.posts.domain.models import (
    DiscordPost,
    PostReference,
    PostTarget,
    ResourceKind,
    Surface,
    starboard_entry_key,
)

__all__ = [
    "DiscordPost",
    "PostReference",
    "PostTarget",
    "ResourceKind",
    "Surface",
    "starboard_entry_key",
]
