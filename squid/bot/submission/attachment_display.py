"""Attachment issue text shared by build cards and the review queue."""

from squid.builds.domain.models import AttachmentFailureInfo


def attachment_issue_line(issue: AttachmentFailureInfo, *, resolution: str | None = None) -> str:
    """Show the filename and the failure or its recorded resolution on one line."""
    filename = " ".join(issue["filename"].split())
    detail = " ".join((issue["detail"] if resolution is None else resolution).split())
    return f"{filename} — {detail}"
