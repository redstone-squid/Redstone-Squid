from __future__ import annotations

from typing import TypedDict

from Database.schema import RecordCategory, DoorOrientationName


class SubmissionCommandResponseT(TypedDict, total=False):
    """Response from the submit or edit command."""

    submission_id: int | None
    record_category: RecordCategory | None
    door_size: str | None
    door_width: int | None
    door_height: int | None
    pattern: str | None
    door_type: DoorOrientationName | None
    build_width: int | None
    build_height: int | None
    build_depth: int | None
    works_in: str | None
    wiring_placement_restrictions: str | None
    component_restrictions: str | None
    information_about_build: str | None
    normal_closing_time: int | None
    normal_opening_time: int | None
    date_of_creation: str | None
    in_game_name_of_creator: str | None
    locationality: str | None
    directionality: str | None
    link_to_image: str | None
    link_to_youtube_video: str | None
    link_to_world_download: str | None
    server_ip: str | None
    coordinates: str | None
    command_to_get_to_build: str | None
