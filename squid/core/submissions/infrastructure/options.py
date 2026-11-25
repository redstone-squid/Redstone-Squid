"""Approved, authoritative submission form options."""

import hashlib
import json
from collections.abc import Sequence
from typing import Protocol, override

from squid.core.actors import ActingContext
from squid.core.submissions.application import FormOptionCatalog, FormOptionSet
from squid.core.submissions.domain import ChoiceOption
from squid.core.tags.domain import TagSemanticKind, TagValueType
from squid.core.tags.models import TagDefinition
from squid.core.versions.domain import MinecraftVersion

_SOURCES = {
    "approved_restrictions": TagSemanticKind.RESTRICTION,
    "approved_patterns": TagSemanticKind.PATTERN,
    "approved_showcase_tags": TagSemanticKind.SHOWCASE,
}


class ApprovedTagDefinitions(Protocol):
    """Read the tag definitions approved for public clients."""

    async def public_definitions(self, *, context: ActingContext) -> Sequence[TagDefinition]: ...


class CanonicalMinecraftVersions(Protocol):
    """Read canonical versions recognized by build persistence."""

    async def list_all(self, *, context: ActingContext) -> Sequence[MinecraftVersion]: ...


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
        """Return approved options and a revision derived from their values and labels.

        This adapter does not filter by category or localize labels. Versions are
        newest first within each edition, with Java before Bedrock; tags use label
        order. Unknown sources raise ValueError.
        """
        del locale
        if source == "approved_source_versions":
            choices = tuple(
                ChoiceOption(value, value)
                for value in sorted(
                    {str(version) for version in await self._versions.list_all(context=ActingContext.current())},
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
            for definition in await self._tags.public_definitions(context=ActingContext.current())
            if definition.semantic_kind is semantic_kind
        )
        choices = tuple(
            ChoiceOption(
                definition.stable_key,
                definition.display_name,
                None if definition.value_type is TagValueType.NONE else definition.value_type.value,
            )
            for definition in sorted(definitions, key=lambda item: (item.display_name.casefold(), item.stable_key))
        )
        return FormOptionSet(source, category, _content_revision(choices), choices)


def _content_revision(options: tuple[ChoiceOption, ...]) -> int:
    # Matches `content_revision` for suggestions, so both catalogues publish the same revision.
    payload = json.dumps(
        [
            (option.value, option.label)
            if option.value_type is None
            else (option.value, option.label, option.value_type)
            for option in options
        ],
        separators=(",", ":"),
    ).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") or 1


def _version_sort_key(value: str) -> tuple[bool, tuple[int, ...]]:
    edition, version = value.split(" ", maxsplit=1)
    return edition != "Java", tuple(-int(part) for part in version.split("."))
