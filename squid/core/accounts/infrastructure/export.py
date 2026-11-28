"""Read the caller's account download from one read-only PostgreSQL snapshot."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.infrastructure.attribution import attributed_credits
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build
from squid.core.exports import Download, ExportFormat, ExportTable, encode_export
from squid.core.persistence.export import export_snapshot
from squid.core.submissions.models.reviews import BuildSubmission


@dataclass(frozen=True)
class Section:
    name: str
    columns: dict[str, str]
    query: str | Select

    async def rows(self, session: AsyncSession, account_id: int) -> AsyncIterator[dict[str, object]]:
        result = await session.stream(
            text(self.query) if isinstance(self.query, str) else self.query,
            {"account": account_id},
            execution_options={"yield_per": 64},
        )
        try:
            async for row in result.mappings():
                values = {}
                for key, kind in self.columns.items():
                    value = row[key]
                    if value is not None and kind.rstrip("?") == "id":
                        value = str(value)
                    elif isinstance(value, datetime):
                        value = value.isoformat()
                    values[key] = value
                yield values
        finally:
            await result.close()


SECTIONS = (
    Section(
        "account",
        {
            "public_account_id": "id",
            "created_at": "timestamp",
            "discord_build_indexing_enabled": "boolean",
            "privacy_notice_version": "string?",
            "privacy_notice_acknowledged_at": "timestamp?",
        },
        "SELECT public_account_id, created_at, discord_build_indexing_enabled, privacy_notice_version, "
        "privacy_notice_acknowledged_at FROM accounts WHERE id = :account",
    ),
    Section(
        "profile",
        {
            "display_name": "string?",
            "bio": "string?",
            "pronouns": "string?",
            "links": "json",
            "hidden": "boolean",
            "avatar_identity_id": "id?",
        },
        "SELECT display_name, bio, pronouns, links, hidden, avatar_identity_id FROM account_profiles WHERE account_id = :account",
    ),
    Section(
        "identities",
        {
            "id": "id",
            "provider": "string",
            "subject": "string",
            "display_name": "string?",
            "verified_at": "timestamp?",
            "is_public": "boolean",
        },
        "SELECT id, provider, subject, display_name, verified_at, is_public FROM account_identities WHERE account_id = :account ORDER BY id",
    ),
    Section(
        "aliases",
        {"id": "id", "name": "string", "claimed_at": "timestamp?", "claim_method": "string?"},
        "SELECT id, name, claimed_at, claim_method FROM creator_aliases WHERE account_id = :account ORDER BY id",
    ),
    Section(
        "claims",
        {
            "id": "id",
            "alias": "string",
            "status": "string",
            "created_at": "timestamp",
            "resolved_at": "timestamp?",
            "approved_credit_ids": "json",
        },
        "SELECT c.id, a.name AS alias, c.status, c.created_at, c.resolved_at, c.approved_credit_ids "
        "FROM creator_alias_claims c JOIN creator_aliases a ON a.id = c.alias_id WHERE c.account_id = :account ORDER BY c.id",
    ),
    Section(
        "claim_selections",
        {"claim_id": "id", "credit_id": "id", "build_id": "id"},
        "SELECT c.id AS claim_id, item.key AS credit_id, item.value->>'build_id' AS build_id "
        "FROM creator_alias_claims c CROSS JOIN LATERAL jsonb_each(c.selected_credits) item "
        "WHERE c.account_id = :account ORDER BY c.id, item.key",
    ),
    Section(
        "submitted_builds",
        {
            "id": "id",
            "source_submission_draft_id": "id?",
            "category": "string",
            "display_name": "string?",
            "description": "string?",
            "submission_status": "integer",
            "width": "integer?",
            "height": "integer?",
            "depth": "integer?",
            "version_spec": "string?",
            "submission_time": "timestamp?",
        },
        "SELECT id, source_submission_draft_id, category, display_name, description, submission_status, width, height, depth, version_spec, submission_time "
        "FROM builds WHERE submitter_account_id = :account ORDER BY id",
    ),
    Section(
        "build_links",
        {"build_id": "id", "url": "string", "media_type": "string?"},
        "SELECT l.build_id, l.url, l.media_type FROM build_links l JOIN builds b ON b.id = l.build_id WHERE b.submitter_account_id = :account ORDER BY l.build_id,l.url",
    ),
    Section(
        "drafts",
        {
            "id": "id",
            "schema_id": "string",
            "schema_revision": "integer",
            "category": "string",
            "status": "string",
            "answers": "json",
            "created_at": "timestamp",
            "updated_at": "timestamp",
        },
        "SELECT id, schema_id, schema_revision, category, status, answers, created_at, updated_at FROM submission_drafts WHERE owner_account_id = :account ORDER BY id",
    ),
    Section(
        "draft_media",
        {"draft_id": "id", "upload_id": "id", "discarded": "boolean"},
        "SELECT m.draft_id, m.upload_id, m.discarded FROM media_draft_references m JOIN submission_drafts d ON d.id = m.draft_id WHERE d.owner_account_id = :account ORDER BY m.draft_id,m.upload_id",
    ),
    Section(
        "draft_schematics",
        {"draft_id": "id", "source_id": "id", "is_primary": "boolean", "discarded": "boolean"},
        "SELECT l.draft_id, l.source_id, l.is_primary, l.discarded FROM submission_draft_schematics l "
        "JOIN submission_drafts d ON d.id = l.draft_id WHERE d.owner_account_id = :account ORDER BY l.draft_id,l.source_id",
    ),
    Section(
        "schematics",
        {"id": "id", "filename": "string", "byte_size": "integer?", "sha256": "string?"},
        "SELECT id, filename, byte_size, sha256 FROM submission_schematic_sources WHERE owner_account_id = :account ORDER BY id",
    ),
    Section(
        "catalogue_preferences",
        {"scope": "string", "filters": "string", "sort": "string"},
        "SELECT scope, filters, sort FROM catalogue_preferences WHERE account_id = :account ORDER BY scope",
    ),
    Section(
        "notification_preferences",
        {"web_enabled": "boolean", "dm_enabled": "boolean", "dm_suspended_at": "timestamp?"},
        "SELECT web_enabled, dm_enabled, dm_suspended_at FROM notification_profiles WHERE account_id = :account",
    ),
    Section(
        "notification_subscriptions",
        {"id": "id", "kind": "string", "subject_id": "id?", "filter": "json?", "enabled": "boolean"},
        "SELECT id, kind, subject_id, filter, enabled FROM notification_subscriptions WHERE account_id = :account ORDER BY id",
    ),
)


async def submission_rows(session: AsyncSession, account_id: int) -> AsyncIterator[dict[str, object]]:
    rows = await session.stream_scalars(
        select(BuildSubmission)
        .where(BuildSubmission.submitter_account_id == account_id)
        .order_by(BuildSubmission.id)
        .execution_options(yield_per=64)
    )
    try:
        async for row in rows:
            yield {
                "id": str(row.id),
                "build_id": str(row.build_id),
                "submitted_at": str(row.submitted_at),
                "content": row.visible_content,
                "decision": row.decision,
                "feedback": row.feedback,
            }
    finally:
        await rows.close()


class AccountExports:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def download(self, account_id: int, file_format: ExportFormat) -> Download:
        async with export_snapshot(self.sessions) as session:
            credits = attributed_credits(viewer_account_id=account_id)
            credit_section = Section(
                "credits",
                {"build_id": "id", "display_name": "string?", "name": "string", "role": "string"},
                select(credits.c.build_id, Build.display_name, credits.c.name, credits.c.role)
                .join(Build, Build.id == credits.c.build_id)
                .where(credits.c.account_id == account_id, Build.submission_status == BuildStatus.APPROVED)
                .order_by(credits.c.build_id, credits.c.position),
            )
            tables = [
                ExportTable(section.name, section.columns, section.rows(session, account_id))
                for section in (*SECTIONS, credit_section)
            ]
            tables.append(
                ExportTable(
                    "submissions",
                    {
                        "id": "id",
                        "build_id": "id",
                        "submitted_at": "timestamp",
                        "content": "json",
                        "decision": "string",
                        "feedback": "string?",
                    },
                    submission_rows(session, account_id),
                )
            )
            return await encode_export(tables, file_format)
