"""Pattern grammar, matching and specificity."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid.permissions.domain import CATALOGUE, InvalidPatternError, Pattern

from .strategies import node_names, patterns, selectors_for


@pytest.mark.parametrize(
    ("pattern", "node", "expected"),
    [
        ("build.submission.approve", "build.submission.approve", True),
        ("build.submission.approve", "build.submission.reject", False),
        # `*` consumes exactly one segment, so it does not reach into a subtree.
        ("build.*", "build.submission.approve", False),
        ("build.*.approve", "build.submission.approve", True),
        ("build.*.approve", "build.submission.reject", False),
        # `**` reaches any depth, but needs at least one segment to consume.
        ("build.**", "build.submission.approve", True),
        ("build.**", "record.entry.inspect", False),
        ("**", "build.submission.approve", True),
        # Cross-cutting selection: one verb across every resource in a namespace.
        ("starboard.*.edit", "starboard.emoji.edit", True),
        ("starboard.*.edit", "starboard.board.delete", False),
        # Tag selectors ignore tree position entirely.
        ("@destructive", "record.entry.rebuild", True),
        ("@destructive", "build.submission.approve", False),
        ("@moderation", "build.submission.approve", True),
    ],
)
def test_matching_examples(pattern: str, node: str, expected: bool) -> None:
    assert Pattern.parse(pattern).matches(CATALOGUE[node]) is expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "Build.Submission",  # uppercase is rejected so patterns are unambiguous to type
        "build..submission",
        "build.**.approve",  # `**` is trailing-only
        "build.-bad",
        "@nonsense",
        "a.b.c.d.e.f",  # deeper than MAX_SEGMENTS
    ],
)
def test_malformed_patterns_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidPatternError):
        Pattern.parse(raw)


def test_specificity_orders_the_documented_example() -> None:
    ordered = [
        "build.submission.approve",
        "build.*.approve",
        "build.*",
        "build.**",
        "@moderation",
        "**",
    ]
    specificities = [Pattern.parse(raw).specificity for raw in ordered]

    assert specificities == sorted(specificities, reverse=True)
    assert len(set(specificities)) == len(specificities)


@given(name=node_names())
def test_every_selector_actually_selects_its_node(name: str) -> None:
    """P2: the selector enumeration used by the other properties is honest."""
    node = CATALOGUE[name]

    assert all(Pattern.parse(raw).matches(node) for raw in selectors_for(name))


@given(name=node_names())
def test_single_star_is_depth_exact_and_implied_by_double_star(name: str) -> None:
    """P3: `a.*` requires exactly one further segment; `a.**` accepts one or more."""
    node = CATALOGUE[name]
    segments = name.split(".")

    for depth in range(1, len(segments)):
        prefix = ".".join(segments[:depth])
        single = Pattern.parse(f"{prefix}.{'*'}")
        subtree = Pattern.parse(f"{prefix}.**")

        assert single.matches(node) is (len(segments) == depth + 1)
        assert subtree.matches(node) is True
        # Nothing is reachable by `*` but not by `**` at the same prefix.
        assert not (single.matches(node) and not subtree.matches(node))


@given(raw=patterns())
def test_parsing_round_trips(raw: str) -> None:
    parsed = Pattern.parse(raw)

    assert str(parsed) == raw
    assert Pattern.parse(str(parsed)) == parsed


@given(raw=patterns())
def test_expansion_agrees_with_matching(raw: str) -> None:
    parsed = Pattern.parse(raw)
    expanded = CATALOGUE.expand(raw)

    assert expanded == {node.name for node in CATALOGUE if parsed.matches(node)}


@given(raw=st.sampled_from(("build.**", "**", "@destructive", "settings.server.edit")))
def test_scopes_reached_matches_expansion(raw: str) -> None:
    assert CATALOGUE.scopes_reached(raw) == {CATALOGUE[name].scope for name in CATALOGUE.expand(raw)}
