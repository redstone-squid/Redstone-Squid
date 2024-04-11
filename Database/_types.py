from enum import StrEnum
from typing import TypedDict
from Database.enums import Status

class Category(StrEnum):
    """The categories of the builds."""
    DOOR = 'Door'
    EXTENDER = 'Extender'
    UTILITY = 'Utility'
    ENTRANCE = 'Entrance'

class BuildRecord(TypedDict, total=False):
    """A record of a build in the database."""
    id: int
    submission_status: Status
    record_category: str
    information: dict  # JSON
    submission_time: str
    edited_time: str
    width: int
    height: int
    depth: int
    completion_time: str
    category: Category
    server_info: dict  # JSON
    submitter_id: int


class DoorRecord(TypedDict, total=False):
    """A record of a door in the database."""
    build_id: int
    orientation: str
    door_width: int
    door_height: int
    door_width: int
    normal_opening_time: int
    normal_closing_time: int
    visible_opening_time: int
    visible_closing_time: int

class ExtenderRecord(TypedDict, total=False):
    """A record of an extender in the database."""
    build_id: int

class UtilityRecord(TypedDict, total=False):
    """A record of a utility in the database."""
    build_id: int

class EntranceRecord(TypedDict, total=False):
    """A record of an entrance in the database."""
    build_id: int
