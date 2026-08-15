"""Public bot-owned Discord post domain API."""

from squid.posts.domain.models import DiscordPost, PostReference, PostTarget, ResourceKind, Surface

__all__ = ["DiscordPost", "PostReference", "PostTarget", "ResourceKind", "Surface"]
