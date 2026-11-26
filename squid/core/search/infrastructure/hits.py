"""Convert indexed documents to public search hits."""

from typing import Literal, cast

from pydantic import TypeAdapter

from squid.core.search.domain import BuildSearchHit, MetadataSearchHit, RecordSearchHit, SearchHit
from squid.core.search.domain.models import RecordSearchHolder
from squid.core.search.infrastructure.holder_metrics import timing_values
from squid.core.search.models import SearchDocument

_HOLDERS = TypeAdapter(tuple[RecordSearchHolder, ...])


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


def document_hit(document: SearchDocument, score: float | None, *, holder_id: int | None = None) -> SearchHit:
    data = document.document_data
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
        holders = _HOLDERS.validate_python(data.get("holders", ()))
        build_title = _string(data, "build_title", document.title)
        if holder_id is not None:
            build_id = holder_id
            if (holder := next((h for h in holders if h.build_id == holder_id), None)) is not None:
                build_title = holder.title
            holder_metrics = data.get("holder_metrics")
            if isinstance(holder_metrics, dict) and isinstance(selected := holder_metrics.get(str(build_id)), dict):
                metrics = selected
        public_metrics = {name: value for name, value in metrics.items() if isinstance(value, str | int | float | bool)}
        public_metrics.update(timing_values(_string(data, "build_kind", ""), metrics.get("timing")))
        return RecordSearchHit(
            source_id=document.source_key,
            title=document.title,
            subtitle=document.subtitle,
            build_id=build_id,
            build_title=build_title,
            criterion=_string(data, "base_title", "unknown"),
            version_scope=_string(data, "version_scope", "all-time"),
            holder_build_ids=tuple(holder_ids) if isinstance(holder_ids, list | tuple) else (),
            holders=holders,
            score=score,
            metrics=public_metrics,
            matched_holder_id=holder_id,
            retired=document.status == "retired",
        )
    if document.resource_kind == "build":
        return BuildSearchHit(
            source_id=document.source_key,
            title=document.title,
            status=document.status or "unknown",
            description=document.description,
            score=score,
            tags=_strings(data.get("tags")),
            dimensions=_dimensions(data.get("dimensions")),
        )
    return MetadataSearchHit(
        source_id=public_identity(document)[1],
        title=document.title,
        metadata_kind=_string(data, "metadata_kind", document.resource_kind),
        description=document.description,
        score=score,
        aliases=_strings(data.get("aliases")),
    )


def _strings(value: object) -> tuple[str, ...]:
    """Read a projected list of names; anything else reads as none."""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in cast(list[object] | tuple[object, ...], value))


def _dimensions(value: object) -> tuple[int | None, int | None, int | None]:
    """Read the projected `{"width": 3, ...}` mapping, which omits unknown dimensions."""
    if not isinstance(value, dict):
        return (None, None, None)
    mapping = cast(dict[str, object], value)

    def read(name: str) -> int | None:
        item = mapping.get(name)
        return item if type(item) is int else None

    return (read("width"), read("height"), read("depth"))


def _string(data: dict[str, object], key: str, default: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else default
