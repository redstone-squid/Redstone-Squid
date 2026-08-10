"""Approved taxonomy-backed submission form options."""

import hashlib
from collections.abc import Sequence
from typing import Protocol, override

from squid.submissions.application import FormOptionCatalog, FormOptionSet
from squid.submissions.domain import ChoiceOption
from squid.tags.domain import TagDefinition, TagSemanticKind

_SOURCES = {
    "approved_restrictions": TagSemanticKind.RESTRICTION,
    "approved_patterns": TagSemanticKind.PATTERN,
    "approved_showcase_tags": TagSemanticKind.SHOWCASE,
}


class ApprovedTagDefinitions(Protocol):
    """Read the tag definitions approved for public clients."""

    async def public_definitions(self) -> Sequence[TagDefinition]: ...


class ApprovedTagOptionCatalog(FormOptionCatalog):
    """Project approved tag definitions into stable renderer-neutral choices."""

    def __init__(self, tags: ApprovedTagDefinitions) -> None:
        self._tags = tags

    @override
    async def options(
        self,
        source: str,
        category: str,
        *,
        locale: str | None,
    ) -> FormOptionSet:
        """Return a deterministic content revision for one supported option source."""
        del locale
        try:
            semantic_kind = _SOURCES[source]
        except KeyError as error:
            msg = f"unknown submission option source: {source}"
            raise ValueError(msg) from error
        definitions = tuple(
            definition
            for definition in await self._tags.public_definitions()
            if definition.semantic_kind is semantic_kind
        )
        choices = tuple(
            ChoiceOption(definition.stable_key, definition.display_name)
            for definition in sorted(definitions, key=lambda item: (item.display_name.casefold(), item.stable_key))
        )
        return FormOptionSet(source, category, _content_revision(choices), choices)


def _content_revision(options: tuple[ChoiceOption, ...]) -> int:
    payload = "\n".join(f"{option.value}\0{option.label}" for option in options).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") or 1
