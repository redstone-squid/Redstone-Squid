"""Submitting and retrieving submissions to/from the database"""

import asyncio
import logging
import os
import re
import time
import typing
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import timedelta
from functools import cached_property
from importlib import resources
from types import TracebackType
from typing import Any, Callable, Final, Literal, Self, overload

import discord
import vecs
from openai import AsyncOpenAI, OpenAIError
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from squid.db import DatabaseManager
from squid.db.schema import (
    Build as SQLBuild,
)
from squid.db.schema import (
    BuildCategory,
    BuildCreator,
    BuildLink,
    BuildRecord,
    BuildRestriction,
    BuildType,
    BuildVersion,
    Door,
    DoorOrientationName,
    DoorRecord,
    Entrance,
    EntranceRecord,
    Extender,
    ExtenderRecord,
    Info,
    LinkRecord,
    Message,
    MessageRecord,
    RecordCategory,
    Restriction,
    RestrictionRecord,
    Status,
    Type,
    TypeRecord,
    UnknownRestrictions,
    User,
    UserRecord,
    Utility,
    UtilityRecord,
    VersionRecord,
)
from squid.db.utils import get_version_string, utcnow

logger = logging.getLogger(__name__)


all_build_columns = "*, versions(*), build_links(*), build_creators(*), users(*), types(*), restrictions(*), doors(*), extenders(*), utilities(*), entrances(*), messages!builds_original_message_id_fkey(*)"
"""All columns that needs to be joined in the build table to get all the information about a build."""


class JoinedBuildRecord(BuildRecord):
    """Represents a build record with all the columns joined."""

    versions: list[VersionRecord]
    build_links: list[LinkRecord]
    build_creators: list[dict[str, Any]]  # You want to use users instead. This is just a join table.
    users: list[UserRecord]
    types: list[TypeRecord]
    restrictions: list[RestrictionRecord]
    doors: DoorRecord | None
    extenders: ExtenderRecord | None
    utilities: UtilityRecord | None
    entrances: EntranceRecord | None
    messages: MessageRecord | None  # Not actually all the associated messages, just the original message


class FrozenField[T]:
    """A descriptor that makes an attribute immutable after it has been set."""

    __slots__ = ("_private_name",)

    def __init__(self, name: str) -> None:
        self._private_name = "__frozen_" + name

    @overload
    def __get__(self, instance: None, owner: type[object]) -> Self: ...

    @overload
    def __get__(self, instance: object, owner: type[object]) -> T: ...

    def __get__(self, instance: object | None, owner: type[object] | None = None) -> T | Self:
        if instance is None:
            return self
        value = getattr(instance, self._private_name)
        return value

    def __set__(self, instance: object, value: T) -> None:
        if hasattr(instance, self._private_name):
            msg = f"Attribute `{self._private_name[1:]}` is immutable!"
            raise TypeError(msg) from None

        setattr(instance, self._private_name, value)


# https://stackoverflow.com/questions/74714300/paramspec-for-a-pre-defined-function-without-using-generic-callablep
def signature_from[**P, T](_original: Callable[P, T]) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Copies the signature of a function to another function."""

    def _decorator(func: Callable[P, T]) -> Callable[P, T]:
        return func

    return _decorator


@signature_from(field)
def frozen_field(**kwargs: Any):
    """A field that is immutable after it has been set. See `dataclasses.field` for more information."""
    metadata = kwargs.pop("metadata", {}) | {"frozen": True}
    return field(**kwargs, metadata=metadata)


def freeze_fields[T](cls: type[T]) -> type[T]:
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

    Locking:
        A build can be locked to prevent concurrent modifications.
        This lock is a simple boolean in the database, but is implemented as a counter in the object to allow nested locks (reentrant locks).
    """

    id: int | None = None
    submission_status: Status | None = None
    category: BuildCategory | None = None
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

    extra_info: Info = field(default_factory=Info)
    creators_ign: list[str] = field(default_factory=list)

    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    world_download_urls: list[str] = field(default_factory=list)

    submitter_id: int | None = None
    # TODO: save the submitted time too
    completion_time: str | None = None
    edited_time: str | None = None

    original_server_id: Final[int | None] = frozen_field(default=None)
    original_channel_id: Final[int | None] = frozen_field(default=None)
    original_message_id: Final[int | None] = frozen_field(default=None)
    original_message_author_id: Final[int | None] = frozen_field(default=None)
    original_message: Final[str | None] = frozen_field(default=None)

    ai_generated: bool | None = None
    embedding: list[float] | None = field(default=None, repr=False)

    lock: "BuildLock" = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        self.lock = BuildLock(self.id)

    @staticmethod
    async def from_id(build_id: int) -> "Build | None":
        """Creates a new Build object from a database ID.

        Args:
            build_id: The ID of the build to retrieve.

        Returns:
            The Build object with the specified ID, or None if the build was not found.
        """
        db = DatabaseManager()

        async with db.async_session() as session:
            stmt = (
                select(SQLBuild)
                .where(SQLBuild.id == build_id)
            )
            result = await session.execute(stmt)
            sql_build = result.unique().scalar_one_or_none()

            if not sql_build:
                return None

            return Build.from_sql_build(sql_build)

    @staticmethod
    async def from_message_id(message_id: int) -> "Build | None":
        """
        Get the build by a message id.

        Args:
            message_id: The message id to get the build from.

        Returns:
            The Build object with the specified message id, or None if the build was not found.
        """
        db = DatabaseManager()

        async with db.async_session() as session:
            stmt = select(Message).where(Message.id == message_id)
            result = await session.execute(stmt)
            message = result.scalar_one_or_none()

            if message and message.build_id:
                return await Build.from_id(message.build_id)
            return None

    @staticmethod
    def from_dict(submission: dict) -> "Build":
        """Creates a new Build object from a dictionary. No validation is done on the data."""
        build = Build()
        for attr in build:
            if attr in submission:
                setattr(build, attr, submission[attr])

        return build

    @staticmethod
    def from_json(data: JoinedBuildRecord) -> "Build":
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
                assert "doors" in data and data["doors"] is not None
                door_orientation_type = data["doors"]["orientation"]
                door_width = data["doors"]["door_width"]
                door_height = data["doors"]["door_height"]
                door_depth = data["doors"]["door_depth"]
                normal_closing_time = data["doors"]["normal_closing_time"]
                normal_opening_time = data["doors"]["normal_opening_time"]
                visible_closing_time = data["doors"]["visible_closing_time"]
                visible_opening_time = data["doors"]["visible_opening_time"]
            case "Extender":
                raise NotImplementedError
            case "Utility":
                raise NotImplementedError
            case "Entrance":
                raise NotImplementedError
            case _:
                raise ValueError("Invalid category")

        # FIXME: This is hardcoded for now
        if types := data.get("types"):
            door_type = [type_["name"] for type_ in types]
        else:
            door_type = ["Regular"]

        restrictions: list[RestrictionRecord] = data.get("restrictions", [])
        wiring_placement_restrictions = [r["name"] for r in restrictions if r["type"] == "wiring-placement"]
        component_restrictions = [r["name"] for r in restrictions if r["type"] == "component"]
        miscellaneous_restrictions = [r["name"] for r in restrictions if r["type"] == "miscellaneous"]

        extra_info = data["extra_info"]

        creators = data.get("users", [])
        creators_ign = [creator["ign"] for creator in creators]

        version_spec = data["version_spec"]
        version_records: list[VersionRecord] = data.get("versions", [])
        versions = [get_version_string(v) for v in version_records]

        links: list[LinkRecord] = data.get("build_links", [])
        image_urls = [link["url"] for link in links if link["media_type"] == "image"]
        video_urls = [link["url"] for link in links if link["media_type"] == "video"]
        world_download_urls = [link["url"] for link in links if link["media_type"] == "world-download"]

        submitter_id = data["submitter_id"]
        completion_time = data["completion_time"]
        edited_time = data["edited_time"]

        message_record: MessageRecord | None = data["messages"]
        if message_record is None:
            original_server_id = original_channel_id = original_message_id = original_message_author_id = None
            original_message = None
        else:
            original_server_id = message_record["server_id"]
            original_channel_id = message_record["channel_id"]
            original_message_id = data["original_message_id"]
            original_message_author_id = message_record["author_id"]
            original_message = message_record["content"]

        ai_generated = data["ai_generated"]
        embedding = data["embedding"]

        return Build(
            id=id,
            submission_status=Status(submission_status),
            record_category=record_category,
            category=BuildCategory(category),
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
            extra_info=extra_info,
            creators_ign=creators_ign,
            image_urls=image_urls,
            video_urls=video_urls,
            world_download_urls=world_download_urls,
            submitter_id=submitter_id,
            completion_time=completion_time,
            edited_time=edited_time,
            original_server_id=original_server_id,
            original_channel_id=original_channel_id,
            original_message_id=original_message_id,
            original_message_author_id=original_message_author_id,
            original_message=original_message,
            ai_generated=ai_generated,
            embedding=embedding,
        )

    @staticmethod
    def from_sql_build(sql_build: SQLBuild) -> "Build":
        """Converts a SQLBuild to a Build object."""
        if not isinstance(sql_build, Door):
            raise ValueError("Can only handle doors right now.")
        door = sql_build
        return Build(
            id=door.id,
            submission_status=door.submission_status,  # type: ignore
            category=door.category,
            record_category=door.record_category,
            width=door.width,
            height=door.height,
            depth=door.depth,
            door_width=door.door_width,
            door_height=door.door_height,
            door_depth=door.door_depth,
            door_type=[type.name for type in door.types],
            door_orientation_type=door.orientation,
            wiring_placement_restrictions=[r.name for r in door.restrictions if r.type == "wiring-placement"],
            component_restrictions=[r.name for r in door.restrictions if r.type == "component"],
            miscellaneous_restrictions=[r.name for r in door.restrictions if r.type == "miscellaneous"],
            normal_closing_time=door.normal_closing_time,
            normal_opening_time=door.normal_opening_time,
            visible_closing_time=door.visible_closing_time,
            visible_opening_time=door.visible_opening_time,
            extra_info=door.extra_info,  # type: ignore
            creators_ign=[creator.ign for creator in door.creators],
            image_urls=[link.url for link in door.links if link.media_type == "image"],
            video_urls=[link.url for link in door.links if link.media_type == "video"],
            world_download_urls=[link.url for link in door.links if link.media_type == "world-download"],
            submitter_id=door.submitter_id,
            completion_time=door.completion_time,
            edited_time=door.edited_time.strftime("%Y-%m-%d %H:%M:%S") if door.edited_time else None,
            original_server_id=door.original_message.server_id if door.original_message else None,
            original_channel_id=door.original_message.channel_id if door.original_message else None,
            original_message_id=door.original_message_id,
            original_message_author_id=door.original_message.author_id if door.original_message else None,
            original_message=door.original_message.content if door.original_message else None,
            ai_generated=door.ai_generated,
            embedding=door.embedding,
        )

    @staticmethod
    def parse_time_string(time_string: str | None) -> int | None:
        """Parses a time string into an integer.

        Args:
            time_string: The time string to parse.

        Returns:
            The time in ticks.
        """
        # TODO: parse "ticks"
        if time_string is None:
            return None
        time_string = time_string.replace("s", "").replace("~", "").strip()
        try:
            return int(float(time_string) * 20)
        except ValueError:
            return None

    @staticmethod
    async def ai_generate_from_message(
        message: discord.Message, *, prompt_path: str = "prompt.txt", model: str = "gpt-4.1-nano"
    ) -> "Build | None":
        """Parses a build from a message using AI.

        Args:
            message: The discord message
            prompt_path: Relative path to the prompt file, defaults to "prompt.txt" in the squid.db package.
            model: The LLM model to use for the AI generation, defaults to "gpt-4.1-nano".
        """
        base_url = os.getenv("OPENAI_BASE_URL")
        if not base_url:
            logger.warning("No OpenAI base URL found, defaulting to https://api.openai.com/v1.")
            base_url = "https://api.openai.com/v1"

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("No OpenAI API key found, cannot generate build from message.")
            return None

        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
        )

        prompt = resources.files("squid.db").joinpath(prompt_path).read_text(encoding="utf-8")
        completion = await client.beta.chat.completions.parse(
            model=model,
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
            return None

        build = Build(
            original_server_id=message.guild.id if message.guild is not None else None,
            original_channel_id=message.channel.id,
            original_message_id=message.id,
            original_message_author_id=message.author.id,
            original_message=message.clean_content,
            ai_generated=True,
        )
        build.record_category = variables["record_category"]  # type: ignore
        build.extra_info["unknown_restrictions"] = UnknownRestrictions()

        validation_tasks: list[tuple[str, Awaitable[tuple[list[str], list[str]]]]] = []
        if variables["component_restriction"] is not None:
            validation_tasks.append(
                (
                    "component",
                    asyncio.create_task(
                        validate_restrictions(variables["component_restriction"].split(", "), "component")
                    ),
                )
            )
        if variables["wiring_placement_restrictions"] is not None:
            validation_tasks.append(
                (
                    "wiring",
                    asyncio.create_task(
                        validate_restrictions(
                            variables["wiring_placement_restrictions"].split(", "), "wiring-placement"
                        )
                    ),
                )
            )
        if variables["miscellaneous_restrictions"] is not None:
            validation_tasks.append(
                (
                    "misc",
                    asyncio.create_task(
                        validate_restrictions(variables["miscellaneous_restrictions"].split(", "), "miscellaneous")
                    ),
                )
            )
        if variables["piston_door_type"] is not None:
            validation_tasks.append(
                ("door_types", asyncio.create_task(validate_door_types(variables["piston_door_type"].split(", "))))
            )

        results = await asyncio.gather(*(task for _, task in validation_tasks))
        for i, (task_type, _) in enumerate(validation_tasks):
            if task_type == "component":
                comps = results[i]
                build.component_restrictions = comps[0]
                build.extra_info["unknown_restrictions"]["component_restrictions"] = comps[1]
            elif task_type == "wiring":
                wirings = results[i]
                build.wiring_placement_restrictions = wirings[0]
                build.extra_info["unknown_restrictions"]["wiring_placement_restrictions"] = wirings[1]
            elif task_type == "misc":
                miscs = results[i]
                build.miscellaneous_restrictions = miscs[0]
                build.extra_info["unknown_restrictions"]["miscellaneous_restrictions"] = miscs[1]
            elif task_type == "door_types":
                door_types = results[i]
                build.door_type = door_types[0]
                build.extra_info["unknown_patterns"] = door_types[1]

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
        build.normal_opening_time = Build.parse_time_string(variables["opening_time"])
        build.normal_closing_time = Build.parse_time_string(variables["closing_time"])
        build.creators_ign = variables["creators"].split(", ") if variables["creators"] else []
        build.version_spec = variables["version"] or await DatabaseManager().get_or_fetch_newest_version(edition="Java")
        build.versions = await DatabaseManager().find_versions_from_spec(build.version_spec)
        build.image_urls = variables["image"].split(", ") if variables["image"] else []
        if variables["author_note"] is not None:
            build.extra_info["user"] = variables["author_note"].replace("\\n", "\n")
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

            for restriction in await DatabaseManager().fetch_all_restrictions():
                for door_restriction in restrictions:
                    if door_restriction.lower() == restriction.name.lower():
                        if restriction.type == "wiring-placement":
                            self.wiring_placement_restrictions.append(restriction.name)
                        elif restriction.type == "component":
                            self.component_restrictions.append(restriction.name)
                        elif restriction.type == "miscellaneous":
                            self.miscellaneous_restrictions.append(restriction.name)

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
        for restriction in self.extra_info.get("unknown_restrictions", {}).get("miscellaneous_restrictions", []):
            if re.match(r"\d+\.\d+\s*s", restriction):
                title += f"{restriction} "
            elif re.match(r"\d+\s*[Bb]locks", restriction):
                title += f"{restriction} "

        # FIXME: This is included in the title for now to match people's expectations
        for restriction in self.component_restrictions:
            title += f"{restriction} "
        for restriction in self.extra_info.get("unknown_restrictions", {}).get("component_restrictions", []):
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

        for restriction in self.extra_info.get("unknown_restrictions", {}).get("wiring_placement_restrictions", []):
            title += f"*{restriction}* "

        # Pattern
        for pattern in self.door_type:
            if pattern != "Regular":
                title += f"{pattern} "

        for pattern in self.extra_info.get("unknown_patterns", []):
            title += f"*{pattern}* "

        # Door type
        if self.door_orientation_type is None:
            raise ValueError("Door orientation type information (i.e. Door/Trapdoor/Skydoor) is missing.")
        title += self.door_orientation_type

        return title

    async def get_persisted_copy(self) -> "Build":
        """Get a persisted copy of the build."""
        if self.id is None:
            raise ValueError("Build id is None, there is no persisted copy.")

        return await Build.from_id(self.id)  # type: ignore

    async def generate_embedding(self) -> list[float] | None:
        """
        Generates embedding for the build using OpenAI's API.

        Returns:
            The embedding generated by the API, or None if the API call failed for any reason (e.g. no API key).
        """
        # The EMBEDDING_ environmental variables are an override for the OPENAI_ ones.
        base_url = os.getenv("EMBEDDING_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        api_key = os.getenv("EMBEDDING_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        try:
            client = AsyncOpenAI(base_url=base_url, api_key=api_key)
            response = await client.embeddings.create(input=str(self), model=model)
            return response.data[0].embedding
        except OpenAIError as e:
            logger.debug(f"Failed to generate embedding for build {self.id}: {e}")
            return None

    def diff[T: Any](self, other: "Build", *, allow_different_id: bool = False) -> list[tuple[str, T, T]]:
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

    async def reload(self) -> None:
        """
        Overwrite the current build with the data from the database.

        Raises:
            ValueError: If the build was not found or build.id is not set.
        """
        if self.id is None:
            raise ValueError("Build ID is missing.")
        raise NotImplementedError  # TODO

    def update_local(self, **data: Any) -> None:
        """Updates the build locally with the given data. No validation is done on the data."""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def as_dict(self) -> dict[str, Any]:
        """Converts the build to a dictionary."""
        build: dict[str, Any] = {}
        for attr in self:
            build[attr] = getattr(self, attr)
        return build

    @staticmethod
    def get_attr_type(attribute: str) -> type:
        """Gets the type of the attribute in the Build class."""
        if attribute in Build.__annotations__:
            attr_type = typing.get_type_hints(Build)[attribute]
        else:
            try:
                cls_attr = getattr(Build, attribute)
                if isinstance(cls_attr, property):
                    attr_type = typing.get_type_hints(cls_attr.fget)["return"]
                else:
                    raise NotImplementedError("Not sure how to automatically get the type of this attribute.")
            except AttributeError:
                raise ValueError(f"Attribute {attribute} is not in the Build class.")
        return attr_type

    async def confirm(self) -> None:
        """Marks the build as confirmed.

        Raises:
            ValueError: If the build could not be confirmed.
        """
        if self.id is None:
            raise ValueError("Build ID is missing.")
        assert self.lock is not None

        async with self.lock(timeout=30):
            self.submission_status = Status.CONFIRMED
            db = DatabaseManager()
            async with db.async_session() as session:
                stmt = update(SQLBuild).where(SQLBuild.id == self.id).values(submission_status=Status.CONFIRMED)
                result = await session.execute(stmt)
                await session.commit()
                if result.rowcount != 1:
                    raise ValueError("Failed to confirm submission in the database.")

    async def deny(self) -> None:
        """Marks the build as denied.

        Raises:
            ValueError: If the build could not be denied.
        """
        if self.id is None:
            raise ValueError("Build ID is missing.")
        assert self.lock is not None

        async with self.lock(timeout=30):
            self.submission_status = Status.DENIED
            db = DatabaseManager()
            async with db.async_session() as session:
                stmt = update(SQLBuild).where(SQLBuild.id == self.id).values(submission_status=Status.DENIED)
                result = await session.execute(stmt)
                await session.commit()
                if result.rowcount != 1:
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
            # "extra_info": self.extra_info,
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
        if self.id is None:
            # Lock the build immediately on creation instead of calling self.lock.acquire_lock()
            # to avoid issues where another task modifies the build before it is locked
            build_data |= {"is_locked": True}
            async with db.async_session() as session:
                stmt = insert(SQLBuild).values(**build_data)
                result = await session.execute(stmt)
                await session.commit()
                assert result.inserted_primary_key is not None, "No primary key was inserted"
                self.id = result.inserted_primary_key[0]
            self.lock._lock_count = 1  # pyright: ignore[reportPrivateUsage]
            delete_build_on_error = True
        else:
            await self.lock.acquire(timeout=30)
            async with db.async_session() as session:
                stmt = update(SQLBuild).where(SQLBuild.id == self.id).values(**build_data)
                result = await session.execute(stmt)
                await session.commit()
                assert result.rowcount == 1
            delete_build_on_error = False

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
            build_vecs = vx.get_or_create_collection(
                name="builds", dimension=int(os.getenv("EMBEDDING_DIMENSION", "1536"))
            )
            self.embedding = await embedding_task
            if self.embedding is None:
                logger.debug("Failed to generate embedding for build id %s, skipping vector update", self.id)
            else:
                build_vecs.upsert(records=[(str(self.id), self.embedding, {})])

            if unknown_restrictions.result():
                self.extra_info["unknown_restrictions"] = (
                    self.extra_info.get("unknown_restrictions", {}) | unknown_restrictions.result()
                )
            if unknown_types.result():
                self.extra_info["unknown_patterns"] = (
                    self.extra_info.get("unknown_patterns", []) + unknown_types.result()
                )

            await message_insert_task
            async with db.async_session() as session:
                stmt = (
                    update(SQLBuild)
                    .where(SQLBuild.id == self.id)
                    .values(extra_info=self.extra_info, original_message_id=self.original_message_id)
                )
                await session.execute(stmt)
                await session.commit()
        except:
            if delete_build_on_error:
                logger.warning("Failed to save build %s, deleting it", repr(self))
                async with db.async_session() as session:
                    stmt = delete(SQLBuild).where(SQLBuild.id == self.id)
                    await session.execute(stmt)
                    await session.commit()
            else:
                logger.error("Failed to update build %s. This means the build is in an inconsistent state.", repr(self))
            raise
        finally:
            vx.disconnect()
            await self.lock.release()

    async def _update_build_subcategory_table(self) -> None:
        """Updates the subcategory table with the given data. This function assumes lock is acquired."""
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
            async with db.async_session() as session:
                stmt = pg_insert(Door).values(**doors_data)
                stmt = stmt.on_conflict_do_update(index_elements=["build_id"], set_=doors_data)
                await session.execute(stmt)
                await session.commit()
        elif self.category == "Extender":
            async with db.async_session() as session:
                stmt = pg_insert(Extender).values(build_id=self.id)
                stmt = stmt.on_conflict_do_nothing()
                await session.execute(stmt)
                await session.commit()
        elif self.category == "Utility":
            async with db.async_session() as session:
                stmt = pg_insert(Utility).values(build_id=self.id)
                stmt = stmt.on_conflict_do_nothing()
                await session.execute(stmt)
                await session.commit()
        elif self.category == "Entrance":
            async with db.async_session() as session:
                stmt = pg_insert(Entrance).values(build_id=self.id)
                stmt = stmt.on_conflict_do_nothing()
                await session.execute(stmt)
                await session.commit()
        else:
            raise ValueError("Build category must be set")

    async def _update_build_restrictions_table(self) -> UnknownRestrictions:
        """Updates the build_restrictions table with the given data. This function assumes lock is acquired."""
        db = DatabaseManager()
        build_restrictions: list[str] = (
            self.wiring_placement_restrictions + self.component_restrictions + self.miscellaneous_restrictions
        )
        build_restrictions = [restriction.title() for restriction in build_restrictions]

        # Get restriction IDs using SQLAlchemy
        async with db.async_session() as session:
            stmt = select(Restriction).where(Restriction.name.in_(build_restrictions))
            result = await session.execute(stmt)
            restrictions = result.scalars().all()

            restriction_ids = [restriction.id for restriction in restrictions]

            # Clear existing build restrictions for this build
            delete_stmt = delete(BuildRestriction).where(BuildRestriction.build_id == self.id)
            await session.execute(delete_stmt)

            # Insert new build restrictions
            if restriction_ids:
                build_restrictions_data = [
                    {"build_id": self.id, "restriction_id": restriction_id} for restriction_id in restriction_ids
                ]
                stmt = insert(BuildRestriction).values(build_restrictions_data)
                await session.execute(stmt)

            await session.commit()

        # Identify unknown restrictions
        unknown_restrictions: UnknownRestrictions = {}
        unknown_wiring_restrictions = []
        unknown_component_restrictions = []
        unknown_miscellaneous_restrictions = []

        for wiring_restriction in self.wiring_placement_restrictions:
            if wiring_restriction not in [
                restriction.name for restriction in restrictions if restriction.type == "wiring-placement"
            ]:
                unknown_wiring_restrictions.append(wiring_restriction)
        for component_restriction in self.component_restrictions:
            if component_restriction not in [
                restriction.name for restriction in restrictions if restriction.type == "component"
            ]:
                unknown_component_restrictions.append(component_restriction)
        for miscellaneous_restriction in self.miscellaneous_restrictions:
            if miscellaneous_restriction not in [
                restriction.name for restriction in restrictions if restriction.type == "miscellaneous"
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
        """Updates the build_types table with the given data. This function assumes lock is acquired.

        Returns:
            A list of unknown types.
        """
        db = DatabaseManager()
        if self.door_type:
            door_type = [type_.title() for type_ in self.door_type]
        else:
            door_type = ["Regular"]

        async with db.async_session() as session:
            stmt = select(Type).where(Type.build_category == self.category).where(Type.name.in_(door_type))
            result = await session.execute(stmt)
            types = result.scalars().all()
            type_ids = [type_.id for type_ in types]

            # Clear existing build types for this build
            delete_stmt = delete(BuildType).where(BuildType.build_id == self.id)
            await session.execute(delete_stmt)

            # Insert new build types
            if type_ids:
                build_types_data = [{"build_id": self.id, "type_id": type_id} for type_id in type_ids]
                stmt = insert(BuildType).values(build_types_data)
                await session.execute(stmt)

            await session.commit()

            unknown_types: list[str] = []
            for door_type_name in self.door_type:
                if door_type_name not in [type_.name for type_ in types]:
                    unknown_types.append(door_type_name)
            return unknown_types

    async def _update_build_links_table(self) -> None:
        """Updates the build_links table with the given data. This function assumes lock is acquired."""
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
            db = DatabaseManager()
            async with db.async_session() as session:
                # Clear existing build links for this build
                delete_stmt = delete(BuildLink).where(BuildLink.build_id == self.id)
                await session.execute(delete_stmt)

                # Insert new build links
                stmt = insert(BuildLink).values(build_links_data)
                await session.execute(stmt)
                await session.commit()

    async def _update_build_creators_table(self) -> None:
        """Updates the build_creators table with the given data. This function assumes lock is acquired."""
        db = DatabaseManager()

        # Look up existing users
        creator_ids: list[int | None] = []
        missing_creator_tasks: list[asyncio.Task[int]] = []
        missing_creator_indices: list[int] = []

        async with db.async_session() as session:
            for i, creator_ign in enumerate(self.creators_ign):
                stmt = select(User.id).where(User.ign == creator_ign)
                result = await session.execute(stmt)
                user_id = result.scalar_one_or_none()

                if user_id is not None:
                    creator_ids.append(user_id)
                else:
                    missing_creator_tasks.append(asyncio.create_task(db.user.add_user(ign=creator_ign)))
                    missing_creator_indices.append(len(creator_ids))
                    creator_ids.append(None)  # Placeholder

        # Add missing creators to the database
        missing_creator_ids = await asyncio.gather(*missing_creator_tasks)
        for idx, creator_id in zip(missing_creator_indices, missing_creator_ids):
            creator_ids[idx] = creator_id
        assert all(creator_id is not None for creator_id in creator_ids), "All creators must have an ID."

        build_creators_data = [{"build_id": self.id, "user_id": user_id} for user_id in creator_ids]
        if build_creators_data:
            async with db.async_session() as session:
                # Clear existing build creators for this build
                delete_stmt = delete(BuildCreator).where(BuildCreator.build_id == self.id)
                await session.execute(delete_stmt)

                # Insert new build creators
                stmt = insert(BuildCreator).values(build_creators_data)
                await session.execute(stmt)
                await session.commit()

    async def _update_build_versions_table(self) -> None:
        """Updates the build_versions table with the given data. This function assumes lock is acquired."""
        db = DatabaseManager()
        functional_versions = self.versions or [await db.get_or_fetch_newest_version(edition="Java")]

        # TODO: raise an error if any versions are not found in the database
        async with db.async_session() as session:
            stmt = select(func.get_quantified_version_ids(functional_versions))
            result = await session.execute(stmt)  # rows of id, quantified_name
            version_ids = [version_id for version_id, _ in result.all()]
            build_versions_data = list({"build_id": self.id, "version_id": version_id} for version_id in version_ids)
            if build_versions_data:
                stmt = pg_insert(BuildVersion).values(build_versions_data)
                await session.execute(stmt)
                await session.commit()

    async def _update_messages_table(self) -> None:
        """Updates the messages table with the given data. This function assumes lock is acquired."""
        if self.original_message_id is None:
            return

        db = DatabaseManager()
        async with db.async_session() as session:
            stmt = pg_insert(Message).values(
                id=self.original_message_id,
                server_id=self.original_server_id,
                channel_id=self.original_channel_id,
                build_id=self.id,
                purpose="build_original_message",
                content=self.original_message,
                author_id=self.original_message_author_id,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "server_id": stmt.excluded.server_id,
                    "channel_id": stmt.excluded.channel_id,
                    "build_id": stmt.excluded.build_id,
                    "purpose": stmt.excluded.purpose,
                    "content": stmt.excluded.content,
                    "author_id": stmt.excluded.author_id,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await session.execute(stmt)
            await session.commit()


class BuildLock:
    """A reentrant lock to prevent concurrent modifications to a build."""

    def __init__(self, build_id: int | None):
        """Initializes the lock

        Args:
            build_id: The ID of the build to lock. If None, this lock becomes a no-op.
            None is supported mainly so users of Build doesn't have to check if the build ID is None before creating a lock.
        """
        self.build_id = build_id
        # This assumes that when _lock_count is > 0, it is ALWAYS synced with the database is_locked value
        self._lock_count = 0

    def locked(self):
        """Whether the build is locked."""
        return self._lock_count > 0

    def __call__(self, *, blocking: bool = True, timeout: float = -1) -> "LockContextManager":
        return LockContextManager(self, blocking=blocking, timeout=timeout)

    async def _try_lock(self) -> bool:
        """Tries to acquire the lock."""
        assert self.build_id is not None
        db = DatabaseManager()
        async with db.async_session() as session:
            stmt = (
                update(SQLBuild)
                .where(SQLBuild.id == self.build_id)
                .where(SQLBuild.is_locked.is_(False))
                .values(is_locked=True)
            )
            result = await session.execute(stmt)
            await session.commit()
            if result.rowcount == 1:
                self._lock_count = 1
                return True
            return False

    async def acquire(self, *, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquires a lock on the build to prevent concurrent modifications.

        Args:
            blocking: Whether to block until the lock is acquired. If False, the function will return immediately if the lock cannot be acquired.
            timeout: The maximum time to wait for the lock. If -1, the function will wait indefinitely.
        """
        # No need to lock if the build is not in the database
        if self.build_id is None:
            return True

        if self._lock_count > 0:
            self._lock_count += 1
            return True

        if not blocking:
            if not await self._try_lock():
                return False
            return True

        # Exponential backoff for acquiring the lock
        sleep_time = 0.01
        max_sleep = 0.5
        if timeout >= 0:
            start_time = time.monotonic()
            while True:
                if await self._try_lock():
                    return True
                if time.monotonic() - start_time >= timeout:
                    return False
                await asyncio.sleep(sleep_time)
                sleep_time = min(sleep_time * 1.5, max_sleep)
        else:
            while not await self._try_lock():
                await asyncio.sleep(sleep_time)
                sleep_time = min(sleep_time * 1.5, max_sleep)
            return True

    async def release(self) -> None:
        """Releases the lock on the build.

        If the lock is acquired multiple times, it will only be released when the lock count reaches 0.
        """
        if self._lock_count <= 0:
            return
        self._lock_count -= 1

        if self.build_id is None:  # No need to release if the build is not in the database
            return

        if self._lock_count == 0:
            db = DatabaseManager()
            async with db.async_session() as session:
                stmt = update(SQLBuild).where(SQLBuild.id == self.build_id).values(is_locked=False)
                await session.execute(stmt)
                await session.commit()


class LockContextManager:
    """A context manager for BuildLock."""

    def __init__(self, lock: BuildLock, *, blocking: bool = True, timeout: float = -1):
        self.lock = lock
        self.blocking = blocking
        self.timeout = timeout

    async def __aenter__(self):
        if await self.lock.acquire(blocking=self.blocking, timeout=self.timeout):
            return self.lock
        raise asyncio.TimeoutError("Timed out waiting for lock")

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ):
        await self.lock.release()


async def clean_locks() -> None:
    """Cleans up locks that were not released properly."""
    db = DatabaseManager()
    async with db.async_session() as session:
        cutoff_time = discord.utils.utcnow() - timedelta(minutes=5)
        stmt = update(SQLBuild).where(SQLBuild.locked_at < cutoff_time).values(is_locked=False)
        await session.execute(stmt)
        await session.commit()


async def get_valid_restrictions(type: Literal["component", "wiring-placement", "miscellaneous"]) -> Sequence[str]:
    """Gets a list of valid restrictions for a given type. The restrictions are returned in the original case.

    Args:
        type: The type of restriction. Either "component", "wiring_placement" or "miscellaneous"

    Returns:
        A list of valid restrictions for the given type.
    """
    db = DatabaseManager()
    async with db.async_session() as session:
        stmt = select(Restriction.name).where(Restriction.type == type)
        result = await session.execute(stmt)
        return result.scalars().all()


async def get_valid_door_types() -> Sequence[str]:
    """Gets a list of valid door types. The door types are returned in the original case.

    Returns:
        A list of valid door types.
    """
    db = DatabaseManager()
    async with db.async_session() as session:
        stmt = select(Type.name).where(Type.build_category == "Door")
        result = await session.execute(stmt)
        return result.scalars().all()


async def validate_restrictions(
    restrictions: list[str], type: Literal["component", "wiring-placement", "miscellaneous"]
) -> tuple[list[str], list[str]]:
    """Validates a list of restrictions for a given type.

    Args:
        restrictions: The list of restrictions to validate
        type: The type of restriction. Either "component", "wiring_placement" or "miscellaneous"

    Returns:
        (valid_restrictions, invalid_restrictions)
    """
    all_valid_restrictions = [r.lower() for r in await get_valid_restrictions(type)]

    valid_restrictions = [r for r in restrictions if r.lower() in all_valid_restrictions]
    invalid_restrictions = [r for r in restrictions if r not in all_valid_restrictions]
    return valid_restrictions, invalid_restrictions


async def validate_door_types(door_types: list[str]) -> tuple[list[str], list[str]]:
    """Validates a list of door types.

    Args:
        door_types: The list of door types to validate

    Returns:
        (valid_door_types, invalid_door_types)
    """
    all_valid_door_types = [t.lower() for t in await get_valid_door_types()]

    valid_door_types = [t for t in door_types if t.lower() in all_valid_door_types]
    invalid_door_types = [t for t in door_types if t.lower() not in all_valid_door_types]
    return valid_door_types, invalid_door_types


async def get_builds_by_filter(*, filter: Mapping[str, Any] | None = None) -> list[Build]:
    """Fetches all builds from the database, optionally filtered by submission status.

    Args:
        filter: A dictionary containing filter criteria, only exact matches are supported.

            A filter is of the format {"column_name": value}, where column_name is the name of the column
            in the database and value is the value to filter by. In general, the attribute names of the Build class
            are the same, but in some cases they are different and the only way to know is to look at the database schema.
            Also, if the attribute you are trying to filter is not in the builds table, you will need to use a join table
            syntax.

            For example, to filter by submission status, use {"submission_status": 1}. To filter by door opening time,
            use {"doors(normal_opening_time)": 0.5}. where doors is a join table. The join is automatically done by
            the supabase client when you use the `select` method with the correct column name.

    Returns:
        A list of Build objects.
    """
    # TODO: This is not trtivial in SQLAlchemy, so we keep the supabase client to do this.
    db = DatabaseManager()
    query = db.table("builds").select(all_build_columns)

    if filter is not None:  # TODO: Support more complex filters (in_ being the most important)
        for column, value in filter.items():
            query = query.eq(column, value)

    response = await query.execute()
    if not response:
        return []
    else:
        return [Build.from_json(build_json) for build_json in response.data]


async def get_builds_by_id(build_ids: list[int]) -> list[Build | None]:
    """Fetches builds from the database with the given IDs."""
    if len(build_ids) == 0:
        return []

    db = DatabaseManager()
    async with db.async_session() as session:
        stmt = (
            select(SQLBuild)
            .options(
                selectinload(SQLBuild.build_creators).selectinload(BuildCreator.user),
                selectinload(SQLBuild.build_restrictions).selectinload(BuildRestriction.restriction),
                selectinload(SQLBuild.build_versions).selectinload(BuildVersion.version),
                selectinload(SQLBuild.build_types).selectinload(BuildType.type),
                selectinload(SQLBuild.links),
                selectinload(SQLBuild.messages),
            )
            .where(SQLBuild.id.in_(build_ids))
        )
        result = await session.execute(stmt)
        sql_builds = result.scalars().all()

        # Create result list with None placeholders
        builds: list[Build | None] = [None] * len(build_ids)

        # Fill in the found builds at their correct positions
        for sql_build in sql_builds:
            idx = build_ids.index(sql_build.id)
            builds[idx] = Build.from_sql_build(sql_build)
        return builds


async def get_unsent_builds(server_id: int) -> list[Build] | None:
    """Get all the builds that have not been posted on the server"""
    db = DatabaseManager()

    # Builds that have not been posted on the server
    response = await db.rpc("get_unsent_builds", {"server_id_input": server_id}).execute()
    server_unsent_builds = response.data
    return [Build.from_json(unsent_sub) for unsent_sub in server_unsent_builds]
