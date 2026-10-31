"""Conservative, reviewable inference changes to an existing build."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from whenever import Instant

from squid.builds.application.editing import BuildEditPatch
from squid.builds.domain import Build, BuildCategory, BuildDraft, DoorBuild
from squid.core.errors import JSONValue, ValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class RevisionFacts:
    """Retained inferred facts, before a reviewer chooses a target build."""

    category: BuildCategory | None = None
    version_spec: str | None = None
    width: int | None = None
    height: int | None = None
    depth: int | None = None
    door_width: int | None = None
    door_height: int | None = None
    door_depth: int | None = None
    door_orientation: str | None = None
    normal_opening_time: int | None = None
    normal_closing_time: int | None = None
    patterns: tuple[str, ...] = ()
    creators_ign: tuple[str, ...] = ()
    wiring_placement_restrictions: tuple[str, ...] = ()
    animated_restrictions: tuple[str, ...] = ()
    component_restrictions: tuple[str, ...] = ()
    miscellaneous_restrictions: tuple[str, ...] = ()
    description: str | None = None

    @classmethod
    def from_draft(cls, draft: BuildDraft) -> RevisionFacts:
        """Copy inference facts without finalizing or applying category defaults."""
        return cls(
            category=draft.category,
            version_spec=draft.version_spec,
            width=draft.width,
            height=draft.height,
            depth=draft.depth,
            door_width=draft.door_width,
            door_height=draft.door_height,
            door_depth=draft.door_depth,
            door_orientation=draft.door_orientation,
            normal_opening_time=draft.normal_opening_time,
            normal_closing_time=draft.normal_closing_time,
            patterns=tuple(draft.patterns),
            creators_ign=tuple(draft.creators_ign),
            wiring_placement_restrictions=tuple(draft.wiring_placement_restrictions),
            animated_restrictions=tuple(draft.animated_restrictions),
            component_restrictions=tuple(draft.component_restrictions),
            miscellaneous_restrictions=tuple(draft.miscellaneous_restrictions),
            description=draft.description or draft.extra_info.get("user"),
        )

    def patch(self, build: Build) -> BuildEditPatch:
        """Preserve missing facts and reject an inferred category change."""
        if self.category is not None and self.category != build.category:
            message = "The inferred category differs from this build; choose a matching candidate."
            raise ValidationError(message)
        values: dict[str, Any] = {}
        if any(value is not None for value in (self.width, self.height, self.depth)):
            values["dimensions"] = tuple(
                new if new is not None else old
                for new, old in zip(
                    (self.width, self.height, self.depth),
                    build.dimensions,
                    strict=True,
                )
            )
        for name in (
            "version_spec",
            "creators_ign",
            "wiring_placement_restrictions",
            "animated_restrictions",
            "component_restrictions",
            "miscellaneous_restrictions",
        ):
            value = getattr(self, name)
            if value:
                values[name] = list(value) if isinstance(value, tuple) else value
        if self.description is not None:
            values["extra_user_info"] = self.description
        if isinstance(build, DoorBuild):
            if any(value is not None for value in (self.door_width, self.door_height, self.door_depth)):
                values["door_dimensions"] = tuple(
                    new if new is not None else old
                    for new, old in zip(
                        (self.door_width, self.door_height, self.door_depth),
                        build.door_dimensions,
                        strict=True,
                    )
                )
            if self.patterns:
                values["door_type"] = list(self.patterns)
            if self.door_orientation is not None:
                values["door_orientation_type"] = self.door_orientation
            for name in ("normal_opening_time", "normal_closing_time"):
                if (value := getattr(self, name)) is not None:
                    values[name] = value
        return BuildEditPatch(**values)


@dataclass(frozen=True, slots=True)
class RevisionProposal:
    """An inferred candidate and its retained review snapshot, optionally matched to a build."""

    id: UUID
    run_id: UUID
    owner_account_id: int
    requested_by_account_id: int
    source_message_id: int
    facts: RevisionFacts
    build_id: int | None
    expected_revision: int | None
    before: dict[str, JSONValue]
    after: dict[str, JSONValue]
    created_at: Instant
    expires_at: Instant
    approved_by_account_id: int | None = None
    applied_revision: int | None = None


def revision_values(build: Build) -> dict[str, JSONValue]:
    """Select editable facts for a diff, excluding identity and moderation metadata."""
    values: dict[str, JSONValue] = {
        "dimensions": list(build.dimensions),
        "version_spec": build.version_spec,
        "creators": list(build.creators_ign),
        "description": build.description,
        "wiring_placement_restrictions": list(build.wiring_placement_restrictions),
        "animated_restrictions": list(build.animated_restrictions),
        "component_restrictions": list(build.component_restrictions),
        "miscellaneous_restrictions": list(build.miscellaneous_restrictions),
    }
    if isinstance(build, DoorBuild):
        values.update(
            {
                "opening_dimensions": list(build.door_dimensions),
                "orientation": build.orientation,
                "patterns": list(build.patterns),
                "opening_time": build.normal_opening_time,
                "closing_time": build.normal_closing_time,
            }
        )
    return values
