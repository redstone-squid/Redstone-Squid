from datetime import datetime
from typing import Literal, Optional

import Database.config as config


class Submission:
    """A class representing a submission to the database. This class is used to store and manipulate submissions."""
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
        self.youtube_link: Optional[str] = None
        self.world_download_link: Optional[str] = None
        self.server_ip: Optional[str] = None
        self.coordinates: Optional[str] = None
        self.command: Optional[str] = None
        self.submitted_by: str | None = None

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

        # First order restrictions
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

        # Second order restrictions
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
        if self.youtube_link:
            fields["Video"] = self.youtube_link

        return fields

    @staticmethod
    def from_dict(submission: dict) -> "Submission":
        result = Submission()

        result.id = int(submission["Submission ID"])
        result.last_updated = datetime.strptime(submission["Last Update"],
                                                r"%d-%m-%Y %H:%M:%S")  # TODO: make this stop relying on a specific format
        result.base_category = submission["Record Category"]
        if submission["Door Width"]:
            result.door_width = int(submission["Door Width"])
        if submission["Door Height"]:
            result.door_height = int(submission["Door Height"])
        result.door_pattern = submission["Pattern"].split(", ")  # TODO: wtf is this assuming
        if submission["Door Type"] == "Trapdoor":
            result.door_type = "TRAP"
        if submission["Door Type"] == "Skydoor":
            result.door_type = "SKY"
        if submission["First Order Restrictions"]:
            result.fo_restrictions = submission["First Order Restrictions"].split(", ")
        if submission["Second Order Restrictions"]:
            result.so_restrictions = submission["Second Order Restrictions"].split(", ")
        if submission["Information About Build"]:
            result.information = submission["Information About Build"]
        result.build_width = int(submission["Width Of Build"])
        result.build_height = int(submission["Height Of Build"])
        result.build_depth = int(submission["Depth Of Build"])
        result.relative_close_time = float(submission["Relative Closing Time"])
        result.relative_open_time = float(submission["Relative Opening Time"])
        if submission["Absolute Closing Time"]:
            result.absolute_close_time = float(submission["Absolute Closing Time"])
        if submission["Absolute Opening Time"]:
            result.absolute_open_time = float(submission["Absolute Opening Time"])
        if submission["Date Of Creation"]:
            result.build_date = submission["Date Of Creation"]
        else:
            result.build_date = submission["Timestamp"]
        result.creators = submission["In Game Name(s) Of Creator(s)"].split(",")
        if submission["Locationality"] == "Locational with known fixes for each location":
            result.locational = "LOCATIONAL_FIX"
        elif submission["Locationality"] == "Locational without known fixes for each location":
            result.locational = "LOCATIONAL"
        if submission["Directionality"] == "Directional with known fixes for each direction":
            result.directional = "DIRECTIONAL_FIX"
        elif submission["Directionality"] == "Directional without known fixes for each direction":
            result.directional = "DIRECTIONAL"
        result.versions = str(submission["Versions Which Submission Works In"]).split(", ")
        if submission["Link To Image"]:
            result.image_url = submission["Link To Image"]
        if submission["Link To YouTube Video"]:
            result.youtube_link = submission["Link To YouTube Video"]
        if submission["Link To World Download"]:
            result.world_download_link = submission["Link To World Download"]
        if submission["Server IP"]:
            result.server_ip = submission["Server IP"]
        if submission["Coordinates"]:
            result.coordinates = submission["Coordinates"]
        if submission["Command To Get To Build/Plot"]:
            result.command = submission["Command To Get To Build/Plot"]
        result.submitted_by = submission["Your IGN / Discord Handle"]

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
            "First Order Restrictions": ", ".join(self.fo_restrictions),
            "Second Order Restrictions": ", ".join(self.so_restrictions),
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
            "Link To YouTube Video": self.youtube_link,
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
            string += f"First Order Restrictions: {', '.join(self.fo_restrictions)}\n"
        if self.so_restrictions:
            string += f"Second Order Restrictions: {', '.join(self.so_restrictions)}\n"
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
        if self.youtube_link:
            string += f"YouTube Link: {self.youtube_link}\n"
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
