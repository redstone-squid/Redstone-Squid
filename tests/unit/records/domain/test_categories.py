from itertools import islice

import pytest

from squid.records.domain.categories import CategorySemantics, generate_category_subsets


def test_generate_category_subsets_streams_all_subsets_up_to_limit() -> None:
    semantics = CategorySemantics(implications={}, incompatibilities={})

    subsets = list(generate_category_subsets(("a", "b", "c"), semantics, max_size=2))

    assert subsets == [
        frozenset(),
        frozenset({"a"}),
        frozenset({"b"}),
        frozenset({"c"}),
        frozenset({"a", "b"}),
        frozenset({"a", "c"}),
        frozenset({"b", "c"}),
    ]


def test_generate_category_subsets_excludes_incompatible_combinations() -> None:
    semantics = CategorySemantics(
        implications={},
        incompatibilities={"flush": frozenset({"uncontained"})},
    )

    subsets = set(generate_category_subsets(("flush", "uncontained", "observerless"), semantics))

    assert frozenset({"flush", "uncontained"}) not in subsets
    assert frozenset({"flush", "uncontained", "observerless"}) not in subsets
    assert frozenset({"flush", "observerless"}) in subsets


def test_generate_category_subsets_includes_implied_facets() -> None:
    semantics = CategorySemantics(
        implications={
            "super_seamless": frozenset({"full_seamless"}),
            "full_seamless": frozenset({"seamless"}),
        },
        incompatibilities={},
    )

    subsets = set(generate_category_subsets(("super_seamless",), semantics))

    assert frozenset({"seamless"}) in subsets
    assert frozenset({"full_seamless"}) in subsets
    assert frozenset({"super_seamless"}) in subsets


def test_generate_category_subsets_is_lazy_for_large_facet_sets() -> None:
    semantics = CategorySemantics(implications={}, incompatibilities={})
    generator = generate_category_subsets((f"facet-{index}" for index in range(100)), semantics)

    assert list(islice(generator, 2)) == [frozenset(), frozenset({"facet-0"})]


def test_generate_category_subsets_rejects_negative_limit() -> None:
    semantics = CategorySemantics(implications={}, incompatibilities={})

    with pytest.raises(ValueError, match="cannot be negative"):
        next(generate_category_subsets((), semantics, max_size=-1))
