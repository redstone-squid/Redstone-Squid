"""Typed checked-text contracts for durable media rows."""

import re
from typing import cast

from sqlalchemy import CheckConstraint, Table
from sqlalchemy.dialects.postgresql import dialect

from squid.media.application.jobs import MediaArtifactRole, MediaJobStatus
from squid.media.domain import MediaKind
from squid.media.infrastructure.models import MediaArtifactRecord, MediaNormalizationJobRecord, MediaUploadRecord
from squid.persistence.types import StrEnumText


def _checked_values(table: Table, constraint_name: str) -> set[str]:
    constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == constraint_name
    )
    return set(re.findall(r"'([^']+)'", str(constraint.sqltext)))


def test_media_enum_mappings_match_database_checks() -> None:
    uploads = cast(Table, MediaUploadRecord.__table__)
    artifacts = cast(Table, MediaArtifactRecord.__table__)
    jobs = cast(Table, MediaNormalizationJobRecord.__table__)

    assert _checked_values(uploads, "media_uploads_kind_check") == {kind.value for kind in MediaKind}
    assert _checked_values(artifacts, "media_artifacts_role_check") == {role.value for role in MediaArtifactRole}
    assert _checked_values(jobs, "media_normalization_jobs_status_check") == {
        status.value for status in MediaJobStatus
    }


def test_media_checked_text_columns_round_trip_as_domain_enums() -> None:
    kind_type = cast(StrEnumText[MediaKind], MediaUploadRecord.kind.type)
    role_type = cast(StrEnumText[MediaArtifactRole], MediaArtifactRecord.role.type)
    status_type = cast(StrEnumText[MediaJobStatus], MediaNormalizationJobRecord.status.type)
    postgres = dialect()

    assert kind_type.process_result_value("video", postgres) is MediaKind.VIDEO
    assert role_type.process_result_value("poster", postgres) is MediaArtifactRole.POSTER
    assert role_type.process_result_value("video_thumbnail", postgres) is MediaArtifactRole.VIDEO_THUMBNAIL
    assert status_type.process_result_value("claimed", postgres) is MediaJobStatus.CLAIMED
