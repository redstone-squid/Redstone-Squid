"""PostgreSQL tag-definition repository."""

from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import override

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.builds.errors import BuildNotFoundError
from squid.builds.models import Build
from squid.edits.domain import Revision, RevisionConflictError
from squid.tags.application import TagDefinitionRepository
from squid.tags.application.history import AssignmentSnapshot
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValue, TagValueType
from squid.tags.models import TagAssignment, TagDefinition


class PostgresTagDefinitionRepository(TagDefinitionRepository):
    """Persist definitions and assignments in the caller's transaction."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    @override
    async def create_showcase(
        self,
        *,
        stable_key: str,
        display_name: str,
        normalized_name: str,
        value_type: TagValueType,
        query_name: str | None,
        created_by_account_id: int,
    ) -> TagDefinition:
        row = TagDefinition(
            stable_key=stable_key,
            display_name=display_name,
            normalized_name=normalized_name,
            query_name=query_name,
            authority=TagAuthority.USER,
            semantic_kind=TagSemanticKind.SHOWCASE,
            restriction_type=None,
            value_type=value_type,
            record_operator=None,
            canonical_unit=None,
            default_display_unit=None,
            numeric_step=None,
            render_template="{name}" if value_type is TagValueType.NONE else "{name}: {value}{unit}",
            default_display_order=0,
            moderation_status=TagModerationStatus.PENDING,
            created_by_account_id=created_by_account_id,
            archived_at=None,
        )
        session = self._session()
        session.add(row)
        await session.flush()
        return row

    @override
    async def pending(self) -> Sequence[TagDefinition]:
        session = self._session()
        rows = (
            await session.scalars(
                select(TagDefinition)
                .where(TagDefinition.moderation_status == TagModerationStatus.PENDING)
                .order_by(TagDefinition.created_at, TagDefinition.id)
            )
        ).all()
        return tuple(row for row in rows)

    @override
    async def get(self, tag_id: int) -> TagDefinition | None:
        session = self._session()
        row = await session.get(TagDefinition, tag_id)
        return None if row is None else row

    async def get_for_update(self, tag_id: int) -> TagDefinition | None:
        return await self._session().get(TagDefinition, tag_id, with_for_update=True)

    async def build_revision(self, build_id: int) -> int:
        revision = await self._session().scalar(select(Build.revision).where(Build.id == build_id))
        if revision is None:
            raise BuildNotFoundError(build_id)
        return revision

    async def advance_build_revision(self, build_id: int, expected: Revision) -> Revision:
        revision = await self._session().scalar(
            update(Build)
            .where(Build.id == build_id, Build.revision == expected.value)
            .values(revision=Build.revision + 1)
            .returning(Build.revision)
        )
        if revision is None:
            raise RevisionConflictError(expected, await self.build_revision(build_id))
        return Revision(expected.target, revision)

    @override
    async def approved(self) -> Sequence[TagDefinition]:
        session = self._session()
        rows = (
            await session.scalars(
                select(TagDefinition)
                .where(TagDefinition.moderation_status == TagModerationStatus.APPROVED)
                .order_by(TagDefinition.default_display_order, TagDefinition.display_name, TagDefinition.id)
            )
        ).all()
        return tuple(row for row in rows)

    @override
    async def set_status(self, tag_id: int, status: TagModerationStatus) -> TagDefinition | None:
        session = self._session()
        row = await session.get(TagDefinition, tag_id, with_for_update=True)
        if row is None:
            return None
        # Queue the public search refresh with the status change so neither can commit alone.
        row.moderation_status = status
        row.archived_at = Instant.now() if status is TagModerationStatus.ARCHIVED else None
        row.updated_at = Instant.now()
        await session.flush()
        await session.execute(
            text(
                """
                INSERT INTO search_projection_queue
                    (resource_kind, source_key, action, enqueued_at, available_at)
                VALUES ('metadata', :source_key, :action, now(), now())
                ON CONFLICT (resource_kind, source_key) DO UPDATE
                SET action = EXCLUDED.action, enqueued_at = EXCLUDED.enqueued_at,
                    available_at = EXCLUDED.available_at, attempts = 0,
                    claimed_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL
                """
            ),
            {
                "source_key": f"tag:{tag_id}",
                "action": "upsert" if status is TagModerationStatus.APPROVED else "delete",
            },
        )
        return row

    async def assignment(self, build_id: int, tag_id: int) -> AssignmentSnapshot | None:
        session = self._session()
        # Serialize with all build edits, including assignments absent at the time of the read.
        await session.scalar(select(Build.id).where(Build.id == build_id).with_for_update())
        row = await session.scalar(
            select(TagAssignment)
            .where(TagAssignment.build_id == build_id, TagAssignment.tag_id == tag_id)
            .with_for_update()
        )
        return None if row is None else AssignmentSnapshot.capture(row)

    async def replace_assignment(
        self,
        build_id: int,
        tag_id: int,
        expected: AssignmentSnapshot | None,
        replacement: AssignmentSnapshot | None,
        *,
        actor_account_id: int,
    ) -> bool:
        session = self._session()
        owned = await session.scalar(
            select(Build.id)
            .where(Build.id == build_id, Build.submitter_account_id == actor_account_id)
            .with_for_update()
        )
        if owned is None or await self.assignment(build_id, tag_id) != expected:
            return False
        row = await session.scalar(
            select(TagAssignment).where(TagAssignment.build_id == build_id, TagAssignment.tag_id == tag_id)
        )
        if replacement is None:
            if row is not None:
                await session.delete(row)
        else:
            if row is None:
                definition = await session.get(TagDefinition, tag_id)
                if definition is None:
                    return False
                row = TagAssignment(definition, replacement.value)
                row.build_id = build_id
                session.add(row)
            row.value_type = replacement.value_type
            row.value = replacement.value
            row.display_unit = replacement.display_unit
            row.display_order = replacement.display_order
            row.evidence = replacement.evidence
            row.provenance = replacement.provenance
            row.created_by_account_id = replacement.created_by_account_id
            row.created_at = replacement.created_at
            row.updated_at = Instant.now()
        await session.flush()
        return True

    @override
    async def assign_showcase(
        self,
        *,
        build_id: int,
        tag_id: int,
        value: TagValue,
        actor_account_id: int,
    ) -> bool:
        """Insert or replace a value on a build owned by the actor.

        Return False if the build is missing, belongs to someone else, or the tag
        is missing. The application service checks tag approval and showcase kind.
        """
        session = self._session()
        owned_build_id = await session.scalar(
            select(Build.id)
            .where(Build.id == build_id, Build.submitter_account_id == actor_account_id)
            .with_for_update()
        )
        if owned_build_id is None:
            return False
        definition = await session.get(TagDefinition, tag_id)
        if definition is None:
            return False
        value_type = definition.value_type
        numeric_value, text_value, boolean_value = _split_value(value_type, value)
        statement = insert(TagAssignment).values(
            build_id=build_id,
            tag_id=tag_id,
            value_type=definition.value_type,
            numeric_value=numeric_value,
            text_value=text_value,
            boolean_value=boolean_value,
            provenance="submitted",
            created_by_account_id=actor_account_id,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[TagAssignment.build_id, TagAssignment.tag_id],
                set_={
                    "numeric_value": statement.excluded.numeric_value,
                    "text_value": statement.excluded.text_value,
                    "boolean_value": statement.excluded.boolean_value,
                    # InstantUTC converts the Instant when binding this value to SQL.
                    "updated_at": Instant.now(),
                },
            )
        )
        return True


def _split_value(
    value_type: TagValueType,
    value: TagValue,
) -> tuple[Decimal | None, str | None, bool | None]:
    if value_type is TagValueType.NONE and value is None:
        return None, None, None
    if value_type is TagValueType.NUMERIC and isinstance(value, Decimal):
        return value, None, None
    if value_type is TagValueType.TEXT and isinstance(value, str):
        return None, value, None
    if value_type is TagValueType.BOOLEAN and isinstance(value, bool):
        return None, None, value
    msg = f"invalid {value_type.value} tag value"
    raise ValueError(msg)
