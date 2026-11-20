"""Properties of the search and version strings accepted by Squid."""

from hypothesis import given
from hypothesis import strategies as st

from squid.core.search.application import QuerySyntaxError, SearchQueryParser
from squid.core.versions.domain import MinecraftVersion, parse_version_string
from squid.core.versions.errors import InvalidVersionError

clauses = st.one_of(
    st.sampled_from(["door", '"piston extender"', "ドア", "width:[1 TO 5]"]),
    st.integers(0, 1024).map("width>={}".format),
)
queries = st.recursive(
    clauses,
    lambda child: st.tuples(child, st.sampled_from([" AND ", " OR ", " "]), child).map(
        lambda parts: "(" + "".join(parts) + ")"
    ),
    max_leaves=8,
)


@given(queries)
def test_search_normalization_preserves_expression(source: str) -> None:
    parser = SearchQueryParser()
    query = parser.parse(source)
    assert parser.parse(query.normalized).expression == query.expression


@given(st.text())
def test_freeform_search(source: str) -> None:
    parser = SearchQueryParser()
    try:
        query = parser.parse(source)
    except QuerySyntaxError:
        return
    else:
        assert parser.parse(query.normalized).expression == query.expression


@given(
    edition=st.sampled_from(["", "Java", "JAVA", "Bedrock", "bedrock"]),
    major=st.integers(0, 999),
    minor=st.integers(0, 999),
    patch=st.one_of(st.none(), st.integers(0, 999)),
    padding=st.sampled_from(["", " ", "!"]),
)
def test_version_defaults_and_spelling(edition: str, major: int, minor: int, patch: int | None, padding: str) -> None:
    release = f"{major}.{minor}" if patch is None else f"{major}.{minor}.{patch}"
    expected = "Bedrock" if edition.lower() == "bedrock" else "Java"
    assert parse_version_string(f"{padding}{edition} {release}{padding}") == (expected, major, minor, patch or 0)


@given(st.text())
def test_freeform_version(source: str) -> None:
    try:
        version = parse_version_string(source)
    except InvalidVersionError:
        return
    assert parse_version_string(str(MinecraftVersion(*version))) == version
