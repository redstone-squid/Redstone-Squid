"""Approved, authoritative submission form options."""

import hashlib
from collections.abc import Sequence
from typing import Protocol, override

from squid.submissions.application import FormOptionCatalog, FormOptionSet
from squid.submissions.domain import ChoiceOption
from squid.tags.domain import TagDefinition, TagSemanticKind
from squid.versions.domain import MinecraftVersion

_SOURCES = {
    "approved_restrictions": TagSemanticKind.RESTRICTION,
    "approved_patterns": TagSemanticKind.PATTERN,
    "approved_showcase_tags": TagSemanticKind.SHOWCASE,
}


class ApprovedTagDefinitions(Protocol):
    """Read the tag definitions approved for public clients."""

    async def public_definitions(self) -> Sequence[TagDefinition]: ...


class CanonicalMinecraftVersions(Protocol):
    """Read canonical versions recognized by build persistence."""

    async def list_all(self) -> Sequence[MinecraftVersion]: ...


class ApprovedSubmissionOptionCatalog(FormOptionCatalog):
    """Project approved tags and canonical versions into choices any client can draw."""

    def __init__(self, tags: ApprovedTagDefinitions, versions: CanonicalMinecraftVersions) -> None:
        self._tags = tags
        self._versions = versions

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
        if source == "approved_source_versions":
            choices = tuple(
                ChoiceOption(value, value)
                for value in sorted(
                    {str(version) for version in await self._versions.list_all()},
                    key=_version_sort_key,
                )
            )
            return FormOptionSet(source, category, _content_revision(choices), choices)
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


def _version_sort_key(value: str) -> tuple[bool, tuple[int, ...]]:
    edition, version = value.split(" ", maxsplit=1)
    return edition != "Java", tuple(-int(part) for part in version.split("."))
