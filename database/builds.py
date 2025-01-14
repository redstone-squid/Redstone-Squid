"""Submitting and retrieving submissions to/from the database"""

from __future__ import annotations

import io
import mimetypes
import re
import os
import asyncio
import logging
from asyncio import Task
from functools import cached_property
from dataclasses import dataclass, field, fields
from collections.abc import Sequence, Mapping
from typing import Callable, Final, Generic, Literal, Any, cast, TypeVar, ParamSpec

import discord
from discord.ext.commands import Bot
from discord.utils import escape_markdown
from openai import AsyncOpenAI, OpenAIError
from postgrest.base_request_builder import APIResponse, SingleAPIResponse
from postgrest.types import CountMethod
import vecs

from bot._types import GuildMessageable
from bot.submission.parse import validate_restrictions, validate_door_types, parse_time_string
from bot import utils as bot_utils
from database.schema import (
    BuildRecord,
    MessageRecord,
    ServerInfo,
    TypeRecord,
    RestrictionRecord,
    Info,
    VersionRecord,
    UnknownRestrictions,
    RecordCategory,
    DoorOrientationName,
    QuantifiedVersionRecord,
)
from database import DatabaseManager, message as msg
from database.server_settings import get_server_setting
from database.user import add_user
from database.utils import utcnow, get_version_string, upload_to_catbox
from database.enums import Status, Category


logger = logging.getLogger(__name__)

T = TypeVar("T")
P = ParamSpec("P")

all_build_columns = "*, versions(*), build_links(*), build_creators(*), users(*), types(*), restrictions(*), doors(*), extenders(*), utilities(*), entrances(*), messages!builds_original_message_id_fkey(*)"
"""All columns that needs to be joined in the build table to get all the information about a build."""

background_tasks: set[Task[Any]] = set()


class FrozenField(Generic[T]):
    """A descriptor that makes an attribute immutable after it has been set."""

    __slots__ = ("private_name",)

    def __init__(self, name: str) -> None:
        self.private_name = "_" + name

    def __get__(self, instance: object | None, owner: type[object] | None = None) -> T:
        value = getattr(instance, self.private_name)
        return value

    def __set__(self, instance: object, value: T) -> None:
        if hasattr(instance, self.private_name):
            msg = f"Attribute `{self.private_name[1:]}` is immutable!"
            raise TypeError(msg) from None

        setattr(instance, self.private_name, value)


# https://stackoverflow.com/questions/74714300/paramspec-for-a-pre-defined-function-without-using-generic-callablep
def signature_from(_original: Callable[P, T]) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Copies the signature of a function to another function."""

    def _decorator(func: Callable[P, T]) -> Callable[P, T]:
        return func

    return _decorator


@signature_from(field)
def frozen_field(**kwargs: Any):
    """A field that is immutable after it has been set. See `dataclasses.field` for more information."""
    metadata = kwargs.pop("metadata", {}) | {"frozen": True}
    return field(**kwargs, metadata=metadata)


def freeze_fields(cls: type[T]) -> type[T]:
    """
    A decorator that makes fields of a dataclass immutable, if they have the `frozen` metadata set to True.

    This is done by replacing the fields with FrozenField descriptors.

    Args:
        cls: The class to make immutable, must be a dataclass.

    Raises:
        TypeError: If cls is not a dataclass
    """

    cls_fields = getattr(cls, "__dataclass_fields__", None)
    if cls_fields is None:
        raise TypeError(f"{cls} is not a dataclass")

    params = getattr(cls, "__dataclass_params__")
    # _DataclassParams(init=True,repr=True,eq=True,order=True,unsafe_hash=False,
    #                   frozen=True,match_args=True,kw_only=False,slots=False,
    #                   weakref_slot=False)
    if params.frozen:
        return cls

    for f in fields(cls):  # type: ignore
        if "frozen" in f.metadata:
            setattr(cls, f.name, FrozenField(f.name))
    return cls


@freeze_fields
@dataclass
class Build:
    """A submission to the database.

    This is a very large class, the methods are ordered as follows:
    - Static constructors
    - Magic (dunder) methods
    - Properties
    - Normal methods
    - load(), save() and the helper methods it calls
    - generate_embed() and the methods it calls
    """

    id: int | None = None
    submission_status: Status | None = None
    category: Category | None = None
    record_category: RecordCategory | None = None
    versions: list[str] = field(default_factory=list)
    version_spec: str | None = None

    width: int | None = None
    height: int | None = None
    depth: int | None = None

    door_width: int | None = None
    door_height: int | None = None
    door_depth: int | None = None

    door_type: list[str] = field(default_factory=list)
    door_orientation_type: DoorOrientationName | None = None

    wiring_placement_restrictions: list[str] = field(default_factory=list)
    component_restrictions: list[str] = field(default_factory=list)
    miscellaneous_restrictions: list[str] = field(default_factory=list)

    normal_closing_time: int | None = None
    normal_opening_time: int | None = None
    visible_closing_time: int | None = None
    visible_opening_time: int | None = None

    information: Info = field(default_factory=dict)  # type: ignore
    creators_ign: list[str] = field(default_factory=list)

    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    world_download_urls: list[str] = field(default_factory=list)
    server_info: ServerInfo = field(default_factory=dict)  # type: ignore

    submitter_id: int | None = None
    # TODO: save the submitted time too
    completion_time: str | None = None
    edited_time: str | None = None

    original_server_id: Final[int | None] = frozen_field(default=None)
    original_channel_id: Final[int | None] = frozen_field(default=None)
    original_message_id: Final[int | None] = frozen_field(default=None)
    original_message: Final[str | None] = frozen_field(default=None)
    _original_message_obj: discord.Message | None = field(default=None, init=False, repr=False)
    """Cache for the original message of the build."""

    ai_generated: bool | None = None
    embedding: list[float] | None = field(default=None, repr=False)

    @staticmethod
    async def from_id(build_id: int) -> Build | None:
        """Creates a new Build object from a database ID.

        Args:
            build_id: The ID of the build to retrieve.

        Returns:
            The Build object with the specified ID, or None if the build was not found.
        """
        db = DatabaseManager()
        response = await db.table("builds").select(all_build_columns).eq("id", build_id).maybe_single().execute()
        if not response:
            return None
        return Build.from_json(response.data)

    @staticmethod
    async def from_message_id(message_id: int) -> Build | None:
        """
        Get the build by a message id.

        Args:
            message_id: The message id to get the build from.

        Returns:
            The Build object with the specified message id, or None if the build was not found.
        """
        db = DatabaseManager()
        response: SingleAPIResponse[MessageRecord] | None = (
            await db.table("messages")
            .select("build_id", count=CountMethod.exact)
            .eq("message_id", message_id)
            .maybe_single()
            .execute()
        )
        if response and response.data["build_id"]:
            return await Build.from_id(response.data["build_id"])
        return None

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
        id = data["id"]
        submission_status = data["submission_status"]
        record_category = data["record_category"]
        category = data["category"]

        width = data["width"]
        height = data["height"]
        depth = data["depth"]

        match data["category"]:
            case "Door":
                assert data["doors"] is not None
                door_orientation_type = data["doors"]["orientation"]
                door_width = data["doors"]["door_width"]
                door_height = data["doors"]["door_height"]
                door_depth = data["doors"]["door_depth"]
                normal_closing_time = data["doors"]["normal_closing_time"]
                normal_opening_time = data["doors"]["normal_opening_time"]
                visible_closing_time = data["doors"]["visible_closing_time"]
                visible_opening_time = data["doors"]["visible_opening_time"]
            case "Extender":
                category_data = data["extenders"]
                raise NotImplementedError
            case "Utility":
                category_data = data["utilities"]
                raise NotImplementedError
            case "Entrance":
                category_data = data["entrances"]
                raise NotImplementedError
            case _:
                raise ValueError("Invalid category")

        # FIXME: This is hardcoded for now
        if data.get("types"):
            types = data["types"]
            door_type = [type_["name"] for type_ in types]
        else:
            door_type = ["Regular"]

        restrictions: list[RestrictionRecord] = data.get("restrictions", [])
        wiring_placement_restrictions = [r["name"] for r in restrictions if r["type"] == "wiring-placement"]
        component_restrictions = [r["name"] for r in restrictions if r["type"] == "component"]
        miscellaneous_restrictions = [r["name"] for r in restrictions if r["type"] == "miscellaneous"]

        information = data["information"]

        creators: list[dict[str, Any]] = data.get("users", [])
        creators_ign = [creator["ign"] for creator in creators]

        version_spec = data["version_spec"]
        version_records: list[VersionRecord] = data.get("versions", [])
        versions = [get_version_string(v) for v in version_records]

        links: list[dict[str, Any]] = data.get("build_links", [])
        image_urls = [link["url"] for link in links if link["media_type"] == "image"]
        video_urls = [link["url"] for link in links if link["media_type"] == "video"]
        world_download_urls = [link["url"] for link in links if link["media_type"] == "world-download"]

        server_info: ServerInfo = data["server_info"] or {}

        submitter_id = data["submitter_id"]
        completion_time = data["completion_time"]
        edited_time = data["edited_time"]

        message_record: MessageRecord = data["messages"] or {}  # type: ignore
        original_server_id = message_record.get("server_id")
        original_channel_id = message_record.get("channel_id")
        original_message_id = data["original_message_id"]
        original_message = message_record.get("content")

        ai_generated = data["ai_generated"]
        embedding = data["embedding"]

        return Build(
            id=id,
            submission_status=submission_status,
            record_category=record_category,
            category=category,
            versions=versions,
            version_spec=version_spec,
            width=width,
            height=height,
            depth=depth,
            door_width=door_width,
            door_height=door_height,
            door_depth=door_depth,
            door_type=door_type,
            door_orientation_type=door_orientation_type,
            wiring_placement_restrictions=wiring_placement_restrictions,
            component_restrictions=component_restrictions,
            miscellaneous_restrictions=miscellaneous_restrictions,
            normal_closing_time=normal_closing_time,
            normal_opening_time=normal_opening_time,
            visible_closing_time=visible_closing_time,
            visible_opening_time=visible_opening_time,
            information=information,
            creators_ign=creators_ign,
            image_urls=image_urls,
            video_urls=video_urls,
            world_download_urls=world_download_urls,
            server_info=server_info,
            submitter_id=submitter_id,
            completion_time=completion_time,
            edited_time=edited_time,
            original_server_id=original_server_id,
            original_channel_id=original_channel_id,
            original_message_id=original_message_id,
            original_message=original_message,
            ai_generated=ai_generated,
            embedding=embedding,
        )

    @staticmethod
    async def ai_generate_from_message(message: discord.Message) -> Build | None:
        """Parses a build from a message using AI."""
        client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        current_dir = os.path.dirname(os.path.abspath(__file__))
        with open(f"{current_dir}/prompt.txt", "r", encoding="utf-8") as f:
            prompt = f.read()
        completion = await client.beta.chat.completions.parse(
            model="deepseek/deepseek-chat",
            messages=[
                {
                    "role": "user",
                    "content": prompt.format(
                        message=f"{message.author.display_name} wrote the following message:\n{message.clean_content}"
                    ),
                },
            ],
        )
        output = completion.choices[0].message.content

        logger.debug("AI Output: %s", output)

        if output is None:
            return None

        # Step 1: Extract content between <target> and </target>
        match = re.search(r"<target>(.*?)</target>", output, re.DOTALL)
        if not match:
            return None

        content = match.group(1).strip()

        # Step 2: Split content into lines and parse key-value pairs
        variables: dict[str, str | None] = {}
        for line in content.split("\n"):
            # Skip empty lines
            if not line.strip():
                continue
            # Split only on the first ':'
            if ":" not in line:
                print(f"Skipping malformed line: {line}")
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value.lower() in ["none", "null", "unknown"]:
                value = None

            variables[key] = value

        # Step 3: Validate and convert variables
        acceptable_keys = [
            "record_category",
            "component_restriction",
            "wiring_placement_restrictions",
            "miscellaneous_restrictions",
            "piston_door_type",
            "door_orientation",
            "door_width",
            "door_height",
            "door_depth",
            "build_width",
            "build_height",
            "build_depth",
            "opening_time",
            "closing_time",
            "creators",
            "version",
            "image",
            "author_note",
        ]

        # All keys must be present
        if not all(key in variables for key in acceptable_keys):
            logging.debug("Missing keys in AI output variables")
            return

        build = Build(
            original_server_id=message.guild.id if message.guild is not None else None,
            original_channel_id=message.channel.id,
            original_message_id=message.id,
            original_message=message.clean_content,
            ai_generated=True,
        )
        build.record_category = variables["record_category"]  # type: ignore
        build.information["unknown_restrictions"] = {}
        if variables["component_restriction"] is not None:
            comps = await validate_restrictions(variables["component_restriction"].split(", "), "component")
            build.component_restrictions = comps[0]
            build.information["unknown_restrictions"]["component_restrictions"] = comps[1]
        if variables["wiring_placement_restrictions"] is not None:
            wirings = await validate_restrictions(
                variables["wiring_placement_restrictions"].split(", "), "wiring-placement"
            )
            build.wiring_placement_restrictions = wirings[0]
            build.information["unknown_restrictions"]["wiring_placement_restrictions"] = wirings[1]
        if variables["miscellaneous_restrictions"] is not None:
            miscs = await validate_restrictions(variables["miscellaneous_restrictions"].split(", "), "miscellaneous")
            build.miscellaneous_restrictions = miscs[0]
            build.information["unknown_restrictions"]["miscellaneous_restrictions"] = miscs[1]
        if variables["piston_door_type"] is not None:
            door_types = await validate_door_types(variables["piston_door_type"].split(", "))
            build.door_type = door_types[0]
            build.information["unknown_patterns"] = door_types[1]
        orientation = variables["door_orientation"]
        if orientation == "Normal":
            build.door_orientation_type = "Door"
        else:
            build.door_orientation_type = orientation or "Door"  # type: ignore
        build.door_width = int(variables["door_width"]) if variables["door_width"] else None
        build.door_height = int(variables["door_height"]) if variables["door_height"] else None
        build.door_depth = int(variables["door_depth"]) if variables["door_depth"] else None
        build.width = int(variables["build_width"]) if variables["build_width"] else None
        build.height = int(variables["build_height"]) if variables["build_height"] else None
        build.depth = int(variables["build_depth"]) if variables["build_depth"] else None
        build.normal_opening_time = parse_time_string(variables["opening_time"])
        build.normal_closing_time = parse_time_string(variables["closing_time"])
        build.creators_ign = variables["creators"].split(", ") if variables["creators"] else []
        build.version_spec = variables["version"] or DatabaseManager.get_newest_version(edition="Java")
        build.versions = DatabaseManager.find_versions_from_spec(build.version_spec)
        build.image_urls = variables["image"].split(", ") if variables["image"] else []
        if variables["author_note"] is not None:
            build.information["user"] = variables["author_note"].replace("\\n", "\n")
        return build

    def __iter__(self):
        """Iterates over the *attributes* of the Build object."""
        for attr in [a for a in dir(self) if not a.startswith("__") and not callable(getattr(self, a))]:
            yield attr

    @cached_property
    def original_link(self) -> str | None:
        """The link to the original message of the build."""
        if self.original_message_id and self.original_channel_id:
            if self.original_server_id is None:
                raise NotImplementedError("This message is from DMs.")
            return f"https://discord.com/channels/{self.original_server_id}/{self.original_channel_id}/{self.original_message_id}"
        return None

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

            for restriction in await DatabaseManager.fetch_all_restrictions():
                for door_restriction in restrictions:
                    if door_restriction.lower() == restriction["name"].lower():
                        if restriction["type"] == "wiring-placement":
                            self.wiring_placement_restrictions.append(restriction["name"])
                        elif restriction["type"] == "component":
                            self.component_restrictions.append(restriction["name"])
                        elif restriction["type"] == "miscellaneous":
                            self.miscellaneous_restrictions.append(restriction["name"])

    async def update_messages(self, bot: discord.Client) -> None:
        """Updates all messages which for this build."""
        if self.id is None:
            raise ValueError("Build id is None.")

        # Get all messages for a build
        message_records = await msg.get_build_messages(self.id)
        em = await self.generate_embed()

        for record in message_records:
            message = await bot_utils.getch(bot, record)
            if message is None:
                continue
            await message.edit(content=self.original_link, embed=em)
            await msg.update_message_edited_time(message.id)

    async def get_original_message(self, bot: discord.Client) -> discord.Message | None:
        """Gets the original message of the build."""
        if self._original_message_obj:
            return self._original_message_obj

        if self.original_channel_id:
            assert self.original_message_id is not None
            return await bot_utils.getch_message(bot, self.original_channel_id, self.original_message_id)
        return None

    async def generate_embedding(self) -> list[float] | None:
        """
        Generates embedding for the build using OpenAI's API.

        Returns:
            The embedding generated by the API, or None if the API call failed for any reason (e.g. no API key).
        """
        try:
            client = AsyncOpenAI()
            response = await client.embeddings.create(input=str(self), model="text-embedding-3-small")
            return response.data[0].embedding
        except OpenAIError as e:
            logger.error(f"Failed to generate embedding for build {self.id}: {e}")
            return None

    async def get_channels_to_post_to(self: Build, bot: Bot) -> list[GuildMessageable]:
        """
        Gets the channels in which this build should be posted to.

        Args:
            bot: A bot instance to get the channels from.
        """

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

        channels: list[GuildMessageable] = []
        for guild in bot.guilds:
            channel_id = await get_server_setting(guild.id, target)
            if channel_id:
                channels.append(cast(GuildMessageable, bot.get_channel(channel_id)))

        return channels

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

    def update_local(self, **data: Any) -> None:
        """Updates the build locally with the given data. No validation is done on the data."""
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

    async def save(self) -> None:
        """
        Updates the build in the database with the given data.

        If the build does not exist in the database, it will be inserted instead.
        """
        self.edited_time = utcnow()

        # Regarding the commented out fields:
        # They are added later in the process.
        # - information may be modified if unknown restrictions or types are found
        # - original_message_id is a foreign key to the messages table
        build_data = {
            "submission_status": self.submission_status,
            "record_category": self.record_category,
            # "information": self.information,
            "edited_time": self.edited_time,
            "width": self.width,
            "height": self.height,
            "depth": self.depth,
            "completion_time": self.completion_time,
            "category": self.category,
            "submitter_id": self.submitter_id,
            # "original_message_id": self.original_message_id,
            "version_spec": self.version_spec,
            "ai_generated": self.ai_generated,
            "embedding": self.embedding,
        }

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

        message_insert_task = asyncio.create_task(self._update_messages_table())
        vx = vecs.create_client(os.environ["DB_CONNECTION"])
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._update_build_subcategory_table())
                tg.create_task(self._update_build_links_table())
                tg.create_task(self._update_build_creators_table())
                tg.create_task(self._update_build_versions_table())
                unknown_restrictions = tg.create_task(self._update_build_restrictions_table())
                unknown_types = tg.create_task(self._update_build_types_table())
                embedding_task = tg.create_task(self.generate_embedding())
            build_vecs = vx.get_or_create_collection(name="builds", dimension=1536)
            self.embedding = await embedding_task
            build_vecs.upsert(records=[(str(self.id), self.embedding, {})])

            if unknown_restrictions.result():
                self.information["unknown_restrictions"] = (
                    self.information.get("unknown_restrictions", {}) | unknown_restrictions.result()
                )
            if unknown_types.result():
                self.information["unknown_patterns"] = (
                    self.information.get("unknown_patterns", []) + unknown_types.result()
                )

            await message_insert_task
            await (
                db.table("builds")
                .update({"information": self.information, "original_message_id": self.original_message_id})
                .eq("id", self.id)
                .execute()
            )
        except:
            vx.disconnect()
            if delete_build_on_error:
                await db.table("builds").delete().eq("id", self.id).execute()
            raise

    async def _update_build_subcategory_table(self) -> None:
        """Updates the subcategory table with the given data."""
        db = DatabaseManager()
        if self.category == "Door":
            doors_data = {
                "build_id": self.id,
                "orientation": self.door_orientation_type,
                "door_width": self.door_width,
                "door_height": self.door_height,
                "door_depth": self.door_depth,
                "normal_closing_time": self.normal_closing_time,
                "normal_opening_time": self.normal_opening_time,
                "visible_closing_time": self.visible_closing_time,
                "visible_opening_time": self.visible_opening_time,
            }
            await db.table("doors").upsert(doors_data).execute()
        elif self.category == "Extender":
            raise NotImplementedError
        elif self.category == "Utility":
            raise NotImplementedError
        elif self.category == "Entrance":
            raise NotImplementedError
        else:
            raise ValueError("Build category must be set")

    async def _update_build_restrictions_table(self) -> UnknownRestrictions:
        """Updates the build_restrictions table with the given data"""
        db = DatabaseManager()
        build_restrictions: list[str] = (
            self.wiring_placement_restrictions + self.component_restrictions + self.miscellaneous_restrictions
        )
        build_restrictions = [restriction.title() for restriction in build_restrictions]
        response = cast(
            APIResponse[RestrictionRecord],
            await DatabaseManager().rpc("find_restriction_ids", {"search_terms": build_restrictions}).execute(),
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
        unknown_miscellaneous_restrictions = []
        for wiring_restriction in self.wiring_placement_restrictions:
            if wiring_restriction not in [
                restriction["name"] for restriction in response.data if restriction["type"] == "wiring-placement"
            ]:
                unknown_wiring_restrictions.append(wiring_restriction)
        for component_restriction in self.component_restrictions:
            if component_restriction not in [
                restriction["name"] for restriction in response.data if restriction["type"] == "component"
            ]:
                unknown_component_restrictions.append(component_restriction)
        for miscellaneous_restriction in self.miscellaneous_restrictions:
            if miscellaneous_restriction not in [
                restriction["name"] for restriction in response.data if restriction["type"] == "miscellaneous"
            ]:
                unknown_miscellaneous_restrictions.append(miscellaneous_restriction)
        if unknown_wiring_restrictions:
            unknown_restrictions["wiring_placement_restrictions"] = unknown_wiring_restrictions
        if unknown_component_restrictions:
            unknown_restrictions["component_restrictions"] = unknown_component_restrictions
        if unknown_miscellaneous_restrictions:
            unknown_restrictions["miscellaneous_restrictions"] = unknown_miscellaneous_restrictions

        return unknown_restrictions

    async def _update_build_types_table(self) -> list[str]:
        """Updates the build_types table with the given data.

        Returns:
            A list of unknown types.
        """
        db = DatabaseManager()
        if self.door_type:
            door_type = [type_.title() for type_ in self.door_type]
        else:
            door_type = ["Regular"]
        response: APIResponse[TypeRecord] = (
            await db.table("types").select("*").eq("build_category", self.category).in_("name", door_type).execute()
        )
        type_ids = [type_["id"] for type_ in response.data]
        build_types_data = list({"build_id": self.id, "type_id": type_id} for type_id in type_ids)
        if build_types_data:
            await db.table("build_types").upsert(build_types_data).execute()
        unknown_types: list[str] = []
        for door_type in self.door_type:
            if door_type not in [type_["name"] for type_ in response.data]:
                unknown_types.append(door_type)
        return unknown_types

    async def _update_build_links_table(self) -> None:
        """Updates the build_links table with the given data."""
        build_links_data = []
        if self.image_urls:
            build_links_data.extend(
                {"build_id": self.id, "url": link, "media_type": "image"} for link in self.image_urls
            )
        if self.video_urls:
            build_links_data.extend(
                {"build_id": self.id, "url": link, "media_type": "video"} for link in self.video_urls
            )
        if self.world_download_urls:
            build_links_data.extend(
                {"build_id": self.id, "url": link, "media_type": "world_download"} for link in self.world_download_urls
            )
        if build_links_data:
            await DatabaseManager().table("build_links").upsert(build_links_data).execute()

    async def _update_build_creators_table(self) -> None:
        """Updates the build_creators table with the given data."""
        db = DatabaseManager()
        creator_ids: list[int] = []
        for creator_ign in self.creators_ign:
            response = await db.table("users").select("id").eq("ign", creator_ign).maybe_single().execute()
            if response:
                creator_ids.append(response.data["id"])
            else:
                creator_id = await add_user(ign=creator_ign)
                creator_ids.append(creator_id)

        build_creators_data = [{"build_id": self.id, "user_id": user_id} for user_id in creator_ids]
        if build_creators_data:
            await DatabaseManager().table("build_creators").upsert(build_creators_data).execute()

    async def _update_build_versions_table(self) -> None:
        """Updates the build_versions table with the given data."""
        functional_versions = self.versions or DatabaseManager.get_newest_version(edition="Java")

        # TODO: raise an error if any versions are not found in the database
        db = DatabaseManager()
        response: SingleAPIResponse[list[QuantifiedVersionRecord]] = (
            await db.rpc("get_quantified_version_names", {}).in_("quantified_name", functional_versions).execute()
        )
        version_ids = [version["id"] for version in response.data]
        build_versions_data = list({"build_id": self.id, "version_id": version_id} for version_id in version_ids)
        if build_versions_data:
            await db.table("build_versions").upsert(build_versions_data).execute()

    async def _update_messages_table(self) -> None:
        """Updates the messages table with the given data."""
        if self.original_message_id is None:
            return

        await (
            DatabaseManager()
            .table("messages")
            .insert(
                {
                    "server_id": self.original_server_id,
                    "channel_id": self.original_channel_id,
                    "message_id": self.original_message_id,
                    "build_id": self.id,
                    "edited_time": utcnow(),
                    "purpose": "build_original_message",
                    "content": self.original_message,
                }
            )
            .execute()
        )

    async def generate_embed(self) -> discord.Embed:
        """Generates an embed for the build."""
        em = bot_utils.info_embed(title=self.get_title(), description=self.get_description())

        fields = self.get_metadata_fields()
        for key, val in fields.items():
            em.add_field(name=key, value=escape_markdown(val), inline=True)

        if self.image_urls:
            for url in self.image_urls:
                mimetype, _ = mimetypes.guess_type(url)
                if mimetype is not None and mimetype.startswith("image"):
                    em.set_image(url=url)
                    break
                else:
                    preview = await bot_utils.get_website_preview(url)
                    if isinstance(preview["image"], io.BytesIO):
                        raise RuntimeError("Got a BytesIO object instead of a URL.")
                    em.set_image(url=preview["image"])
        elif self.video_urls:
            for url in self.video_urls:
                preview = await bot_utils.get_website_preview(url)
                if image := preview["image"]:
                    if isinstance(image, str):
                        em.set_image(url=image)
                    else:  # isinstance(image, io.BytesIO)
                        preview_url = await upload_to_catbox(
                            filename="video_preview.png", file=image, mimetype="image/png"
                        )
                        self.image_urls.append(preview_url)
                        if self.id is not None:
                            background_tasks.add(
                                asyncio.create_task(
                                    DatabaseManager()
                                    .table("build_links")
                                    .insert({"build_id": self.id, "url": preview_url, "media_type": "image"})
                                    .execute()
                                )
                            )
                        em.set_image(url=preview_url)
                    break

        em.set_footer(text=f"Submission ID: {self.id} • Last Update {utcnow()}")
        return em

    def get_title(self) -> str:
        """Generates the official Redstone Squid defined title for the build."""
        title = ""

        if self.category != "Door":
            raise NotImplementedError("Only doors are supported for now.")

        if self.submission_status == Status.PENDING:
            title += "Pending: "
        elif self.submission_status == Status.DENIED:
            title += "Denied: "
        if self.ai_generated:
            title += "\N{ROBOT FACE}"
        if self.record_category:
            title += f"{self.record_category} "

        # Special casing misc restrictions shaped like "0.3s" and "524 Blocks"
        for restriction in self.information.get("unknown_restrictions", {}).get("miscellaneous_restrictions", []):
            if re.match(r"\d+\.\d+\s*s", restriction):
                title += f"{restriction} "
            elif re.match(r"\d+\s*[Bb]locks", restriction):
                title += f"{restriction} "

        # FIXME: This is included in the title for now to match people's expectations
        for restriction in self.component_restrictions:
            title += f"{restriction} "
        for restriction in self.information.get("unknown_restrictions", {}).get("component_restrictions", []):
            title += f"*{restriction}* "

        # Door dimensions
        if self.door_width and self.door_height and self.door_depth and self.door_depth > 1:
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

        for restriction in self.information.get("unknown_restrictions", {}).get("wiring_placement_restrictions", []):
            title += f"*{restriction}* "

        # Pattern
        for pattern in self.door_type:
            if pattern != "Regular":
                title += f"{pattern} "

        for pattern in self.information.get("unknown_patterns", []):
            title += f"*{pattern}* "

        # Door type
        if self.door_orientation_type is None:
            raise ValueError("Door orientation type information (i.e. Door/Trapdoor/Skydoor) is missing.")
        title += self.door_orientation_type

        return title

    def get_description(self) -> str | None:
        """Generates a description for the build, which includes component restrictions, version compatibility, and other information."""
        desc = []

        if self.component_restrictions and self.component_restrictions[0] != "None":
            desc.append(", ".join(self.component_restrictions))

        if DatabaseManager.get_newest_version(edition="Java") not in self.versions:
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

    def get_metadata_fields(self) -> dict[str, str]:
        """Returns a dictionary of metadata fields for the build.

        The fields are formatted as key-value pairs, where the key is the field name and the value is the field value. The values are not escaped."""
        fields = {"Dimensions": f"{self.width or '?'} x {self.height or '?'} x {self.depth or '?'}"}

        if self.width and self.height and self.depth:
            fields["Volume"] = str(self.width * self.height * self.depth)

        # The times are stored as game ticks, so they need to be divided by 20 to get seconds
        if self.normal_opening_time:
            fields["Opening Time"] = f"{self.normal_opening_time / 20}s"
        if self.normal_closing_time:
            fields["Closing Time"] = f"{self.normal_closing_time / 20}s"
        if self.visible_opening_time:
            fields["Visible Opening Time"] = f"{self.visible_opening_time / 20}s"
        if self.visible_closing_time:
            fields["Visible Closing Time"] = f"{self.visible_closing_time / 20}s"

        if self.creators_ign:
            fields["Creators"] = ", ".join(sorted(self.creators_ign))

        if self.completion_time:
            fields["Date Of Completion"] = str(self.completion_time)

        fields["Versions"] = self.version_spec or "Unknown"

        if ip := self.server_info.get("server_ip"):
            fields["Server"] = ip
            if coordinates := self.server_info.get("coordinates"):
                fields["Coordinates"] = coordinates
            if command := self.server_info.get("command_to_build"):
                fields["Command"] = command

        if self.world_download_urls:
            fields["World Download"] = ", ".join(self.world_download_urls)
        if self.video_urls:
            fields["Videos"] = ", ".join(self.video_urls)

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
    from dotenv import load_dotenv

    load_dotenv()
    await DatabaseManager.setup()
    build = await Build.from_id(43)
    if build:
        print(repr(build))
        # await build.save()
        # print(build.as_dict())


if __name__ == "__main__":
    asyncio.run(main())
