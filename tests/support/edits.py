"""Helpers for fake edit runners."""

from squid.edits.domain import EditCommand, Revision


def bound_revisions(command: EditCommand) -> tuple[Revision, ...]:
    """Return the command's expected revisions, which bot editors always send as concrete values."""
    revisions = tuple(version for version in command.expected if isinstance(version, Revision))
    assert len(revisions) == len(command.expected), "fake runners do not resolve LatestRevision"
    return revisions
