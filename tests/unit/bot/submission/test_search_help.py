"""The runnable reference examples must be accepted by the real query parser."""

import pytest

from squid.bot.submission.search_help import BUILD_EXAMPLES, RECORD_EXAMPLES
from squid.search.application import SearchQueryParser


@pytest.mark.parametrize("query", [query for _label, query in (*BUILD_EXAMPLES, *RECORD_EXAMPLES)])
def test_help_examples_are_valid_queries(query: str) -> None:
    assert SearchQueryParser().parse(query).expression is not None
