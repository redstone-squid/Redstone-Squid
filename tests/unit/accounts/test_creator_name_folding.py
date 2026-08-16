"""Golden corpus pinning `fold_creator_name`.

The fold defines creator identity: two names that fold together are one creator, and the
`creator_aliases.normalized_name` unique index is built on the result. Nothing in the database
can check it, because Postgres cannot reproduce `casefold`. So the outputs are pinned here
instead, and a CPython upgrade that changes Unicode casefolding fails this file loudly rather
than silently regrouping existing creators.
"""

# ruff: noqa: RUF001  Confusable and compatibility characters are the subject
# matter here: they are the inputs whose folding this file exists to pin.

import unicodedata

import pytest

from squid.accounts.domain import fold_creator_name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("Bob", "bob", id="ascii-case"),
        pytest.param("  Bob  ", "bob", id="ascii-space"),
        pytest.param("\tBob\t", "bob", id="tab"),
        pytest.param(" Bob ", "bob", id="nbsp-via-nfkc"),
        pytest.param("　Bob", "bob", id="ideographic-space-via-nfkc"),
        # `str.lower()` gives `σς` here: it lowers the trailing capital sigma to the
        # *final* form, so `ΣΣ` and `Σς` would be two creators. casefold does not.
        pytest.param("ΣΣ", "σσ", id="greek-final-sigma"),
        pytest.param("Σς", "σσ", id="greek-final-sigma-written-out"),
        pytest.param("Straße", "strasse", id="eszett"),
        pytest.param("İ", "i̇", id="dotted-capital-i"),
        pytest.param("Ａlice", "alice", id="fullwidth-via-nfkc"),
        pytest.param("ﬁx", "fix", id="ligature-via-nfkc"),
        pytest.param("", "", id="empty"),
        pytest.param("   ", "", id="whitespace-only"),
    ],
)
def test_fold_is_pinned(name: str, expected: str) -> None:
    assert fold_creator_name(name) == expected


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param("ΣΣ", "Σς", id="sigma-forms"),
        pytest.param("Straße", "Strasse", id="eszett-expansion"),
        pytest.param("Ａlice", "alice", id="fullwidth"),
        pytest.param("Bob", " BOB ", id="case-and-space"),
    ],
)
def test_names_that_are_one_creator(left: str, right: str) -> None:
    assert fold_creator_name(left) == fold_creator_name(right)


def test_dotted_capital_i_stays_distinct_from_ascii_i() -> None:
    """Postgres `lower()` collapses these two; casefold keeps them apart.

    Pinned because it is the one case where the retired SQL fold was *stricter*, so a
    regression toward `lower()` semantics would silently merge two creators.
    """
    assert fold_creator_name("İ") != fold_creator_name("I")


def test_fold_is_idempotent() -> None:
    """Re-folding a stored value must not move it, or lookups would drift after a rewrite."""
    for name in ("ΣΣ", "Straße", "İ", "Ａlice", "ﬁx", " Bob "):
        once = fold_creator_name(name)
        assert fold_creator_name(once) == once


def test_fold_satisfies_the_database_check_constraint() -> None:
    """`creator_aliases_normalized_name_folded` asserts these two properties in SQL."""
    for name in ("ΣΣ", "Straße", "İ", "Ａlice", "ﬁx", " Bob ", "MiXeD"):
        folded = fold_creator_name(name)
        assert folded == folded.strip(" "), "check constraint requires btrim-stable output"
        assert not any("A" <= character <= "Z" for character in folded), "check constraint forbids ASCII uppercase"


def test_fold_agrees_with_the_unicode_standard_algorithm() -> None:
    """Guard the pipeline's shape, not a Unicode version.

    `requires-python` spans 3.12 and 3.13, which ship different Unicode versions, so pinning
    `unicodedata.unidata_version` would fail on a supported interpreter. The corpus above is
    the real tripwire for a casefold change; this pins that the fold stays composed of the
    standard operations rather than drifting back toward `str.lower()`.
    """
    for name in ("ΣΣ", "Straße", "İ", "Ａlice", "ﬁx", " Bob ", "MiXeD"):
        assert fold_creator_name(name) == unicodedata.normalize("NFKC", name).strip().casefold()
