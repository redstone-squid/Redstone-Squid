"""Match families to existing concrete categories without enumerating their domains."""

from dataclasses import replace
from functools import lru_cache

from pydantic import ValidationError

from squid.builds.domain.expansion import DoorExpansion
from squid.builds.domain.expansion_evaluation import (
    NormalizedExpansion,
    PointStatus,
    evaluate_expansion,
    normalize_expansion,
)
from squid.records.application.live import DoorRecordLookup
from squid.records.application.models import CandidateBuild, CategoryIdentity
from squid.records.domain import BaseTitle, BuildKind


def door_category_lookup(
    identity: CategoryIdentity, *, base_title: BaseTitle = BaseTitle.SMALLEST, version_id: int | None = None
) -> DoorRecordLookup | None:
    if identity.kind is not BuildKind.DOOR:
        return None
    try:
        kind, size, types, orientation = identity.base_key.split("|")
        if kind != "door" or not types.startswith("t[") or not types.endswith("]"):
            return None
        axes = tuple(int(axis) for axis in size.split("x"))
        if len(axes) == 2:
            axes = (*axes, 1)
        return DoorRecordLookup.model_validate(
            {
                "opening": axes,
                "orientation": orientation,
                "type_ids": tuple(int(value) for value in types[2:-1].split(",") if value),
                "restriction_ids": identity.restriction_ids,
                "restriction_values": identity.restriction_values,
                "base_title": base_title,
                "version_id": version_id,
            }
        )
    except ValueError, ValidationError:
        return None


# A rebuild tests each family against every observed opening size.
@lru_cache(maxsize=1024)
def _normalized(expansion: DoorExpansion) -> NormalizedExpansion:
    return normalize_expansion(expansion)


def family_at_category(
    source: CandidateBuild, identity: CategoryIdentity, *, competing_only: bool = False
) -> CandidateBuild | None:
    """Give a family the category's size if its orientation and pattern set match.

    Sample measurements never become competition facts. With competing_only, a
    family is also left out unless some branch is supported or unresolved at the
    category's opening. Published categories use this so that a family proven
    absent from a size neither creates that category nor changes how it is
    computed. Live lookups keep such families to show why they do not compete.
    """
    query = door_category_lookup(identity)
    if (
        query is None
        or source.door is None
        or source.door.orientation != query.orientation
        or frozenset(f.id for f in source.types) != query.type_ids
    ):
        return None
    if competing_only and source.expansion is not None:
        point = evaluate_expansion(_normalized(source.expansion), query.opening)
        if not any(c.status in (PointStatus.SUPPORTED, PointStatus.UNRESOLVED) for c in point.configurations):
            return None
    size = identity.base_key.split("|")[1]
    return replace(source, door=replace(source.door, size=size))
