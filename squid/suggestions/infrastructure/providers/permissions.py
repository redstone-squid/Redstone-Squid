"""Suggestion providers over the permission node catalogue.

The catalogue is a frozen in-memory structure, so these need no caching and no I/O — but they do
belong in the registry rather than in a cog, because permission patterns are also written through
the API and will eventually be written from an admin UI.
"""

from squid.permissions.domain.catalogue import CATALOGUE
from squid.suggestions.application import Candidate, candidate
from squid.suggestions.domain import SuggestionRequest

WILDCARD = "**"


class PermissionNodeProvider:
    """Suggest concrete permission nodes."""

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return tuple(
            candidate(node.name, description=node.scope.value, kind="permission_node")
            for node in sorted(CATALOGUE, key=lambda node: node.name)
        )


class PermissionPatternProvider:
    """Suggest anything a grant may be written against: nodes, wildcards, and tag selectors."""

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        patterns: dict[str, str] = {WILDCARD: "every permission"}
        for node in CATALOGUE:
            patterns[node.name] = node.scope.value
            segments = node.name.split(".")
            for depth in range(1, len(segments)):
                prefix = ".".join(segments[:depth])
                patterns.setdefault(f"{prefix}.{WILDCARD}", f"every {prefix} permission")
            for tag in node.tags:
                patterns.setdefault(f"@{tag.value}", f"every permission tagged {tag.value}")
        return tuple(
            candidate(pattern, description=description, kind="permission_pattern")
            for pattern, description in sorted(patterns.items())
        )
