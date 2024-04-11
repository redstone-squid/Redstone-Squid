"""Submitting and retrieving submissions to/from the database"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Optional, Literal

import discord
from postgrest.types import CountMethod

import Discord.config
from Database._types import BuildRecord, DoorRecord
from Database.database import DatabaseManager, all_build_columns
from Database.utils import MISSING, Missing, drop_missing
from Database.enums import Status
from Discord import utils
from common import utcnow


class Build:
    """A class representing a submission to the database. This class is used to store and manipulate submissions."""

    def __init__(self):
        """Initializes an empty build.

         This should not be used externally. Use `from_dict()` or `from_id()` instead."""
        self.id: int | MISSING = Missing
        self.submission_status: int | MISSING = Missing
        self.category: Optional[Literal["Door", "Extender", "Utility", "Entrance"]] | MISSING = Missing
        self.record_category: Optional[Literal["Smallest", "Fastest", "First"]] | MISSING = Missing
        self.functional_versions: Optional[list[str]] | MISSING = Missing

        self.width: Optional[int] | MISSING = Missing
        self.height: Optional[int] | MISSING = Missing
        self.depth: Optional[int] | MISSING = Missing

        self.door_width: int | MISSING = Missing
        self.door_height: int | MISSING = Missing
        self.door_depth: int | MISSING = Missing

        self.door_type: Optional[list[str]] | MISSING = Missing
        self.door_orientation_type: Literal["Door", "Trapdoor", "Skydoor"] | MISSING = Missing

        self.wiring_placement_restrictions: Optional[list[str]] | MISSING = Missing
        self.component_restrictions: Optional[list[str]] | MISSING = Missing
        self.miscellaneous_restrictions: Optional[list[str]] | MISSING = Missing

        self.normal_closing_time: Optional[int] | MISSING = Missing
        self.normal_opening_time: Optional[int] | MISSING = Missing
        self.visible_closing_time: Optional[int] | MISSING = Missing
        self.visible_opening_time: Optional[int] | MISSING = Missing

        # In the database, we force empty information to be {}
        self.information: dict | MISSING = Missing
        self.creators_ign: Optional[str] | MISSING = Missing

        self.image_url: Optional[list[str]] | MISSING = Missing
        self.video_url: Optional[list[str]] | MISSING = Missing
        self.world_download_url: Optional[list[str]] | MISSING = Missing

        self.server_ip: Optional[str] | MISSING = Missing
        self.coordinates: Optional[str] | MISSING = Missing
        self.command: Optional[str] | MISSING = Missing

        self.submitter_id: int | MISSING = Missing
        self.completion_time: Optional[str] | MISSING = Missing
        self.edited_time: datetime | MISSING = Missing

    def __iter__(self):
        """Iterates over the *attributes* of the Build object."""
        for attr in [a for a in dir(self) if not a.startswith('__') and not callable(getattr(self, a))]:
            yield attr

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
    def from_json(data: dict) -> Build:
        """
        Converts a JSON object to a Build object.

        Args:
            data: the exact JSON object returned by
                `DatabaseManager().table('builds').select(all_build_columns).eq('id', build_id).execute().data[0]`

        Returns:
            A Build object.
        """
        build = Build()
        build.id = data['id']
        build.submission_status = data['submission_status']
        build.record_category = data['record_category']
        build.category = data['category']

        build.width = data['width']
        build.height = data['height']
        build.depth = data['depth']

        match data['category']:
            case 'Door':
                category_data = data['doors']
            case 'Extender':
                category_data = data['extenders']
            case 'Utility':
                category_data = data['utilities']
            case 'Entrance':
                category_data = data['entrances']

        # FIXME: This is hardcoded for now
        types = data.get('types', [])
        build.door_type = [type_['name'] for type_ in types]

        build.door_orientation_type = data['doors']['orientation']
        build.door_width = data['doors']['door_width']
        build.door_height = data['doors']['door_height']
        build.normal_closing_time = data['doors']['normal_closing_time']
        build.normal_opening_time = data['doors']['normal_opening_time']
        build.visible_closing_time = data['doors']['visible_closing_time']
        build.visible_opening_time = data['doors']['visible_opening_time']

        restrictions = data.get('restrictions', [])
        build.wiring_placement_restrictions = [r['name'] for r in restrictions if r['type'] == 'wiring-placement']
        build.component_restrictions = [r['name'] for r in restrictions if r['type'] == 'component']
        build.miscellaneous_restrictions = [r['name'] for r in restrictions if r['type'] == 'miscellaneous']

        build.information = json.loads(data['information'])

        creators: list[dict] = data.get('build_creators', [])
        build.creators_ign = [creator['creator_ign'] for creator in creators]

        versions: list[dict] = data.get('functional_versions', [])
        build.functional_versions = [version['full_name_temp'] for version in versions]

        links: list[dict] = data.get('build_links', [])
        build.image_url = [link['url'] for link in links if link['media_type'] == 'image']
        build.video_url = [link['url'] for link in links if link['media_type'] == 'video']
        build.world_download_url = [link['url'] for link in links if link['media_type'] == 'world-download']

        server_info: dict = data['server_info']
        if server_info:
            build.server_ip = server_info.get('server_ip')
            build.coordinates = server_info.get('coordinates')
            build.command = server_info.get('command_to_build')

        build.submitter_id = data['submitter_id']
        build.completion_time = data['completion_time']
        build.last_updated = datetime.strptime(data.get("edited_time"), '%Y-%m-%dT%H:%M:%S')

        return build

    @staticmethod
    def from_dict(submission: dict) -> Build:
        """Creates a new Build object from a dictionary. No validation is done on the data."""
        build = Build()
        for attr in build:
            if attr in submission:
                setattr(build, attr, submission[attr])

        return build

    async def load(self) -> Build:
        """
        Loads the build from the database. All previous data is overwritten.

        Returns:
            The Build object.

        Raises:
            ValueError: If the build was not found.
        """
        if self.id is Missing:
            raise ValueError("Build ID is missing.")

        db = await DatabaseManager()
        response = await db.table('builds').select(all_build_columns).eq('id', self.id).maybe_single().execute()
        if not response:
            raise ValueError("Build not found in the database.")
        return Build.from_json(response.data)

    async def save(self) -> None:
        """
        Updates the build in the database with the given data.

        If the build does not exist in the database, it will be inserted instead.
        """
        self.edited_time = utcnow()
        data = {key: drop_missing(value) for key, value in self.as_dict().items()}
        db = await DatabaseManager()

        build_data = {key: data[key] for key in BuildRecord.__annotations__.keys() if key in data}
        # information is a special JSON field in the database that stores various information about the build
        # this needs to be kept because it will be updated later
        information = {}
        if build_data.get("information"):
            information = {"user": build_data['information']}
            build_data['information'] = json.dumps(information)

        if self.id:
            response = await db.table('builds').update(build_data, count=CountMethod.exact).eq('id', self.id).execute()
            if response.count != 1:
                raise ValueError("Failed to update submission in the database.")
        else:
            response = await db.table('builds').insert(build_data, count=CountMethod.exact).execute()
            if response.count != 1:
                raise ValueError("Failed to insert submission in the database.")
            build_id = response.data[0]['id']
            self.id = build_id

        # doors table
        if data.get("category") == "Door":
            doors_data = {key: data[key] for key in DoorRecord.__annotations__.keys() if key in data}
            # FIXME
            doors_data['orientation'] = data['door_orientation_type']
            doors_data['build_id'] = self.id
            await db.table('doors').upsert(doors_data).execute()

        if data.get("category") == "Extender":
            raise NotImplementedError
        if data.get("category") == "Utility":
            raise NotImplementedError
        if data.get("category") == "Entrance":
            raise NotImplementedError

        # build_restrictions table
        build_restrictions = data.get("wiring_placement_restrictions", []) + data.get("component_restrictions", []) + data.get("miscellaneous_restrictions", [])
        response = await db.table('restrictions').select('*').in_('name', build_restrictions).execute()
        restriction_ids = [restriction['id'] for restriction in response.data]
        build_restrictions_data = list({'build_id': self.id, 'restriction_id': restriction_id} for restriction_id in restriction_ids)
        await db.table('build_restrictions').upsert(build_restrictions_data).execute()

        unknown_restrictions = {}
        unknown_wiring_restrictions = []
        unknown_component_restrictions = []
        for wiring_restriction in data.get("wiring_placement_restrictions", []):
            if wiring_restriction not in [restriction['name'] for restriction in response.data if restriction['type'] == 'wiring-placement']:
                unknown_wiring_restrictions.append(wiring_restriction)
        for component_restriction in data.get("component_restrictions", []):
            if component_restriction not in [restriction['name'] for restriction in response.data if restriction['type'] == 'component']:
                unknown_component_restrictions.append(component_restriction)
        if unknown_wiring_restrictions:
            unknown_restrictions["wiring_placement_restrictions"] = unknown_wiring_restrictions
        if unknown_component_restrictions:
            unknown_restrictions["component_restrictions"] = unknown_component_restrictions
        if unknown_restrictions:
            information["unknown_restrictions"] = unknown_restrictions
            await db.table('builds').update({'information': json.dumps(information)}).eq('id', self.id).execute()

        # build_types table
        response = await db.table('types').select('*').eq('build_category', data.get("category")).in_('name', data.get("door_type", [])).execute()
        type_ids = [type_['id'] for type_ in response.data]
        build_types_data = list({'build_id': self.id, 'type_id': type_id} for type_id in type_ids)
        await db.table('build_types').upsert(build_types_data).execute()

        unknown_types = []
        for door_type in data.get("door_type", []):
            if door_type not in [type_['name'] for type_ in response.data]:
                unknown_types.append(door_type)
        if unknown_types:
            information["unknown_types"] = unknown_types
            await db.table('builds').update({'information': json.dumps(information)}).eq('id', self.id).execute()

        # build_links table
        build_links_data = []
        if data.get('image_url'):
            build_links_data.extend(
                {'build_id': self.id, 'url': link, 'media_type': 'image'} for link in data.get("image_url", []))
        if data.get('video_url'):
            build_links_data.extend(
                {'build_id': self.id, 'url': link, 'media_type': 'video'} for link in data.get("video_url", []))
        if data.get('world_download_url'):
            build_links_data.extend({'build_id': self.id, 'url': link, 'media_type': 'world_download'} for link in data.get("world_download_url", []))
        if build_links_data:
            await db.table('build_links').upsert(build_links_data).execute()

        # build_creators table
        build_creators_data = list({'build_id': self.id, 'creator_ign': creator} for creator in data.get("creators_ign", []))
        await db.table('build_creators').upsert(build_creators_data).execute()

        # build_versions table
        response = await db.table('versions').select('*').in_('full_name_temp', data.get("functional_versions", [])).execute()
        version_ids = [version['id'] for version in response.data]
        build_versions_data = list({'build_id': self.id, 'version_id': version_id} for version_id in version_ids)
        await db.table('build_versions').upsert(build_versions_data).execute()

    def update_local(self, data: dict) -> None:
        """Updates the build locally with the given data. No validation is done on the data."""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def as_dict(self) -> dict:
        """Converts the build to a dictionary."""
        build = {}
        for attr in self:
            if getattr(self, attr) is not Missing:
                build[attr] = getattr(self, attr)
        return build

    async def confirm(self) -> None:
        """Marks the build as confirmed.

        Raises:
            ValueError: If the build could not be confirmed.
        """
        self.submission_status = Status.CONFIRMED
        db = await DatabaseManager()
        response = await db.table('builds').update({'submission_status': Status.CONFIRMED}, count=CountMethod.exact).eq('id', self.id).execute()
        if response.count != 1:
            raise ValueError("Failed to confirm submission in the database.")

    async def deny(self) -> None:
        """Marks the build as denied.

        Raises:
            ValueError: If the build could not be denied.
        """
        self.submission_status = Status.DENIED
        db = await DatabaseManager()
        response = await db.table('builds').update({'submission_status': Status.DENIED}, count=CountMethod.exact).eq('id', self.id).execute()
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
            em.set_image(url=self.image_url[0])

        em.set_footer(text=f'Submission ID: {self.id}.')

        return em

    def get_title(self) -> str:
        title = "Pending: " if self.submission_status == Status.PENDING else ""

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
        if self.wiring_placement_restrictions is not None:
            for restriction in self.wiring_placement_restrictions:
                if restriction != "None":
                    title += f"{restriction} "

        # Pattern
        # FIXME: list index out of range due to not adding Regular to the list when Build.save()
        # if self.door_type[0] != "Regular":
        for pattern in self.door_type:
            title += f"{pattern} "

        # Door type
        title += self.door_orientation_type

        return title

    def get_description(self) -> str | None:
        description = []

        # Component Restrictions
        if self.component_restrictions and self.component_restrictions[0] != "None":
            description.append(", ".join(self.component_restrictions))

        if self.functional_versions is Missing:
            description.append("Unknown version compatibility.")
        elif not Discord.config.VERSIONS_LIST[-1] in self.functional_versions:
            description.append("**Broken** in current version.")

        if "Locational" in self.miscellaneous_restrictions:
            description.append("**Locational**.")
        elif "Locational with fixes" in self.miscellaneous_restrictions:
            description.append("**Locational** with known fixes for each location.")

        if "Directional" in self.miscellaneous_restrictions:
            description.append("**Directional**.")
        elif "Directional with fixes" in self.miscellaneous_restrictions:
            description.append("**Directional** with known fixes for each direction.")

        if self.information and self.information.get("user"):
            description.append("\n" + self.information.get("user"))

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
            if version in self.functional_versions:

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

        fields["Creators"] = ', '.join(sorted(self.creators_ign))
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

    # TODO: Find a use or remove this method
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
        if self.wiring_placement_restrictions:
            string += f"Wiring Placement Restrictions: {', '.join(self.wiring_placement_restrictions)}\n"
        if self.component_restrictions:
            string += f"Component Restrictions: {', '.join(self.component_restrictions)}\n"
        if self.miscellaneous_restrictions:
            string += f"Miscellaneous Restrictions: {', '.join(self.miscellaneous_restrictions)}\n"
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
        string += f"Creators: {', '.join(self.creators_ign)}\n"
        string += f"Versions: {', '.join(self.functional_versions)}\n"
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


async def get_all_builds(submission_status: Optional[int] = None) -> list[Build]:
    """Fetches all builds from the database, optionally filtered by submission status.

    Args:
        submission_status: The status of the submissions to filter by. If None, all submissions are returned. See Build class for possible values.

    Returns:
        A list of Build objects.
    """
    db = await DatabaseManager()
    query = db.table('builds').select(all_build_columns)

    if submission_status:
        query = query.eq('submission_status', submission_status)

    response = await query.execute()
    if not response:
        return []
    else:
        return [Build.from_json(build_json) for build_json in response.data]


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


async def get_unsent_builds(server_id: int) -> list[Build] | None:
    """Get all the builds that have not been posted on the server"""
    db = await DatabaseManager()

    # Builds that have not been posted on the server
    response = await db.rpc('get_unsent_builds', {'server_id_input': server_id}).execute()
    server_unsent_builds = response.data
    return [Build.from_json(unsent_sub) for unsent_sub in server_unsent_builds]


async def main():
    pass

if __name__ == '__main__':
    asyncio.run(main())
