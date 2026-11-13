"""Offer permission nodes, wildcard prefixes, and tag selectors from the code catalogue."""

from squid.permissions.domain.catalogue import CATALOGUE
from squid.suggestions.application import Candidate

WILDCARD = "**"


def permission_node_candidates() -> tuple[Candidate, ...]:
    """Suggest concrete permission nodes."""
    return tuple(
        Candidate.create(node.name, description=node.scope.value, kind="permission_node")
        for node in sorted(CATALOGUE, key=lambda node: node.name)
    )


def permission_pattern_candidates() -> tuple[Candidate, ...]:
    """Suggest anything a grant may be written against: nodes, wildcards, and tag selectors."""
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
        Candidate.create(pattern, description=description, kind="permission_pattern")
        for pattern, description in sorted(patterns.items())
    )
