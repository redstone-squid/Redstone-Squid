"""Submission form options served from the shared suggestion registry.

Form option_source values name suggestion sources. Enumerable sources supply
both form choices and autocomplete results from the same catalog. Queried sources
cannot supply a complete choice list and are rejected here.
"""

from typing import override

from squid.core.actors import ActingContext
from squid.core.submissions.application import FormOptionCatalog, FormOptionSet
from squid.core.submissions.domain import ChoiceOption
from squid.core.suggestions.application import SuggestionService, UnknownSuggestionSourceError
from squid.core.suggestions.domain import SourceKind


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
        context: ActingContext,
    ) -> FormOptionSet:
        """Return a deterministic content revision for one supported option source.

        Raise ValueError for unknown or non-enumerable sources. Forms need a full
        choice list, which a source that only supports queries cannot provide.
        """
        try:
            definition = self._suggestions.registry.resolve(source)
        except UnknownSuggestionSourceError as error:
            msg = f"unknown submission option source: {source}"
            raise ValueError(msg) from error
        if definition.kind is not SourceKind.ENUMERABLE:
            msg = f"unknown submission option source: {source}"
            raise ValueError(msg)

        result = await self._suggestions.enumerate(
            source, source_context={"category": category}, locale=locale, context=context
        )
        choices = tuple(ChoiceOption(item.value, item.label, item.value_type) for item in result.items)
        # Reuse the suggestion source's revision so both clients can cache the same choices.
        return FormOptionSet(source, category, result.revision or 1, choices)
