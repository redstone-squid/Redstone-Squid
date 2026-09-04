"""Typed persistence fixtures for synchronized submission target tests."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.infrastructure.models import Account
from squid.builds.domain import BUILD_CLASS_BY_CATEGORY, Build, BuildCategory, DoorBuild, ExtenderBuild, Status
from squid.sponsors import PublicSponsor
from squid.submissions.domain import (
    GeneralSubmissionDetails,
    NormalizedSubmission,
    SchematicRightsPolicy,
    SubmissionCategory,
    SubmissionDimensions,
    SubmissionOrigin,
    SubmissionSchematicVisibility,
    SubmissionTaxonomy,
    VerifiedSubmissionArtifacts,
)
from squid.versions.infrastructure.models import Version


async def seed_account_and_version(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Persist one build owner and the exact Java version used by target fixtures."""
    async with session_factory.begin() as session:
        account = Account()
        session.add_all(
            [
                account,
                Version(
                    edition="Java",
                    major_version=1,
                    minor_version=21,
                    patch_number=0,
                    data_version=3953,
                ),
            ]
        )
        await session.flush()
        return account.id


def submission_build(
    category: BuildCategory,
    account_id: int,
    *,
    draft_id: UUID | None = None,
    sponsor: PublicSponsor | None = None,
) -> Build:
    """Construct a minimally persistable typed build for one category."""
    common: dict[str, Any] = {
        "submission_status": Status.PENDING,
        "submitter_account_id": account_id,
        "source_submission_draft_id": draft_id,
        "sponsor": sponsor,
        "display_name": "Workshop prototype" if draft_id is not None else None,
        "versions": ["Java 1.21.0"],
        "width": 3,
        "height": 4,
        "depth": 5,
    }
    if category is BuildCategory.DOOR:
        return DoorBuild(**common, door_width=2, door_height=3, orientation="Door")
    if category is BuildCategory.EXTENDER:
        return ExtenderBuild(**common, orientation="Upward", extension_length=3, extender_type="Regular")
    return BUILD_CLASS_BY_CATEGORY[category](**common)


def normalized_submission(account_id: int, draft_id: UUID, source_version: str) -> NormalizedSubmission:
    """Construct the canonical general-category payload used by target tests."""
    return NormalizedSubmission(
        source_draft_id=draft_id,
        owner_account_id=account_id,
        origin=SubmissionOrigin.WEB,
        schema_id="build_submission.v1",
        schema_revision=1,
        category=SubmissionCategory.OTHER,
        display_name="Version integrity test",
        description=None,
        creators=("Builder",),
        capture_dimensions=SubmissionDimensions(3, 4, 5),
        source_version=source_version,
        version_compatibility=None,
        taxonomy=SubmissionTaxonomy(),
        schematic_policy=SchematicRightsPolicy(
            visibility=SubmissionSchematicVisibility.REVIEWER_ONLY,
            license=None,
            rights_attested=False,
            include_inventories=False,
            include_free_text=False,
        ),
        completion=None,
        ai_generated=False,
        sponsor_attribution=False,
        artifacts=VerifiedSubmissionArtifacts(),
        details=GeneralSubmissionDetails(),
    )
