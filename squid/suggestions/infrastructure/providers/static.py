"""Share fixed candidate lists and synchronous candidate factories across transports."""

from collections.abc import Callable, Iterable, Sequence

from squid.core.i18n import SUPPORTED_LOCALES
from squid.records.domain import BaseTitle, BuildKind, VersionScope
from squid.settings.domain.runtime import RuntimeSettings
from squid.suggestions.application import Candidate
from squid.suggestions.application.registry import SuggestionSource
from squid.suggestions.domain import SourceKind, SuggestionRequest


class StaticProvider:
    """Suggest from a fixed candidate list."""

    def __init__(self, values: Iterable[Candidate]) -> None:
        self._candidates = tuple(values)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return self._candidates

    @classmethod
    def of(cls, values: Sequence[str], *, kind: str = "") -> StaticProvider:
        """Build a provider whose value and label are the same string."""
        return cls(Candidate.create(value, kind=kind) for value in values)

    @classmethod
    def labelled(cls, values: Sequence[tuple[str, str]], *, kind: str = "") -> StaticProvider:
        """Build a provider from `(value, label)` pairs."""
        return cls(Candidate.create(value, label, kind=kind) for value, label in values)


class CallableProvider:
    """Call a synchronous candidate factory for each request.

    The factory runs on the event loop and must not perform blocking work.
    """

    def __init__(self, produce: Callable[[SuggestionRequest], Iterable[Candidate]]) -> None:
        self._produce = produce

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        return tuple(self._produce(request))


def static_sources() -> tuple[SuggestionSource, ...]:
    return (
        SuggestionSource(
            id="runtime_setting_names",
            kind=SourceKind.ENUMERABLE,
            kind_label="setting",
            provider=StaticProvider(
                Candidate.create(
                    name,
                    name.replace("_", " ").title(),
                    description=(
                        "Boolean: true or false"
                        if field.annotation is bool
                        else "Whole number"
                        if field.annotation is int
                        else "Decimal number"
                        if field.annotation is float
                        else "JSON array of channel IDs"
                    ),
                )
                for name, field in RuntimeSettings.model_fields.items()
            ),
        ),
        SuggestionSource(
            id="build_kinds",
            provider=StaticProvider.of([kind.value for kind in BuildKind], kind="build_kind"),
            kind=SourceKind.ENUMERABLE,
            kind_label="build_kind",
        ),
        SuggestionSource(
            id="base_titles",
            provider=StaticProvider.of([item.value for item in BaseTitle], kind="base_title"),
            kind=SourceKind.ENUMERABLE,
            kind_label="base_title",
        ),
        SuggestionSource(
            id="version_scopes",
            provider=StaticProvider.of([item.value for item in VersionScope], kind="version_scope"),
            kind=SourceKind.ENUMERABLE,
            kind_label="version_scope",
        ),
        SuggestionSource(
            id="locales",
            provider=StaticProvider.of(sorted(SUPPORTED_LOCALES), kind="locale"),
            kind=SourceKind.ENUMERABLE,
            kind_label="locale",
        ),
    )
