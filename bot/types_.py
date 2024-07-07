from __future__ import annotations

from typing import TypedDict, Optional, Literal, TypeAlias, get_args, Tuple


class SubmissionCommandResponseT(TypedDict, total=False):
    """Response from the submit or edit command."""

    submission_id: Optional[int]
    record_category: Optional[RecordCategory]
    door_size: Optional[str]
    door_width: Optional[int]
    door_height: Optional[int]
    pattern: Optional[str]
    door_type: Optional[DoorType]
    build_width: Optional[int]
    build_height: Optional[int]
    build_depth: Optional[int]
    works_in: Optional[str]
    wiring_placement_restrictions: Optional[str]
    component_restrictions: Optional[str]
    information_about_build: Optional[str]
    normal_closing_time: Optional[int]
    normal_opening_time: Optional[int]
    date_of_creation: Optional[str]
    in_game_name_of_creator: Optional[str]
    locationality: Optional[str]
    directionality: Optional[str]
    link_to_image: Optional[str]
    link_to_youtube_video: Optional[str]
    link_to_world_download: Optional[str]
    server_ip: Optional[str]
    coordinates: Optional[str]
    command_to_get_to_build: Optional[str]


RecordCategory: TypeAlias = Literal["Smallest", "Fastest", "First"]
RECORD_CATEGORIES: Tuple[RecordCategory, ...] = get_args(RecordCategory)

BuildType: TypeAlias = Literal["Door", "Extender", "Utility", "Entrance"]
BUILD_TYPES: Tuple[BuildType, ...] = get_args(BuildType)

DoorType: TypeAlias = Literal["Door", "Skydoor", "Trapdoor"]
DOOR_TYPES: Tuple[DoorType, ...] = get_args(DoorType)


class Restriction(TypedDict):
    """A restriction on a build."""

    id: int
    build_category: BuildType
    name: str
    type: Literal["wiring-placement", "component", "miscellaneous"]
