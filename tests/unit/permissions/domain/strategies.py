"""Generate permission rules from the application catalogue.

New catalogue entries become candidates for the property tests without updating a
separate test catalogue. Hypothesis samples these candidates; it does not enumerate
every possible rule set.
"""

import itertools

from hypothesis import strategies as st

from squid.permissions.domain import CATALOGUE, Effect, Origin, Pattern, Rule, SubjectKind

NODE_NAMES: tuple[str, ...] = CATALOGUE.names
GUILD_ID = 4242


def selectors_for(name: str) -> tuple[str, ...]:
    """Generate exact, subtree, single-segment wildcard, and tag selectors for a node."""
    segments = name.split(".")
    found = {name, "**"}
    for depth in range(1, len(segments)):
        found.add(".".join(segments[:depth]) + ".**")
    for count in range(1, len(segments) + 1):
        for positions in itertools.combinations(range(len(segments)), count):
            wildcarded = list(segments)
            for position in positions:
                wildcarded[position] = "*"
            found.add(".".join(wildcarded))
    for tag in CATALOGUE[name].tags:
        found.add(f"@{tag.value}")
    return tuple(sorted(found))


ALL_SELECTORS: tuple[str, ...] = tuple(sorted({p for name in NODE_NAMES for p in selectors_for(name)}))


def node_names() -> st.SearchStrategy[str]:
    """Any node name from the catalogue."""
    return st.sampled_from(NODE_NAMES)


def patterns() -> st.SearchStrategy[str]:
    """Sample a generated selector that matches at least one catalogue node."""
    return st.sampled_from(ALL_SELECTORS)


def effects(*, include_forbid: bool = True) -> st.SearchStrategy[Effect]:
    allowed = list(Effect) if include_forbid else [Effect.ALLOW, Effect.DENY]
    return st.sampled_from(allowed)


@st.composite
def rules(
    draw: st.DrawFn,
    *,
    pattern: st.SearchStrategy[str] | None = None,
    include_forbid: bool = True,
    guild_scoped: st.SearchStrategy[bool] | None = None,
) -> Rule:
    """Generate a rule using a catalogue selector unless a custom pattern strategy is supplied."""
    raw = draw(pattern if pattern is not None else patterns())
    scoped = draw(guild_scoped if guild_scoped is not None else st.booleans())
    return Rule(
        pattern=Pattern.parse(raw),
        effect=draw(effects(include_forbid=include_forbid)),
        subject_kind=draw(st.sampled_from(list(SubjectKind))),
        origin=draw(st.sampled_from(list(Origin))),
        scope_guild_id=GUILD_ID if scoped else None,
        source=draw(st.sampled_from(("account:1", "role:@trusted", "role:moderator", ""))),
    )


def rule_sets(*, include_forbid: bool = True, max_size: int = 6) -> st.SearchStrategy[list[Rule]]:
    """Generate a bounded list of rules, allowing duplicates."""
    return st.lists(rules(include_forbid=include_forbid), max_size=max_size)
