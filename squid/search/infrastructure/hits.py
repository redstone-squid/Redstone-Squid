"""Convert indexed documents to public search hits."""

from typing import Literal, cast

from squid.search.domain import BuildSearchHit, MetadataSearchHit, RecordSearchHit, SearchHit
from squid.search.models import SearchDocument


def public_identity(document: SearchDocument) -> tuple[Literal["build", "record", "metadata"], str]:
    """Return the resource kind and source ID that search results expose for a document.

    Builds and records keep their stored key. Every other stored kind is metadata,
    whose source ID is prefixed with the stored kind to keep it unique.
    """
    if document.resource_kind == "build":
        return "build", document.source_key
    if document.resource_kind == "record":
        return "record", document.source_key
    return "metadata", f"{document.resource_kind}:{document.source_key}"


def document_hit(document: SearchDocument, score: float | None) -> SearchHit:
    data = document.document_data
    tags = tuple(document.tags)
    if document.resource_kind == "record":
        metrics = data.get("metrics", data.get("metric", {}))
        holder_ids = data.get("holder_build_ids", ())
        build_id = _integer(data, "build_id")
        if not build_id and isinstance(holder_ids, list | tuple) and holder_ids:
            first_holder = holder_ids[0]
            if isinstance(first_holder, int) and not isinstance(first_holder, bool):
                build_id = first_holder
        if not isinstance(metrics, dict):
            metrics = {}
        return RecordSearchHit(
            source_id=document.source_key,
            title=document.title,
            subtitle=document.subtitle,
            build_id=build_id,
            build_title=_string(data, "build_title", document.title),
            base_title=_string(data, "base_title", "unknown"),
            version_scope=_string(data, "version_scope", "all-time"),
            score=score,
            tags=tags,
            metrics=cast(dict[str, str | int | float | bool], metrics),
        )
    if document.resource_kind == "build":
        return BuildSearchHit(
            source_id=document.source_key,
            title=document.title,
            status=document.status or "unknown",
            description=document.description,
            score=score,
            tags=tags,
        )
    aliases = data.get("aliases", ())
    if not isinstance(aliases, list | tuple):
        aliases = ()
    typed_aliases = cast(list[object] | tuple[object, ...], aliases)
    return MetadataSearchHit(
        source_id=public_identity(document)[1],
        title=document.title,
        metadata_kind=_string(data, "metadata_kind", document.resource_kind),
        description=document.description,
        score=score,
        aliases=tuple(str(alias) for alias in typed_aliases),
    )


def _string(data: dict[str, object], key: str, default: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else default


def _integer(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
