from typing import TypedDict, Optional

import discord
from discord.ext.commands import Bot


class SubmissionCommandResponseT(TypedDict, total=False):
    """Response from the submit or edit command."""
    self: Bot
    interaction: discord.Interaction
    submission_id: Optional[int]
    record_category: Optional[str]
    door_width: Optional[int]
    door_height: Optional[int]
    pattern: Optional[str]
    door_type: Optional[str]
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
