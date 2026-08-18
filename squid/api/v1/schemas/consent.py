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
    """Paragraphs separated by blank lines. Plain text, never markup: it is rendered into a
    Discord card, an HTML page and a terminal, and only one of those could parse anything else."""


class ConsentGrantRequest(BaseModel):
    """Which notice version the client actually displayed before asking."""

    model_config = ConfigDict(extra="forbid")

    version: str | None = None
    """Optional so an older client keeps working; supplied, it is checked against the published
    version so a stale cached notice cannot record consent to text nobody read."""
