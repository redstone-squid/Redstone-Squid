"""Build submission command values."""

from dataclasses import dataclass

from squid.builds.domain import DoorOrientationLiteral

type Dimensions = tuple[int | None, int | None, int | None]


@dataclass(slots=True, frozen=True)
class DoorSubmissionInput:
    """Input for legacy Discord door submission entry points.

    Provider-neutral synchronized submissions use ``BuildService.submit_for_account``.
    """

    # Discord snowflake retained at this transport boundary for the existing bot/API flow.
    submitter_id: int
    door_size: Dimensions
    pattern: tuple[str, ...] = ("Regular",)
    door_type: DoorOrientationLiteral = "Door"
    build_size: Dimensions = (None, None, None)
    works_in: str | None = None
    restrictions: tuple[str, ...] = ()
    information_about_build: str | None = None
    normal_closing_time: int | None = None
    normal_opening_time: int | None = None
    date_of_creation: str | None = None
    creators: tuple[str, ...] = ()
    locationality: str | None = None
    directionality: str | None = None
    image_urls: tuple[str, ...] = ()
    video_urls: tuple[str, ...] = ()
    world_download_urls: tuple[str, ...] = ()
    schematic_urls: tuple[str, ...] = ()
    ai_generated: bool = False
