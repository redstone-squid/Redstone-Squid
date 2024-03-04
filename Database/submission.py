from datetime import datetime
from typing import Literal, Optional

import discord

import Database.config as config
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
        self.base_category: Literal["Smallest", "Fastest", "Smallest Observerless", "Fastest Observerless", "First"] | None = None
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
        self.relative_close_time: float | None = None
        self.relative_open_time: float | None = None
        self.absolute_close_time: Optional[float] = None
        self.absolute_open_time: Optional[float] = None
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
        title = self.base_category + " "

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
        if self.door_type is None:
            title += "Door."
        elif self.door_type == "SKY":
            title += "Skydoor."
        elif self.door_type == "TRAP":
            title += "Trapdoor."

        return title

    def get_description(self) -> str | None:
        description = []

        # Component Restrictions
        if self.so_restrictions is not None and self.so_restrictions[0] != "None":
            description.append(", ".join(self.so_restrictions))

        if not config.VERSIONS_LIST[-1] in self.versions:
            description.append("**Broken** in current version.")

        if self.locational == "LOCATIONAL":
            description.append("**Locational**.")
        elif self.locational == "LOCATIONAL_FIX":
            description.append("**Locational** with known fixes for each location.")

        if self.directional == "DIRECTIONAL":
            description.append("**Directional**.")
        elif self.directional == "DIRECTIONAL_FIX":
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

        for index, version in enumerate(config.VERSIONS_LIST):
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

            if index == len(config.VERSIONS_LIST) - 1 and linking:
                if first_version == last_version:
                    versions.append(first_version)
                else:
                    versions.append(f"{first_version} - {last_version}")

        return ', '.join(versions)

    def get_meta_fields(self) -> dict[str, str]:
        fields = {"Dimensions": f"{self.build_width}x{self.build_height}x{self.build_depth}",
                  "Volume": str(self.build_width * self.build_height * self.build_depth),
                  "Opening Time": str(self.relative_open_time),
                  "Closing Time": str(self.relative_close_time)}

        if self.absolute_open_time and self.absolute_close_time:
            fields["Absolute Opening Time"] = self.absolute_open_time
            fields["Absolute Closing Time"] = self.absolute_close_time

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
        result = Submission()

        result.id = submission["submission_id"]
        result.last_updated = datetime.strptime(submission["last_update"],
                                                r"%Y-%m-%dT%H:%M:%S")  # TODO: make this stop relying on a specific format
        result.base_category = submission["record_category"]
        result.door_width = submission.get("door_width")
        result.door_height = submission.get("door_height")
        result.door_pattern = submission["pattern"]  # TODO: removed .split(", ") from here so need to fix the database
        result.door_type = submission["door_type"]
        result.fo_restrictions = submission.get("wiring_placement_restrictions")  # TODO: removed .split(", ")
        result.so_restrictions = submission.get("component_restrictions")  # TODO: removed .split(", ")
        result.information = submission.get("information")
        result.build_width = int(submission["build_width"])
        result.build_height = int(submission["build_height"])
        result.build_depth = int(submission["build_depth"])
        # The times are stored as game ticks, so they need to be divided by 20 to get seconds
        result.relative_close_time = submission["relative_closing_time"] / 20
        result.relative_open_time = submission["relative_opening_time"] / 20
        if submission["absolute_closing_time"]:
            result.absolute_close_time = submission["absolute_closing_time"] / 20
        if submission["absolute_opening_time"]:
            result.absolute_open_time = submission["absolute_opening_time"] / 20
        result.build_date = submission.get("date_of_creation")
        if not result.build_date:
            result.build_date = submission["submission_time"]
        result.creators = submission.get("creators_ign")  # TODO: removed .split(", ")
        # Locational with known fixes for each location
        # Locational without known fixes for each location
        result.locational = submission["locationality"]
        result.directional = submission["directionality"]
        result.versions = submission.get("functional_versions")  # TODO: removed .split(", ")
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
        result.submitted_by = submission["submitter_name"]  # TODO

        return result

    def to_dict(self):
        return {
            "Submission ID": self.id,
            "Last Update": self.last_updated.strftime(r'%d-%m-%Y %H:%M:%S'),
            "Record Category": self.base_category,
            "Door Width": self.door_width,
            "Door Height": self.door_height,
            "Pattern": ", ".join(self.door_pattern),
            "Door Type": self.door_type,
            "wiring_placement_restrictions": ", ".join(self.fo_restrictions),
            "component_restrictions": ", ".join(self.so_restrictions),
            "Information About Build": self.information,
            "Width Of Build": self.build_width,
            "Height Of Build": self.build_height,
            "Depth Of Build": self.build_depth,
            "Relative Closing Time": self.relative_close_time,
            "Relative Opening Time": self.relative_open_time,
            "Absolute Closing Time": self.absolute_close_time,
            "Absolute Opening Time": self.absolute_open_time,
            "Date Of Creation": self.build_date,
            "In Game Name(s) Of Creator(s)": ", ".join(self.creators),
            "Locationality": self.locational,
            "Directionality": self.directional,
            "Versions Which Submission Works In": ", ".join(self.versions),
            "Link To Image": self.image_url,
            "Link To YouTube Video": self.video_link,
            "Link To World Download": self.world_download_link,
            "Server IP": self.server_ip,
            "Coordinates": self.coordinates,
            "Command To Get To Build/Plot": self.command,
            "Your IGN / Discord Handle": self.submitted_by
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
        string += f"Relative Closing Time: {self.relative_close_time}\n"
        string += f"Relative Opening Time: {self.relative_open_time}\n"
        if self.absolute_close_time:
            string += f"Absolute Closing Time: {self.absolute_close_time}\n"
        if self.absolute_open_time:
            string += f"Absolute Opening Time: {self.absolute_open_time}\n"
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
