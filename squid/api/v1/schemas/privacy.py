"""The published privacy notice, as served to any client that has to display it."""

from pydantic import BaseModel, ConfigDict


class PrivacyNoticeDetail(BaseModel):
    """The current notice, its version, and the locale it was rendered in."""

    model_config = ConfigDict(extra="forbid")

    version: str
    locale: str
    """Echoed so a client can tell it received a fallback rather than what it asked for."""

    title: str
    body: str
    """Plain text with blank lines between paragraphs, suitable for web, Discord, and terminal display."""
