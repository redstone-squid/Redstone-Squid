"""Submitting and retrieving submissions to/from the database"""

import asyncio
import logging
import os
import re
import time
import typing
import warnings
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from functools import cached_property
from importlib import resources
from types import TracebackType
from typing import Any, Final, Literal, Self, overload

import discord
from openai import AsyncOpenAI, OpenAIError
from sqlalchemy import update

from squid.db.schema import (
    Build as SQLBuild,
)
from squid.db.schema import (
    BuildCategory,
    BuildRecord,
    DoorOrientationLiteral,
    DoorRecord,
    EntranceRecord,
    ExtenderRecord,
    Info,
    LinkRecord,
    MessageRecord,
    RecordCategoryLiteral,
    RestrictionRecord,
    RestrictionTypeLiteral,
    Status,
    TypeRecord,
    UnknownRestrictions,
    UserRecord,
    UtilityRecord,
    VersionRecord,
)
from squid.utils import parse_time_string

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
        return getattr(instance, self._private_name)

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
        msg = f"{cls} is not a dataclass"
        raise TypeError(msg)

    params = cls.__dataclass_params__  # type: ignore
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
    record_category: RecordCategoryLiteral | None = None
    versions: list[str] = field(default_factory=list)
    version_spec: str | None = None

    width: int | None = None
    height: int | None = None
    depth: int | None = None

    door_width: int | None = None
    door_height: int | None = None
    door_depth: int | None = None

    door_type: list[str] = field(default_factory=list)
    door_orientation_type: DoorOrientationLiteral | None = None

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
    edited_time: datetime | None = None

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
        from squid.db import DatabaseManager

        warnings.warn("Build.from_id is deprecated; use BuildManager.get_by_id", DeprecationWarning, stacklevel=2)
        return await DatabaseManager().build.get_by_id(build_id)

    @staticmethod
    async def from_message_id(message_id: int) -> "Build | None":
        """
        Get the build by a message id.

        Args:
            message_id: The message id to get the build from.

        Returns:
            The Build object with the specified message id, or None if the build was not found.
        """
        from squid.db import DatabaseManager

        warnings.warn(
            "Build.from_message_id is deprecated; use BuildManager.get_by_message_id", DeprecationWarning, stacklevel=2
        )
        return await DatabaseManager().build.get_by_message_id(message_id)

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
        from squid.db import DatabaseManager

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
            logger.debug("Missing keys in AI output variables")
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
        build_tags = DatabaseManager().build_tags
        if variables["component_restriction"] is not None:
            validation_tasks.append(
                (
                    "component",
                    asyncio.create_task(
                        build_tags.validate_restrictions(variables["component_restriction"].split(", "), "component")
                    ),
                )
            )
        if variables["wiring_placement_restrictions"] is not None:
            validation_tasks.append(
                (
                    "wiring",
                    asyncio.create_task(
                        build_tags.validate_restrictions(
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
                        build_tags.validate_restrictions(
                            variables["miscellaneous_restrictions"].split(", "), "miscellaneous"
                        )
                    ),
                )
            )
        if variables["piston_door_type"] is not None:
            validation_tasks.append(
                (
                    "door_types",
                    asyncio.create_task(build_tags.validate_door_types(variables["piston_door_type"].split(", "))),
                )
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
        build.normal_opening_time = parse_time_string(variables["opening_time"])
        build.normal_closing_time = parse_time_string(variables["closing_time"])
        build.creators_ign = variables["creators"].split(", ") if variables["creators"] else []
        build.version_spec = variables["version"] or await DatabaseManager().get_or_fetch_newest_version(edition="Java")
        build.versions = await DatabaseManager().find_versions_from_spec(build.version_spec)
        build.image_urls = variables["image"].split(", ") if variables["image"] else []
        if variables["author_note"] is not None:
            build.extra_info["user"] = variables["author_note"].replace("\\n", "\n")
        return build

    @cached_property
    def original_link(self) -> str | None:
        """The link to the original message of the build."""
        if self.original_message_id and self.original_channel_id:
            if self.original_server_id is None:
                msg = "This message is from DMs."
                raise NotImplementedError(msg)
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

    @property
    def restrictions(
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

    @restrictions.setter
    async def restrictions(
        self,
        restrictions: dict[
            Literal["wiring_placement_restrictions", "component_restrictions", "miscellaneous_restrictions"],
            Sequence[str] | None,
        ],
    ) -> None:
        """Sets the restrictions of the build."""
        self.wiring_placement_restrictions = list(restrictions.get("wiring_placement_restrictions") or [])
        self.component_restrictions = list(restrictions.get("component_restrictions") or [])
        self.miscellaneous_restrictions = list(restrictions.get("miscellaneous_restrictions") or [])

    async def set_restrictions_auto(self, restrictions: Sequence[str]) -> None:
        """Sets the restrictions of the build automatically based on the given list of restriction names.

        This method would fetch the restrictions from the database and categorize them into the appropriate lists based on their type.
        """
        from squid.db import DatabaseManager

        self.wiring_placement_restrictions = []
        self.component_restrictions = []
        self.miscellaneous_restrictions = []

        db_restrictions = await DatabaseManager().build_tags.fetch_all_restrictions()
        name_to_row = {r.name.lower(): r for r in db_restrictions}
        bucket: dict[RestrictionTypeLiteral, list[str]] = {
            "wiring-placement": self.wiring_placement_restrictions,
            "component": self.component_restrictions,
            "miscellaneous": self.miscellaneous_restrictions,
        }

        for r in restrictions:  # O(M)
            row = name_to_row.get(r.lower())
            if row:
                if row.type is None:
                    msg = "The type is supposed to never be None, this is a bug in the database."
                    raise RuntimeError(msg)
                bucket[row.type].append(row.name)

    @property
    def title(self) -> str:
        """The official Redstone Squid defined title for the build."""
        title = ""

        if self.category != "Door":
            msg = "Only doors are supported for now."
            raise NotImplementedError(msg)

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
            if re.match(r"\d+\.\d+\s*s", restriction) or re.match(r"\d+\s*[Bb]locks", restriction):
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
            msg = "Door orientation type information (i.e. Door/Trapdoor/Skydoor) is missing."
            raise ValueError(msg)
        title += self.door_orientation_type

        return title

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
            logger.debug("Failed to generate embedding for build %s: %s", self.id, e)
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
            msg = "The IDs of the builds are different."
            raise ValueError(msg)

        differences: list[tuple[str, T, T]] = []
        # TODO: too much magic, try using __dataclass_fields__ or just listing the fields manually
        for attr in [a for a in dir(self) if not a.startswith("__") and not callable(getattr(self, a))]:
            if attr == "id":
                continue
            if getattr(self, attr) != getattr(other, attr):
                differences.append((attr, getattr(self, attr), getattr(other, attr)))

        return differences

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
                    msg = "Not sure how to automatically get the type of this attribute."
                    raise NotImplementedError(msg)
            except AttributeError:
                msg = f"Attribute {attribute} is not in the Build class."
                raise ValueError(msg)
        return attr_type

    async def confirm(self) -> None:
        """Marks the build as confirmed.

        Raises:
            ValueError: If the build could not be confirmed.
        """
        from squid.db import DatabaseManager

        warnings.warn("Build.confirm is deprecated; use BuildManager.confirm", DeprecationWarning, stacklevel=2)
        await DatabaseManager().build.confirm(self)

    async def deny(self) -> None:
        """Marks the build as denied.

        Raises:
            ValueError: If the build could not be denied.
        """
        from squid.db import DatabaseManager

        warnings.warn("Build.deny is deprecated; use BuildManager.deny", DeprecationWarning, stacklevel=2)
        await DatabaseManager().build.deny(self)

    async def save(self) -> None:
        """
        Updates the build in the database with the given data.

        If the build does not exist in the database, it will be inserted instead.
        """
        from squid.db import DatabaseManager

        warnings.warn("Build.save is deprecated; use BuildManager.save", DeprecationWarning, stacklevel=2)
        await DatabaseManager().build.save(self)


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
        from squid.db import DatabaseManager

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
            return await self._try_lock()

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
        from squid.db import DatabaseManager

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
        msg = "Timed out waiting for lock"
        raise TimeoutError(msg)

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ):
        await self.lock.release()


async def clean_locks() -> None:
    """Cleans up locks that were not released properly."""
    from squid.db import DatabaseManager

    db = DatabaseManager()
    async with db.async_session() as session:
        cutoff_time = discord.utils.utcnow() - timedelta(minutes=5)
        stmt = update(SQLBuild).where(SQLBuild.locked_at < cutoff_time).values(is_locked=False)
        await session.execute(stmt)
        await session.commit()
