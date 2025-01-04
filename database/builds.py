"""Submitting and retrieving submissions to/from the database"""

from __future__ import annotations

import asyncio
from asyncio import Task
from dataclasses import dataclass, field
from functools import cache
from collections.abc import Sequence, Mapping
from typing import Literal, Any, cast, TypeVar

import discord
from discord.utils import escape_markdown
from postgrest.base_request_builder import APIResponse
from postgrest.types import CountMethod

from database.schema import (
    BuildRecord,
    DoorRecord,
    TypeRecord,
    RestrictionRecord,
    Info,
    VersionRecord,
    UnknownRestrictions,
    RecordCategory,
    DoorOrientationName,
    QuantifiedVersionRecord,
)
from database import DatabaseManager
from database.server_settings import get_server_setting
from database.user import add_user
from database.utils import utcnow, get_version_string
from database.enums import Status, Category
from bot import utils


T = TypeVar("T")


all_build_columns = "*, versions(*), build_links(*), build_creators(*), users(*), types(*), restrictions(*), doors(*), extenders(*), utilities(*), entrances(*)"
"""All columns that needs to be joined in the build table to get all the information about a build."""


@dataclass
class Build:
    """A submission to the database.

    This is a very large class, the methods are ordered as follows:
    - Static constructors
    - Magic (dunder) methods
    - Properties
    - Normal methods
    - load(), save() and the helper methods it calls
    """

    id: int | None = None
    submission_status: Status | None = None
    category: Category | None = None
    record_category: RecordCategory | None = None
    functional_versions: list[str] | None = None

    width: int | None = None
    height: int | None = None
    depth: int | None = None

    door_width: int | None = None
    door_height: int | None = None
    door_depth: int | None = None

    door_type: list[str] | None = None
    door_orientation_type: DoorOrientationName | None = None

    wiring_placement_restrictions: list[str] = field(default_factory=list)
    component_restrictions: list[str] = field(default_factory=list)
    miscellaneous_restrictions: list[str] = field(default_factory=list)

    normal_closing_time: int | None = None
    normal_opening_time: int | None = None
    visible_closing_time: int | None = None
    visible_opening_time: int | None = None

    information: Info | None = None
    creators_ign: list[str] = field(default_factory=list)

    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    world_download_urls: list[str] = field(default_factory=list)

    # TODO: Put these three into server_info
    server_ip: str | None = None
    coordinates: str | None = None
    command: str | None = None

    submitter_id: int | None = None
    # TODO: save the submitted time too
    completion_time: str | None = None
    edited_time: str | None = None

    @staticmethod
    async def from_id(build_id: int) -> Build | None:
        """Creates a new Build object from a database ID.

        Args:
            build_id: The ID of the build to retrieve.

        Returns:
            The Build object with the specified ID, or None if the build was not found.
        """
        build = Build()
        build.id = build_id
        return await build.load()

    @staticmethod
    def from_dict(submission: dict) -> Build:
        """Creates a new Build object from a dictionary. No validation is done on the data."""
        build = Build()
        for attr in build:
            if attr in submission:
                setattr(build, attr, submission[attr])

        return build

    @staticmethod
    def from_json(data: dict[str, Any]) -> Build:
        """
        Converts a JSON object to a Build object.

        Args:
            data: the exact JSON object returned by
                `DatabaseManager().table('builds').select(all_build_columns).eq('id', build_id).execute().data[0]`

        Returns:
            A Build object.
        """
        build = Build()
        build.id = data["id"]
        build.submission_status = data["submission_status"]
        build.record_category = data["record_category"]
        build.category = data["category"]

        build.width = data["width"]
        build.height = data["height"]
        build.depth = data["depth"]

        match data["category"]:
            case "Door":
                category_data = data["doors"]
            case "Extender":
                category_data = data["extenders"]
            case "Utility":
                category_data = data["utilities"]
            case "Entrance":
                category_data = data["entrances"]

        # FIXME: This is hardcoded for now
        if data.get("types"):
            types = data["types"]
            build.door_type = [type_["name"] for type_ in types]
        else:
            build.door_type = ["Regular"]

        build.door_orientation_type = data["doors"]["orientation"]
        build.door_width = data["doors"]["door_width"]
        build.door_height = data["doors"]["door_height"]
        build.door_depth = data["doors"]["door_depth"]
        build.normal_closing_time = data["doors"]["normal_closing_time"]
        build.normal_opening_time = data["doors"]["normal_opening_time"]
        build.visible_closing_time = data["doors"]["visible_closing_time"]
        build.visible_opening_time = data["doors"]["visible_opening_time"]

        restrictions: list[RestrictionRecord] = data.get("restrictions", [])
        build.wiring_placement_restrictions = [r["name"] for r in restrictions if r["type"] == "wiring-placement"]
        build.component_restrictions = [r["name"] for r in restrictions if r["type"] == "component"]
        build.miscellaneous_restrictions = [r["name"] for r in restrictions if r["type"] == "miscellaneous"]

        build.information = data["information"]

        creators: list[dict[str, Any]] = data.get("users", [])
        build.creators_ign = [creator["ign"] for creator in creators]

        versions: list[VersionRecord] = data.get("versions", [])
        build.functional_versions = [get_version_string(v) for v in versions]

        links: list[dict[str, Any]] = data.get("build_links", [])
        build.image_urls = [link["url"] for link in links if link["media_type"] == "image"]
        build.video_urls = [link["url"] for link in links if link["media_type"] == "video"]
        build.world_download_urls = [link["url"] for link in links if link["media_type"] == "world-download"]

        server_info: dict[str, Any] = data["server_info"]
        if server_info:
            build.server_ip = server_info.get("server_ip")
            build.coordinates = server_info.get("coordinates")
            build.command = server_info.get("command_to_build")

        build.submitter_id = data["submitter_id"]
        build.completion_time = data["completion_time"]
        build.edited_time = data["edited_time"]

        return build

    def __iter__(self):
        """Iterates over the *attributes* of the Build object."""
        for attr in [a for a in dir(self) if not a.startswith("__") and not callable(getattr(self, a))]:
            yield attr

    @property
    def dimensions(self) -> tuple[int | None, int | None, int | None]:
        """The dimensions of the build."""
        return self.width, self.height, self.depth

    @dimensions.setter
    def dimensions(self, dimensions: tuple[int | None, int | None, int | None]) -> None:
        self.width, self.height, self.depth = dimensions

    @property
    def door_dimensions(self) -> tuple[int | None, int | None, int | None]:
        """The dimensions of the door (hallway)."""
        return self.door_width, self.door_height, self.door_depth

    @door_dimensions.setter
    def door_dimensions(self, dimensions: tuple[int | None, int | None, int | None]) -> None:
        self.door_width, self.door_height, self.door_depth = dimensions

    # TODO: Invalidate cache every, say, 1 day (or make supabase callback whenever the table is updated)
    @staticmethod
    @cache
    async def fetch_all_restrictions() -> list[RestrictionRecord]:
        """Fetches all restrictions from the database."""
        response: APIResponse[RestrictionRecord] = await DatabaseManager().table("restrictions").select("*").execute()
        return response.data

    def get_restrictions(
        self,
    ) -> dict[
        Literal["wiring_placement_restrictions", "component_restrictions", "miscellaneous_restrictions"],
        Sequence[str] | None,
    ]:
        """The restrictions of the build."""
        return {
            "wiring_placement_restrictions": self.wiring_placement_restrictions,
            "component_restrictions": self.component_restrictions,
            "miscellaneous_restrictions": self.miscellaneous_restrictions,
        }

    async def set_restrictions(
        self,
        restrictions: Sequence[str]
        | Mapping[
            Literal["wiring_placement_restrictions", "component_restrictions", "miscellaneous_restrictions"],
            Sequence[str],
        ],
    ) -> None:
        """Sets the restrictions of the build."""
        if isinstance(restrictions, Mapping):
            self.wiring_placement_restrictions = list(restrictions.get("wiring_placement_restrictions", []))
            self.component_restrictions = list(restrictions.get("component_restrictions", []))
            self.miscellaneous_restrictions = list(restrictions.get("miscellaneous_restrictions", []))
        else:
            self.wiring_placement_restrictions = []
            self.component_restrictions = []
            self.miscellaneous_restrictions = []

            for restriction in await self.fetch_all_restrictions():
                for door_restriction in restrictions:
                    if door_restriction.lower() == restriction["name"].lower():
                        if restriction["type"] == "wiring-placement":
                            self.wiring_placement_restrictions.append(restriction["name"])
                        elif restriction["type"] == "component":
                            self.component_restrictions.append(restriction["name"])
                        elif restriction["type"] == "miscellaneous":
                            self.miscellaneous_restrictions.append(restriction["name"])

    async def get_channel_type_to_post_to(self: Build) -> Literal["Smallest", "Fastest", "First", "Builds", "Vote"]:
        """Gets the type of channel to post a submission to."""

        target: Literal["Smallest", "Fastest", "First", "Builds", "Vote"]

        match (self.submission_status, self.record_category):
            case (Status.PENDING, _):
                target = "Vote"
            case (Status.DENIED, _):
                raise ValueError("Denied submissions should not be posted.")
            case (Status.CONFIRMED, None):
                target = "Builds"
            case (Status.CONFIRMED, "Smallest"):
                target = "Smallest"
            case (Status.CONFIRMED, "Fastest"):
                target = "Fastest"
            case (Status.CONFIRMED, "First"):
                target = "First"
            case _:
                raise ValueError("Invalid status or record category")

        return target

    def diff(self, other: Build, *, allow_different_id: bool = False) -> list[tuple[str, T, T]]:
        """
        Returns the differences between this build and another

        Args:
            other: Another build to compare to.
            allow_different_id: Whether the ID of the builds can be different.

        Returns:
            A list of tuples containing the attribute name, the value of this build, and the value of the other build.

        Raises:
            ValueError: If the IDs of the builds are different and allow_different_id is False.
        """
        if self.id != other.id and not allow_different_id:
            raise ValueError("The IDs of the builds are different.")

        differences: list[tuple[str, T, T]] = []
        for attr in self:
            if attr == "id":
                continue
            if getattr(self, attr) != getattr(other, attr):
                differences.append((attr, getattr(self, attr), getattr(other, attr)))

        return differences

    async def load(self) -> Build:
        """
        Loads the build from the database. All previous data is overwritten.

        Returns:
            The Build object.

        Raises:
            ValueError: If the build was not found or build.id is not set.
        """
        if self.id is None:
            raise ValueError("Build ID is missing.")

        db = DatabaseManager()
        response = await db.table("builds").select(all_build_columns).eq("id", self.id).maybe_single().execute()
        if not response:
            raise ValueError("Build not found in the database.")
        return Build.from_json(response.data)

    async def save(self) -> None:
        """
        Updates the build in the database with the given data.

        If the build does not exist in the database, it will be inserted instead.
        """
        self.edited_time = utcnow()

        data = {key: value for key, value in self.as_dict().items() if value is not None}
        build_data = {key: data[key] for key in BuildRecord.__annotations__.keys() if key in data}
        # information is a special JSON field in the database that stores various information about the build
        # this needs to be kept because it will be updated later
        information: Info = build_data.get("information", {})

        db = DatabaseManager()
        response: APIResponse[BuildRecord]
        if self.id:
            response = await db.table("builds").update(build_data, count=CountMethod.exact).eq("id", self.id).execute()
            assert response.count == 1
            delete_build_on_error = False
        else:
            response = await db.table("builds").insert(build_data, count=CountMethod.exact).execute()
            assert response.count == 1
            self.id = response.data[0]["id"]
            delete_build_on_error = True

        try:
            background_tasks: set[Task[Any]] = set()
            async with asyncio.TaskGroup() as tg:
                background_tasks.add(tg.create_task(self._update_build_subcategory_table(data)))
                background_tasks.add(tg.create_task(self._update_build_links_table(data)))
                background_tasks.add(tg.create_task(self._update_build_creators_table(data)))
                background_tasks.add(tg.create_task(self._update_build_versions_table(data)))
                unknown_restrictions = tg.create_task(self._update_build_restrictions_table(data))
                unknown_types = tg.create_task(self._update_build_types_table(data))

            if unknown_restrictions.result():
                information["unknown_restrictions"] = unknown_restrictions.result()
            if unknown_types.result():
                information["unknown_patterns"] = unknown_types.result()
            # Update the information field in the database to store any unknown restrictions or types
            await db.table("builds").update({"information": information}).eq("id", self.id).execute()
        except:
            if delete_build_on_error:
                await db.table("builds").delete().eq("id", self.id).execute()
            raise

    async def _update_build_subcategory_table(self, data: dict[str, Any]) -> None:
        """Updates the subcategory table with the given data."""
        db = DatabaseManager()
        if data["category"] == "Door":
            doors_data = {key: data[key] for key in DoorRecord.__annotations__.keys() if key in data}
            # FIXME: database and Build class have different names for the same field
            doors_data["orientation"] = data["door_orientation_type"]
            doors_data["build_id"] = self.id
            await db.table("doors").upsert(doors_data).execute()
        elif data["category"] == "Extender":
            raise NotImplementedError
        elif data["category"] == "Utility":
            raise NotImplementedError
        elif data["category"] == "Entrance":
            raise NotImplementedError
        else:
            raise ValueError("Build category must be set")

    async def _update_build_restrictions_table(self, data: dict[str, Any]) -> UnknownRestrictions:
        """Updates the build_restrictions table with the given data"""
        db = DatabaseManager()
        build_restrictions = (
            data.get("wiring_placement_restrictions", [])
            + data.get("component_restrictions", [])
            + data.get("miscellaneous_restrictions", [])
        )
        response: APIResponse[RestrictionRecord] = (
            await db.table("restrictions").select("*").in_("name", build_restrictions).execute()
        )
        restriction_ids = [restriction["id"] for restriction in response.data]
        build_restrictions_data = list(
            {"build_id": self.id, "restriction_id": restriction_id} for restriction_id in restriction_ids
        )
        if build_restrictions_data:
            await db.table("build_restrictions").upsert(build_restrictions_data).execute()

        unknown_restrictions: UnknownRestrictions = {}
        unknown_wiring_restrictions = []
        unknown_component_restrictions = []
        for wiring_restriction in data.get("wiring_placement_restrictions", []):
            if wiring_restriction not in [
                restriction["name"] for restriction in response.data if restriction["type"] == "wiring-placement"
            ]:
                unknown_wiring_restrictions.append(wiring_restriction)
        for component_restriction in data.get("component_restrictions", []):
            if component_restriction not in [
                restriction["name"] for restriction in response.data if restriction["type"] == "component"
            ]:
                unknown_component_restrictions.append(component_restriction)
        # TODO: miscellaneous restrictions?
        if unknown_wiring_restrictions:
            unknown_restrictions["wiring_placement_restrictions"] = unknown_wiring_restrictions
        if unknown_component_restrictions:
            unknown_restrictions["component_restrictions"] = unknown_component_restrictions
        return unknown_restrictions

    async def _update_build_types_table(self, data: dict[str, Any]) -> list[str]:
        """Updates the build_types table with the given data.

        Returns:
            A list of unknown types.
        """
        db = DatabaseManager()
        if data.get("door_type") is not None:
            door_type = data.get("door_type")
            if not isinstance(door_type, list):
                raise ValueError("Door type must be a list")
        else:
            door_type = ["Regular"]
        response: APIResponse[TypeRecord] = (
            await db.table("types")
            .select("*")
            .eq("build_category", data.get("category"))
            .in_("name", door_type)
            .execute()
        )
        type_ids = [type_["id"] for type_ in response.data]
        build_types_data = list({"build_id": self.id, "type_id": type_id} for type_id in type_ids)
        await db.table("build_types").upsert(build_types_data).execute()
        unknown_types = []
        for door_type in data.get("door_type", []):
            if door_type not in [type_["name"] for type_ in response.data]:
                unknown_types.append(door_type)
        return unknown_types

    async def _update_build_links_table(self, data: dict[str, Any]) -> None:
        """Updates the build_links table with the given data."""
        build_links_data = []
        if data.get("image_urls"):
            build_links_data.extend(
                {"build_id": self.id, "url": link, "media_type": "image"} for link in data.get("image_urls", [])
            )
        if data.get("video_urls"):
            build_links_data.extend(
                {"build_id": self.id, "url": link, "media_type": "video"} for link in data.get("video_urls", [])
            )
        if data.get("world_download_urls"):
            build_links_data.extend(
                {"build_id": self.id, "url": link, "media_type": "world_download"}
                for link in data.get("world_download_urls", [])
            )
        if build_links_data:
            await DatabaseManager().table("build_links").upsert(build_links_data).execute()

    async def _update_build_creators_table(self, data: dict[str, Any]) -> None:
        """Updates the build_creators table with the given data."""
        db = DatabaseManager()
        creator_ids = []
        for creator_ign in data.get("creators_ign", []):
            response = await db.table("users").select("id").eq("ign", creator_ign).maybe_single().execute()
            if response:
                creator_ids.append(response.data["id"])
            else:
                creator_id = add_user(ign=creator_ign)
                creator_ids.append(creator_id)

        build_creators_data = [{"build_id": self.id, "user_id": user_id} for user_id in creator_ids]
        if build_creators_data:
            await DatabaseManager().table("build_creators").upsert(build_creators_data).execute()

    async def _update_build_versions_table(self, data: dict[str, Any]) -> None:
        """Updates the build_versions table with the given data."""
        functional_versions = data.get("functional_versions", await DatabaseManager.get_newest_version(edition="Java"))

        # TODO: raise an error if any versions are not found in the database
        db = DatabaseManager()
        response = (
            await db.rpc("get_quantified_version_names", {}).in_("quantified_name", functional_versions).execute()
        )
        response = cast(APIResponse[QuantifiedVersionRecord], response)
        version_ids = [version["id"] for version in response.data]
        build_versions_data = list({"build_id": self.id, "version_id": version_id} for version_id in version_ids)
        if build_versions_data:
            await db.table("build_versions").upsert(build_versions_data).execute()

    def update_local(self, data: dict[Any, Any]) -> None:
        """Updates the build locally with the given data. No validation is done on the data."""
        # FIXME: this does not work with nested data like self.information
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def as_dict(self) -> dict[str, Any]:
        """Converts the build to a dictionary."""
        build = {}
        for attr in self:
            build[attr] = getattr(self, attr)
        return build

    async def confirm(self) -> None:
        """Marks the build as confirmed.

        Raises:
            ValueError: If the build could not be confirmed.
        """
        self.submission_status = Status.CONFIRMED
        db = DatabaseManager()
        response: APIResponse[BuildRecord] = (
            await db.table("builds")
            .update({"submission_status": Status.CONFIRMED}, count=CountMethod.exact)
            .eq("id", self.id)
            .execute()
        )
        if response.count != 1:
            raise ValueError("Failed to confirm submission in the database.")

    async def deny(self) -> None:
        """Marks the build as denied.

        Raises:
            ValueError: If the build could not be denied.
        """
        self.submission_status = Status.DENIED
        db = DatabaseManager()
        response: APIResponse[BuildRecord] = (
            await db.table("builds")
            .update({"submission_status": Status.DENIED}, count=CountMethod.exact)
            .eq("id", self.id)
            .execute()
        )
        if response.count != 1:
            raise ValueError("Failed to deny submission in the database.")

    async def generate_embed(self) -> discord.Embed:
        """Generates an embed for the build."""
        em = utils.info_embed(title=self.get_title(), description=await self.get_description())

        fields = await self.get_metadata_fields()
        for key, val in fields.items():
            em.add_field(name=key, value=escape_markdown(val), inline=True)

        if self.image_urls:
            em.set_image(url=self.image_urls[0])

        em.set_footer(text=f"Submission ID: {self.id}.")
        return em

    def get_title(self) -> str:
        """Generates the official Redstone Squid defined title for the build."""
        title = ""

        if self.category != "Door":
            raise NotImplementedError("Only doors are supported for now.")

        if self.submission_status == Status.PENDING:
            title += "Pending: "
        if self.record_category:
            title += f"{self.record_category} "

        # Door dimensions
        if self.door_width and self.door_height and self.door_depth:
            title += f"{self.door_width}x{self.door_height}x{self.door_depth} "
        elif self.door_width and self.door_height:
            title += f"{self.door_width}x{self.door_height} "
        elif self.door_width:
            title += f"{self.door_width} Wide "
        elif self.door_height:
            title += f"{self.door_height} High "

        # Wiring Placement Restrictions
        for restriction in self.wiring_placement_restrictions:
            title += f"{restriction} "

        # Pattern
        if self.door_type is not None:
            for pattern in self.door_type:
                if pattern != "Regular":
                    title += f"{pattern} "

        # Door type
        if self.door_orientation_type is None:
            raise ValueError("Door orientation type information (i.e. Door/Trapdoor/Skydoor) is missing.")
        title += self.door_orientation_type

        return title

    async def get_description(self) -> str | None:
        """Generates a description for the build, which includes component restrictions, version compatibility, and other information."""
        desc = []

        if self.component_restrictions and self.component_restrictions[0] != "None":
            desc.append(", ".join(self.component_restrictions))

        if self.functional_versions is None:
            desc.append("Unknown version compatibility.")
        elif (
            get_version_string(await DatabaseManager.get_newest_version(edition="Java")) not in self.functional_versions
        ):
            desc.append("**Broken** in current (Java) version.")

        if "Locational" in self.miscellaneous_restrictions:
            desc.append("**Locational**.")
        elif "Locational with fixes" in self.miscellaneous_restrictions:
            desc.append("**Locational** with known fixes for each location.")

        if "Directional" in self.miscellaneous_restrictions:
            desc.append("**Directional**.")
        elif "Directional with fixes" in self.miscellaneous_restrictions:
            desc.append("**Directional** with known fixes for each direction.")

        if self.information and (user_message := self.information.get("user")):
            desc.append("\n" + escape_markdown(user_message))

        return "\n".join(desc) if desc else None

    async def get_versions_string(self) -> str:
        """Returns a string representation of the versions the build is functional in.

        The versions are formatted as a range if they are consecutive. For example, "1.16 - 1.17, 1.19".
        """
        if not self.functional_versions:
            return ""

        versions: list[str] = []

        linking = False
        """Whether the current version is part of a range. This is used to render consecutive versions as a range (e.g. 1.16.2-1.18)."""
        start_version: VersionRecord | None = None
        end_version: VersionRecord | None = None

        for version in await DatabaseManager.get_versions_list(edition="Java"):
            if get_version_string(version, no_edition=True) in self.functional_versions:
                if not linking:
                    linking = True
                    start_version = version
                end_version = version

            elif linking:  # Current looped version is not functional, but the previous one was
                assert start_version is not None
                assert end_version is not None
                versions.append(
                    get_version_string(start_version)
                    if start_version == end_version
                    else f"{start_version} - {end_version}"
                )
                linking = False

        if linking:  # If the last version is functional
            assert start_version is not None
            assert end_version is not None
            versions.append(
                get_version_string(start_version)
                if start_version == end_version
                else f"{start_version} - {end_version}"
            )

        return ", ".join(versions)

    async def get_metadata_fields(self) -> dict[str, str]:
        """Returns a dictionary of metadata fields for the build.

        The fields are formatted as key-value pairs, where the key is the field name and the value is the field value. The values are not escaped."""
        fields = {"Dimensions": f"{self.width or '?'} x {self.height or '?'} x {self.depth or '?'}"}

        if self.normal_opening_time:
            fields["Opening Time"] = str(self.normal_opening_time)

        if self.normal_closing_time:
            fields["Opening Time"] = str(self.normal_closing_time)

        if self.width and self.height and self.depth:
            fields["Volume"] = str(self.width * self.height * self.depth)

        if self.visible_opening_time and self.visible_closing_time:
            # The times are stored as game ticks, so they need to be divided by 20 to get seconds
            fields["Visible Opening Time"] = str(self.visible_opening_time / 20)
            fields["Visible Closing Time"] = str(self.visible_closing_time / 20)

        if self.creators_ign:
            fields["Creators"] = ", ".join(sorted(self.creators_ign))

        if self.completion_time:
            fields["Date Of Completion"] = str(self.completion_time)

        fields["Versions"] = await self.get_versions_string()

        if self.server_ip:
            fields["Server"] = self.server_ip

            if self.coordinates:
                fields["Coordinates"] = self.coordinates

            if self.command:
                fields["Command"] = self.command

        if self.world_download_urls:
            fields["World Download"] = str(self.world_download_urls)
        if self.video_urls:
            fields["Video"] = str(self.video_urls)

        return fields


async def get_all_builds(submission_status: Status | None = None) -> list[Build]:
    """Fetches all builds from the database, optionally filtered by submission status.

    Args:
        submission_status: The status of the submissions to filter by. If None, all submissions are returned. See Build class for possible values.

    Returns:
        A list of Build objects.
    """
    db = DatabaseManager()
    query = db.table("builds").select(all_build_columns)

    if submission_status:
        query = query.eq("submission_status", submission_status.value)

    response = await query.execute()
    if not response:
        return []
    else:
        return [Build.from_json(build_json) for build_json in response.data]


async def get_builds(build_ids: list[int]) -> list[Build | None]:
    """Fetches builds from the database with the given IDs."""
    if len(build_ids) == 0:
        return []

    db = DatabaseManager()
    response = await db.table("builds").select(all_build_columns).in_("id", build_ids).execute()

    builds: list[Build | None] = [None] * len(build_ids)
    for build_json in response.data:
        idx = build_ids.index(build_json["id"])
        builds[idx] = Build.from_json(build_json)
    return builds


async def get_unsent_builds(server_id: int) -> list[Build] | None:
    """Get all the builds that have not been posted on the server"""
    db = DatabaseManager()

    # Builds that have not been posted on the server
    response = await db.rpc("get_unsent_builds", {"server_id_input": server_id}).execute()
    server_unsent_builds = response.data
    return [Build.from_json(unsent_sub) for unsent_sub in server_unsent_builds]


async def main():
    print(Build(id=1, submission_status=Status.PENDING).diff(Build(id=1, submission_status=Status.CONFIRMED)))


if __name__ == "__main__":
    asyncio.run(main())
