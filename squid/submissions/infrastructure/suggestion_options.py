"""Submission form options served from the shared suggestion registry.

The form manifest's `option_source` values and the suggestion registry's source ids are the same
namespace by design. Serving both from one catalogue is what makes that true rather than merely
intended: a source cannot be completable in Discord but missing from the web form, and the two
cannot disagree about what a name means.
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
        """Return a deterministic content revision for one supported option source.

        Raises `ValueError` for anything a form may not read, preserving the contract the route
        already relies on. A queried source is refused as firmly as an unregistered one: a form
        needs the whole set, and a source that cannot be enumerated has no whole set to give.
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
