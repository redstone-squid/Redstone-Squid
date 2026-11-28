"""Optional strong revision preconditions shared by HTTP editors."""

import re

from litestar.datastructures import ResponseHeader

from squid.core.edits.domain import EditTarget, LatestRevision, Revision
from squid.core.errors import ConflictError, DomainError
from squid.core.i18n import tr

EDIT_ETAG = ResponseHeader(
    name="ETag",
    documentation_only=True,
    required=True,
    value='"target-key-r1"',
    description="Revision of the edit target. Send this value unchanged on writes that accept If-Match.",
)


class RevisionRequiredError(DomainError):
    default_message = tr(t"edits-revision-required = Send the ETag you read in the If-Match header.")


class InvalidRevisionError(ConflictError):
    default_message = tr(t"edits-invalid-revision = This revision does not match the item being edited.")


def revision_etag(version: Revision) -> str:
    return f'"{version.target.kind}-{version.target.key}-r{version.value}"'


def expected_revision(target: EditTarget, if_match: str | None) -> Revision | LatestRevision:
    """Missing headers apply to current state; supplied tags must match exactly."""
    if if_match is None:
        return LatestRevision(target)
    prefix = re.escape(f'"{target.kind}-{target.key}-r')
    match = re.fullmatch(prefix + r'(\d+)"', if_match.strip())
    if match is None:
        raise InvalidRevisionError
    return Revision(target, int(match[1]))
