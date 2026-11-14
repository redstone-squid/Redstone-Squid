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
        # `metric` and `build_title` describe the first ranked holder. A record without a holder
        # has no build to open, which 0 represents.
        metrics = data.get("metric", {})
        if not isinstance(metrics, dict):
            metrics = {}
        holder_ids = data.get("holder_build_ids", ())
        build_id = 0
        if isinstance(holder_ids, list | tuple) and holder_ids and type(holder_ids[0]) is int:
            build_id = holder_ids[0]
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
