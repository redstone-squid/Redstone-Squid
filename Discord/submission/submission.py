"""A """
from datetime import datetime
from typing import Literal, Optional

import discord

import Discord.config
from Discord import utils


class Submission:
    """A class representing a submission to the database. This class is used to store and manipulate submissions."""
    PENDING = 0
    CONFIRMED = 1
    DENIED = 2

    def __init__(self):
        """Initializes an empty submission.

         This should not be used externally. Use `from_dict()` instead."""
        # type | None indicates that the value is expected to be filled in.
        # Optional[type] is used to indicate that the value is actually optional.
        # If you do not fill in parameters that are typed "type | None", errors will occur from all parts of the code.
        self.id: int | None = None
        self.last_updated: datetime | None = None
        self.base_category: Literal["Smallest", "Fastest", "First"] | None = None
        self.door_width: int | None = None
        self.door_height: int | None = None
        self.door_pattern: Optional[list[str]] = None
        self.door_type: Optional[Literal["TRAP", "SKY"]] = None
        self.fo_restrictions: Optional[list[str]] = None
        self.so_restrictions: Optional[list[str]] = None
        self.information: Optional[str] = None
        self.build_width: int | None = None
        self.build_height: int | None = None
        self.build_depth: int | None = None
        self.normal_closing_time: float | None = None
        self.normal_opening_time: float | None = None
        self.visible_closing_time: Optional[float] = None
        self.visible_opening_time: Optional[float] = None
        self.build_date: Optional[str] = None
        self.creators: Optional[str] = None
        self.locational: Optional[Literal["LOCATIONAL", "LOCATIONAL_FIX"]] = None
        self.directional: Optional[Literal["DIRECTIONAL", "DIRECTIONAL_FIX"]] = None
        self.versions: list[str] | None = None
        self.image_url: Optional[str] = None
        self.video_link: Optional[str] = None
        self.world_download_link: Optional[str] = None
        self.server_ip: Optional[str] = None
        self.coordinates: Optional[str] = None
        self.command: Optional[str] = None
        self.submitted_by: str | None = None

    def generate_embed(self: "Submission"):
        title = self.get_title()
        description = self.get_description()

        if description is None:
            em = discord.Embed(title=title, colour=utils.discord_green)
        else:
            em = discord.Embed(title=title, description=description, colour=utils.discord_green)

        fields = self.get_meta_fields()
        for key, val in fields.items():
            em.add_field(name=key, value=val, inline=True)

        if self.image_url:
            em.set_image(url=self.image_url)

        em.set_footer(text=f'Submission ID: {self.id}.')

        return em

    def get_title(self):
        # Category
        title = f"{self.base_category or ''} "

        # Door dimensions
        if self.door_width and self.door_height:
            title += f"{self.door_width}x{self.door_height} "
        elif self.door_width:
            title += f"{self.door_width} Wide "
        elif self.door_height:
            title += f"{self.door_height} High "

        # Wiring Placement Restrictions
        if self.fo_restrictions is not None:
            for restriction in self.fo_restrictions:
                if restriction != "None":
                    title += f"{restriction} "

        # Pattern
        if self.door_pattern[0] != "Regular":
            for pattern in self.door_pattern:
                title += f"{pattern} "

        # Door type
        title += self.door_type

        return title

    def get_description(self) -> str | None:
        description = []

        # Component Restrictions
        if self.so_restrictions and self.so_restrictions[0] != "None":
            description.append(", ".join(self.so_restrictions))

        if not Discord.config.VERSIONS_LIST[-1] in self.versions:
            description.append("**Broken** in current version.")

        if self.locational == "Locational":
            description.append("**Locational**.")
        elif self.locational == "Locational with fixes":
            description.append("**Locational** with known fixes for each location.")

        if self.directional == "Directional":
            description.append("**Directional**.")
        elif self.directional == "Directional with fixes":
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
        fields = {"Dimensions": f"{self.build_width}x{self.build_height}x{self.build_depth}",
                  "Volume": str(self.build_width * self.build_height * self.build_depth),
                  "Opening Time": str(self.normal_opening_time),
                  "Closing Time": str(self.normal_closing_time)}

        if self.visible_opening_time and self.visible_closing_time:
            fields["Visible Opening Time"] = self.visible_opening_time
            fields["Visible Closing Time"] = self.visible_closing_time

        fields["Creators"] = ', '.join(sorted(self.creators))
        fields["Date Of Completion"] = str(self.build_date)
        fields["Versions"] = self.get_versions_string()

        if self.server_ip:
            fields["Server"] = self.server_ip

            if self.coordinates:
                fields["Coordinates"] = self.coordinates

            if self.command:
                fields["Command"] = self.command

        if self.world_download_link:
            fields["World Download"] = self.world_download_link
        if self.video_link:
            fields["Video"] = self.video_link

        return fields

    @staticmethod
    def from_dict(submission: dict) -> "Submission":
        """Creates a new Submission object from a dictionary."""
        result = Submission()

        result.id = submission["submission_id"]
        for fmt in (r"%Y-%m-%dT%H:%M:%S", r"%Y-%m-%dT%H:%M:%S.%f", r"%d-%m-%Y %H:%M:%S"):
            try:
                result.last_updated = datetime.strptime(submission.get("last_update"), fmt)
            except ValueError:
                pass
        result.base_category = submission["record_category"] if submission.get("record_category") and submission.get("record_category") != "None" else None
        result.door_width = submission.get("door_width")
        result.door_height = submission.get("door_height")
        result.door_pattern = submission["pattern"].split(", ") if submission["pattern"] else ["Regular"]
        result.door_type = submission["door_type"]
        result.fo_restrictions = submission.get("wiring_placement_restrictions").split(", ") if submission.get(
            "wiring_placement_restrictions") else []
        result.so_restrictions = submission.get("component_restrictions").split(", ") if submission.get(
            "component_restrictions") else []
        result.information = submission.get("information")
        result.build_width = int(submission["build_width"])
        result.build_height = int(submission["build_height"])
        result.build_depth = int(submission["build_depth"])
        # The times are stored as game ticks, so they need to be divided by 20 to get seconds
        result.normal_closing_time = submission["normal_closing_time"] / 20
        result.normal_opening_time = submission["normal_opening_time"] / 20
        if submission["visible_closing_time"]:
            result.visible_close_time = submission["visible_closing_time"] / 20
        if submission["visible_opening_time"]:
            result.visible_open_time = submission["visible_opening_time"] / 20
        result.build_date = submission.get("date_of_creation")
        if not result.build_date:
            result.build_date = submission["submission_time"]
        result.creators = submission.get("creators_ign").split(", ") if submission.get("creators_ign") else []
        # Locational with known fixes for each location
        # Locational without known fixes for each location
        result.locational = submission["locationality"]
        result.directional = submission["directionality"]
        result.versions = submission.get("functional_versions").split(", ") if submission.get("functional_versions") else []
        if submission["image_link"]:  # TODO: maybe better as image_url
            result.image_url = submission["image_link"]
        if submission["video_link"]:
            result.video_link = submission["video_link"]
        if submission["world_download_link"]:
            result.world_download_link = submission["world_download_link"]
        if submission["server_ip"]:
            result.server_ip = submission["server_ip"]
        if submission["coordinates"]:
            result.coordinates = submission["coordinates"]
        if submission["command_to_build"]:
            result.command = submission["command_to_build"]
        result.submitted_by = submission["submitted_by"]

        return result

    def to_dict(self):
        """Converts the submission to a dictionary with keys conforming to the database column names."""
        return {
            "submission_id": self.id,
            "last_update": self.last_updated.strftime(r'%d-%m-%Y %H:%M:%S'),
            "record_category": self.base_category,
            "door_width": self.door_width,
            "door_height": self.door_height,
            "pattern": ", ".join(self.door_pattern),
            "door_type": self.door_type,
            "wiring_placement_restrictions": ", ".join(self.fo_restrictions),
            "component_restrictions": ", ".join(self.so_restrictions),
            "information": self.information,
            "build_width": self.build_width,
            "build_height": self.build_height,
            "build_depth": self.build_depth,
            "normal_closing_time": self.normal_closing_time,
            "normal_opening_time": self.normal_opening_time,
            "visible_closing_time": self.visible_closing_time,
            "visible_opening_time": self.visible_opening_time,
            "date_of_creation": self.build_date,
            "creators_ign": ", ".join(self.creators),
            "locationality": self.locational,
            "directionality": self.directional,
            "functional_versions": ", ".join(self.versions),
            "image_link": self.image_url,
            "video_link": self.video_link,
            "world_download_link": self.world_download_link,
            "server_ip": self.server_ip,
            "coordinates": self.coordinates,
            "command_to_build": self.command,
            "submitted_by": self.submitted_by
        }

    def to_string(self) -> str:
        string = ""

        string += f"ID: {self.id}\n"
        string += f"Base Catagory: {self.base_category}\n"
        if self.door_width:
            string += f"Door Width: {self.door_width}\n"
        if self.door_height:
            string += f"Door Height: {self.door_height}\n"
        string += f"Pattern: {' '.join(self.door_pattern)}\n"
        string += f"Door Type: {self.door_type}\n"
        if self.fo_restrictions:
            string += f"Wiring Placement Restrictions: {', '.join(self.fo_restrictions)}\n"
        if self.so_restrictions:
            string += f"Component Restrictions: {', '.join(self.so_restrictions)}\n"
        if self.information:
            string += f"Information: {self.information}\n"
        string += f"Build Width: {self.build_width}\n"
        string += f"Build Height: {self.build_height}\n"
        string += f"Build Depth: {self.build_depth}\n"
        string += f"Relative Closing Time: {self.normal_closing_time}\n"
        string += f"Relative Opening Time: {self.normal_opening_time}\n"
        if self.visible_closing_time:
            string += f"Absolute Closing Time: {self.visible_closing_time}\n"
        if self.visible_opening_time:
            string += f"Absolute Opening Time: {self.visible_opening_time}\n"
        string += f"Date Of Creation: {self.build_date}\n"
        string += f"Creators: {', '.join(self.creators)}\n"
        if self.locational:
            string += f"Locationality Tag: {self.locational}\n"
        if self.directional:
            string += f"Directionality Tag: {self.directional}\n"
        string += f"Versions: {', '.join(self.versions)}\n"
        if self.image_url:
            string += f"Image URL: {self.image_url}\n"
        if self.video_link:
            string += f"YouTube Link: {self.video_link}\n"
        if self.world_download_link:
            string += f"World Download: {self.world_download_link}\n"
        if self.server_ip:
            string += f"Server IP: {self.server_ip}\n"
        if self.coordinates:
            string += f"Coordinates: {self.coordinates}\n"
        if self.command:
            string += f"Command: {self.command}\n"
        string += f"Submitted By: {self.submitted_by}\n"

        return string
