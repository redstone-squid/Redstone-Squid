"""Resource scopes supported by search."""

from enum import StrEnum


class SearchScope(StrEnum):
    """Resources included in a search."""

    RECORDS = "records"
    BUILDS = "builds"
    METADATA = "metadata"
    ALL = "all"


CATALOGUE_SCOPES = frozenset({SearchScope.BUILDS, SearchScope.RECORDS})
