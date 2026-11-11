"""Strong revision preconditions shared by HTTP editors."""

import re

from squid.core.errors import ConflictError, DomainError
from squid.core.i18n import tr
from squid.edits.domain import EditTarget, Revision


class RevisionRequiredError(DomainError):
    default_message = tr(t"edits-revision-required = Send the ETag you read in the If-Match header.")


class InvalidRevisionError(ConflictError):
    default_message = tr(t"edits-invalid-revision = This revision does not match the item being edited.")


def revision_etag(version: Revision) -> str:
    return f'"{version.target.kind}-{version.target.key}-r{version.value}"'


def expected_revision(target: EditTarget, if_match: str | None) -> Revision:
    if if_match is None:
        raise RevisionRequiredError
    prefix = re.escape(f'"{target.kind}-{target.key}-r')
    match = re.fullmatch(prefix + r'(\d+)"', if_match.strip())
    if match is None:
        raise InvalidRevisionError
    return Revision(target, int(match[1]))
