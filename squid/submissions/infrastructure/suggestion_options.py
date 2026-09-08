"""Submission form options served from the shared suggestion registry.

The form manifest's `option_source` values and the suggestion registry's source ids are one
namespace. Serving both from this catalogue is what enforces that: no source can be completable
in Discord but missing from the web form, and the two cannot disagree about what a name means.
"""

from typing import override

from squid.submissions.application import FormOptionCatalog, FormOptionSet
from squid.submissions.domain import ChoiceOption
from squid.suggestions.application import SuggestionService, UnknownSuggestionSourceError
from squid.suggestions.domain import SourceKind


class SuggestionFormOptionCatalog(FormOptionCatalog):
    """Project registered enumerable sources into choices any client can draw."""

    def __init__(self, suggestions: SuggestionService) -> None:
        self._suggestions = suggestions

    @override
    async def options(
        self,
        source: str,
        category: str,
        *,
        locale: str | None,
    ) -> FormOptionSet:
        """Enumerate one registered source, carrying through its content-addressed revision.

        Raises:
            ValueError: If the source is unregistered, or is queried rather than enumerable. A
                form needs the whole set, and a queried source has no whole set to give.
        """
        try:
            definition = self._suggestions.registry.resolve(source)
        except UnknownSuggestionSourceError as error:
            msg = f"unknown submission option source: {source}"
            raise ValueError(msg) from error
        if definition.kind is not SourceKind.ENUMERABLE:
            msg = f"unknown submission option source: {source}"
            raise ValueError(msg)

        result = await self._suggestions.enumerate(source, context={"category": category}, locale=locale)
        choices = tuple(ChoiceOption(item.value, item.label) for item in result.items)
        # `enumerate` returns the source's own revision, which is content-addressed over the same
        # value/label pairs, so a client's cached revision stays valid across this change.
        return FormOptionSet(source, category, result.revision or 1, choices)
