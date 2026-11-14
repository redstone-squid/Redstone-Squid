"""Match families to existing concrete categories without enumerating their domains."""

from dataclasses import replace

from pydantic import ValidationError

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


def family_at_category(source: CandidateBuild, identity: CategoryIdentity) -> CandidateBuild | None:
    """Project category identity only; sample measurements never become competition facts."""
    query = door_category_lookup(identity)
    if (
        query is None
        or source.door is None
        or source.door.orientation != query.orientation
        or frozenset(f.id for f in source.types) != query.type_ids
    ):
        return None
    size = identity.base_key.split("|")[1]
    return replace(source, door=replace(source.door, size=size))
