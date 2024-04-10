"""Submitting and retrieving submissions to/from the database"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional, Literal

import discord

import Discord.config
from Database.database import DatabaseManager, all_build_columns
from Discord import utils

class Build:
    """A class representing a submission to the database. This class is used to store and manipulate submissions."""
    PENDING = 0
    CONFIRMED = 1
    DENIED = 2

    def __init__(self):
        """Initializes an empty build.

         This should not be used externally. Use `from_dict()` or `from_id()` instead."""
        # type | None indicates that the value is expected to be filled in.
        # Optional[type] is used to indicate that the value is actually optional.
        # If you do not fill in parameters that are typed "type | None", errors will occur from all parts of the code.
        self.id: int | None = None
        self.submission_status: int | None = None
        self.record_category: Optional[Literal["Smallest", "Fastest", "First"]]= None
        self.versions: Optional[list[str]] = None

        self.width: int | None = None
        self.height: int | None = None
        self.depth: int | None = None

        self.door_width: int | None = None
        self.door_height: int | None = None

        self.door_type: Optional[list[str]] = None
        self.door_orientation_type: Optional[Literal["Door", "Trapdoor", "Skydoor"]] = None

        self.wp_restrictions: Optional[list[str]] = None
        self.comp_restrictions: Optional[list[str]] = None
        self.misc_restrictions: Optional[list[str]] = None

        self.normal_closing_time: int | None = None
        self.normal_opening_time: int | None = None
        self.visible_closing_time: Optional[int] = None
        self.visible_opening_time: Optional[int] = None

        self.information: Optional[dict] = None
        self.creators: Optional[str] = None

        self.image_url: Optional[str] = None
        self.video_url: Optional[str] = None
        self.world_download_url: Optional[str] = None

        self.server_ip: Optional[str] = None
        self.coordinates: Optional[str] = None
        self.command: Optional[str] = None

        self.submitter_id: int | None = None
        self.completion_time: Optional[str] = None
        self.edited_time: datetime | None = None

    async def confirm(self) -> None:
        """Marks the build as confirmed.

        Raises:
            ValueError: If the build could not be confirmed.
        """
        self.submission_status = Build.CONFIRMED
        db = await DatabaseManager()
        response = await db.table('builds').update({'submission_status': Build.CONFIRMED}, count='exact').eq('id', self.id).execute()
        if response.count != 1:
            raise ValueError("Failed to confirm submission in the database.")

    async def deny(self) -> None:
        """Marks the build as denied.

        Raises:
            ValueError: If the build could not be denied.
        """
        self.submission_status = Build.DENIED
        db = await DatabaseManager()
        response = await db.table('builds').update({'submission_status': Build.DENIED}, count='exact').eq('id', self.id).execute()
        if response.count != 1:
            raise ValueError("Failed to deny submission in the database.")

    def generate_embed(self) -> discord.Embed:
        title = self.get_title()
        description = self.get_description()

        em = utils.info_embed(title=title, description=description)

        fields = self.get_meta_fields()
        for key, val in fields.items():
            em.add_field(name=key, value=val, inline=True)

        if self.image_url:
            em.set_image(url=self.image_url)

        em.set_footer(text=f'Submission ID: {self.id}.')

        return em

    def get_title(self) -> str:
        title = "Pending: " if self.submission_status == Build.PENDING else ""

        # Category
        title += f"{self.record_category or ''} "

        # Door dimensions
        if self.door_width and self.door_height:
            title += f"{self.door_width}x{self.door_height} "
        elif self.door_width:
            title += f"{self.door_width} Wide "
        elif self.door_height:
            title += f"{self.door_height} High "

        # Wiring Placement Restrictions
        if self.wp_restrictions is not None:
            for restriction in self.wp_restrictions:
                if restriction != "None":
                    title += f"{restriction} "

        # Pattern
        if self.door_type[0] != "Regular":
            for pattern in self.door_type:
                title += f"{pattern} "

        # Door type
        title += self.door_orientation_type

        return title

    def get_description(self) -> str | None:
        description = []

        # Component Restrictions
        if self.comp_restrictions and self.comp_restrictions[0] != "None":
            description.append(", ".join(self.comp_restrictions))

        if not Discord.config.VERSIONS_LIST[-1] in self.versions:
            description.append("**Broken** in current version.")

        if "Locational" in self.misc_restrictions:
            description.append("**Locational**.")
        elif "Locational with fixes" in self.misc_restrictions:
            description.append("**Locational** with known fixes for each location.")

        if "Directional" in self.misc_restrictions:
            description.append("**Directional**.")
        elif "Directional with fixes" in self.misc_restrictions:
            description.append("**Directional** with known fixes for each direction.")

        if self.information:
            description.append("\n" + str(self.information))

        if len(description) > 0:
            return "\n".join(description)
        else:
            return None

    def get_versions_string(self) -> str | None:
        versions = []

        linking = False
        first_version = None
        last_version = None

        for index, version in enumerate(Discord.config.VERSIONS_LIST):
            if version in self.versions:

                if not linking:
                    linking = True
                    first_version = version
                    last_version = version
                else:
                    last_version = version

            elif linking:
                linking = False

                if first_version == last_version:
                    versions.append(first_version)
                else:
                    versions.append(f"{first_version} - {last_version}")

                first_version = None
                last_version = None

            if index == len(Discord.config.VERSIONS_LIST) - 1 and linking:
                if first_version == last_version:
                    versions.append(first_version)
                else:
                    versions.append(f"{first_version} - {last_version}")

        return ', '.join(versions)

    def get_meta_fields(self) -> dict[str, str]:
        fields = {"Dimensions": f"{self.width}x{self.height}x{self.depth}",
                  "Volume": str(self.width * self.height * self.depth),
                  "Opening Time": str(self.normal_opening_time),
                  "Closing Time": str(self.normal_closing_time)}

        if self.visible_opening_time and self.visible_closing_time:
            # The times are stored as game ticks, so they need to be divided by 20 to get seconds
            fields["Visible Opening Time"] = self.visible_opening_time / 20
            fields["Visible Closing Time"] = self.visible_closing_time / 20

        fields["Creators"] = ', '.join(sorted(self.creators))
        fields["Date Of Completion"] = str(self.completion_time)
        fields["Versions"] = self.get_versions_string()

        if self.server_ip:
            fields["Server"] = self.server_ip

            if self.coordinates:
                fields["Coordinates"] = self.coordinates

            if self.command:
                fields["Command"] = self.command

        if self.world_download_url:
            fields["World Download"] = self.world_download_url
        if self.video_url:
            fields["Video"] = self.video_url

        return fields

    @staticmethod
    async def add(data: dict) -> Build:
        """Adds a build to the database.

        Returns:
            The Build object that was added.
        """
        db = await DatabaseManager()
        response = await db.table('builds').insert(data, count='exact').execute()
        assert response.count == 1
        return Build.from_dict(response.data[0])

    @staticmethod
    async def from_id(build_id: int) -> Build | None:
        """Creates a new Build object from a database ID.

        Args:
            build_id: The ID of the build to retrieve.

        Returns:
            The Build object with the specified ID, or None if the build was not found.
        """
        db = await DatabaseManager()
        response = await db.table('builds').select('*').eq('id', build_id).maybe_single().execute()
        if response:
            return Build.from_dict(response.data)
        else:
            return None

    @staticmethod
    def from_dict(submission: dict) -> Build:
        """Creates a new Build object from a dictionary."""
        result = Build()

        result.id = submission["id"]
        result.submission_status = submission.get("submission_status", Build.PENDING)
        for fmt in (r"%Y-%m-%dT%H:%M:%S", r"%Y-%m-%dT%H:%M:%S.%f", r"%d-%m-%Y %H:%M:%S"):
            try:
                result.edited_time = datetime.strptime(submission.get("last_update"), fmt)
            except (ValueError, TypeError):
                pass
        else:
            result.edited_time = datetime.now()
        result.record_category = submission["record_category"] if submission.get("record_category") and submission.get("record_category") != "None" else None
        result.door_width = submission.get("door_width")
        result.door_height = submission.get("door_height")
        result.door_type = submission["pattern"].split(", ") if submission["pattern"] else ["Regular"]
        result.door_orientation_type = submission["door_orientation_type"]
        result.wp_restrictions = submission.get("wiring_placement_restrictions").split(", ") if submission.get(
            "wiring_placement_restrictions") else []
        result.comp_restrictions = submission.get("component_restrictions").split(", ") if submission.get(
            "component_restrictions") else []
        result.information = submission.get("information")
        result.width = int(submission["width"])
        result.height = int(submission["height"])
        result.depth = int(submission["depth"])
        result.normal_closing_time = submission["normal_closing_time"]
        result.normal_opening_time = submission["normal_opening_time"]
        result.visible_close_time = submission.get("visible_closing_time")
        result.visible_open_time = submission.get("visible_opening_time")
        # Date of creation is the user provided time, defaulting to the submission time if not provided
        result.completion_time = submission.get("date_of_creation", submission["submission_time"])
        result.creators = submission.get("creators_ign").split(", ") if submission.get("creators_ign") else []
        result.locational = submission["locationality"]
        result.directional = submission["directionality"]
        if submission.get("functional_versions"):
            result.versions = submission.get("functional_versions").split(", ")
        else:
            result.versions = []
        result.image_url = submission.get("image_url")
        result.video_url = submission.get("video_url")
        result.world_download_url = submission.get("world_download_url")
        result.server_ip = submission.get("server_ip")
        result.coordinates = submission.get("coordinates")
        result.command = submission.get("command_to_build")
        result.submitter_id = submission["submitter_id"]

        return result

    @staticmethod
    def from_json(data: dict) -> Build:
        """
        Converts a JSON object to a Build object.

        Args:
            data: the exact JSON object returned by
                `DatabaseManager().table('builds').select(all_build_columns).eq('id', build_id).execute().data[0]`

        Returns:
            A Build object.
        """


    def to_dict(self):
        """Converts the submission to a dictionary with keys conforming to the database column names."""
        return {
            "id": self.id,
            "submission_status": self.submission_status,
            "last_update": self.edited_time.strftime(r'%d-%m-%Y %H:%M:%S'),
            "record_category": self.record_category,
            "door_width": self.door_width,
            "door_height": self.door_height,
            "pattern": ", ".join(self.door_type),
            "door_orientation_type": self.door_orientation_type,
            "wiring_placement_restrictions": ", ".join(self.wp_restrictions),
            "component_restrictions": ", ".join(self.comp_restrictions),
            "information": self.information,
            "width": self.width,
            "height": self.height,
            "depth": self.depth,
            "normal_closing_time": self.normal_closing_time,
            "normal_opening_time": self.normal_opening_time,
            "visible_closing_time": self.visible_closing_time,
            "visible_opening_time": self.visible_opening_time,
            "date_of_creation": self.completion_time,
            "creators_ign": ", ".join(self.creators),
            "functional_versions": ", ".join(self.versions),
            "image_link": self.image_url,
            "video_url": self.video_url,
            "world_download_url": self.world_download_url,
            "server_ip": self.server_ip,
            "coordinates": self.coordinates,
            "command_to_build": self.command,
            "submitter_id": self.submitter_id
        }

    def to_string(self) -> str:
        string = ""

        string += f"ID: {self.id}\n"
        string += f"Submission status: {self.submission_status}"
        string += f"Base Category: {self.record_category}\n"
        if self.door_width:
            string += f"Door Width: {self.door_width}\n"
        if self.door_height:
            string += f"Door Height: {self.door_height}\n"
        string += f"Pattern: {' '.join(self.door_type)}\n"
        string += f"Door Type: {self.door_orientation_type}\n"
        if self.wp_restrictions:
            string += f"Wiring Placement Restrictions: {', '.join(self.wp_restrictions)}\n"
        if self.comp_restrictions:
            string += f"Component Restrictions: {', '.join(self.comp_restrictions)}\n"
        if self.misc_restrictions:
            string += f"Miscellaneous Restrictions: {', '.join(self.misc_restrictions)}\n"
        if self.information:
            string += f"Information: {self.information}\n"
        string += f"Build Width: {self.width}\n"
        string += f"Build Height: {self.height}\n"
        string += f"Build Depth: {self.depth}\n"
        string += f"Relative Closing Time: {self.normal_closing_time}\n"
        string += f"Relative Opening Time: {self.normal_opening_time}\n"
        if self.visible_closing_time:
            string += f"Absolute Closing Time: {self.visible_closing_time}\n"
        if self.visible_opening_time:
            string += f"Absolute Opening Time: {self.visible_opening_time}\n"
        string += f"Date Of Creation: {self.completion_time}\n"
        string += f"Creators: {', '.join(self.creators)}\n"
        string += f"Versions: {', '.join(self.versions)}\n"
        if self.image_url:
            string += f"Image URL: {self.image_url}\n"
        if self.video_url:
            string += f"YouTube Link: {self.video_url}\n"
        if self.world_download_url:
            string += f"World Download: {self.world_download_url}\n"
        if self.server_ip:
            string += f"Server IP: {self.server_ip}\n"
        if self.coordinates:
            string += f"Coordinates: {self.coordinates}\n"
        if self.command:
            string += f"Command: {self.command}\n"
        string += f"Submitted By: {self.submitter_id}\n"

        return string


async def get_all_builds_raw(submission_status: Optional[int] = None) -> list[dict]:
    """Fetches all builds from the database, optionally filtered by submission status.

    Args:
        submission_status: The status of the submissions to filter by. If None, all submissions are returned. See Build class for possible values.

    Returns:
        A list of dictionaries, each representing a build.
    """
    db = await DatabaseManager()
    query = db.table('builds').select(all_build_columns)

    if submission_status:
        query = query.eq('submission_status', submission_status)

    response = await query.execute()
    return response.data if response else []


async def get_builds(build_ids: list[int]) -> list[Build | None]:
    """Fetches builds from the database with the given IDs."""
    if len(build_ids) == 0:
        return []

    db = await DatabaseManager()
    response = await db.table('builds').select(all_build_columns).in_('id', build_ids).execute()

    builds: list[Build | None] = [None] * len(build_ids)
    for build_json in response.data:
        idx = build_ids.index(build_json['id'])
        builds[idx] = Build.from_json(build_json)
    return builds


async def update_build(build_id: int, data: dict) -> Build | None:
    """ Update a build in the database using the given data. No validation is done on the data.

    Args:
        build_id: The ID of the build to update.
        data: A dictionary containing the data to update. The keys should match the column names in the database.
            See `Build.to_dict()` for an example.

    Returns:
        The updated build, or None if the build was not found.
    """
    db = await DatabaseManager()
    update_values = {key: value for key, value in data.items() if key != 'id' and value is not None}
    response = await db.table('builds').update(update_values, count='exact').eq('id', build_id).execute()
    if response.count == 1:
        return Build.from_dict(response.data[0])
    return None


async def get_unsent_builds(server_id: int) -> list[Build] | None:
    """Get all the builds that have not been posted on the server"""
    db = await DatabaseManager()

    # Builds that have not been posted on the server
    server_unsent_builds = await db.rpc('get_unsent_builds', {'server_id_input': server_id}).execute().data
    return [Build.from_dict(unsent_sub) for unsent_sub in server_unsent_builds]


async def main():
    from pprint import pprint
    pprint(await get_all_builds_raw())

if __name__ == '__main__':
    asyncio.run(main())
